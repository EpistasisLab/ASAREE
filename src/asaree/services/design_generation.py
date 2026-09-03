"""Design generation — the "design experiments" half of ASAREE's vision
(project_plan/core_asaree_use_case.md §10), the part safe to build as a
genuine platform primitive: pure combinatorics over arbitrary factors, no
use-case-specific assumptions. The other half (orchestration — the
critic-gating loop, workspace staging) stays notebook-side, deliberately.

Two responsibilities, kept separate: computing the design (pure, no I/O) and
materializing it as ``FactorialCell`` parents with
``FactorialReplicateResult`` children under a *design revision*
(see services.design_revisions). Generation used to be purely additive, which
meant a design that shrank left the old design's cells behind to pollute every
count and every "run all cells"; a design whose cell set changes now opens a
new revision and carries forward the results of any combination the two share.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.services.design_revisions import get_current_revision, supersede_and_create
from asaree.services.factorial_cells import list_replicates, upsert_replicate


class DesignValidationError(ValueError):
    """A factor/level declaration that fails validation before anything is generated."""


def _validate_factors(factors: list[dict[str, Any]]) -> None:
    if not factors:
        raise DesignValidationError("factors must be a non-empty list")
    seen: set[str] = set()
    for f in factors:
        name = f.get("name")
        levels = f.get("levels")
        if not isinstance(name, str) or not name.strip():
            raise DesignValidationError(f"factor name must be a non-empty string, got {name!r}")
        if name in seen:
            raise DesignValidationError(f"duplicate factor name: {name!r}")
        seen.add(name)
        if not isinstance(levels, list) or not levels:
            raise DesignValidationError(f"factor {name!r} must have a non-empty list of levels")


def generate_design(factors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The cross product of every factor's levels: ``∏ᵢ Lᵢ`` combinations,
    each a dict of ``factor_name -> level_value``. Any factor names, any
    level types — no assumption about what a "factor" or "level" means for a
    particular use case."""
    _validate_factors(factors)
    names = [f["name"] for f in factors]
    levels_lists = [f["levels"] for f in factors]
    return [dict(zip(names, values, strict=True)) for values in itertools.product(*levels_lists)]


# Checked in order for a dict-valued level (e.g. a whole LLM/Tool/Dataset node
# config or a pattern-override payload bound as a single factor) -- whichever
# of these identifying keys is present first names the slug, since one of them
# is always the thing a human actually wants to see in a cell label
# ("claude-sonnet-5", "reason_act", "search-mcp", "spine-2024"). Falls back to
# a short stable hash when none match, rather than Python's own unstable dict
# repr.
#
# ``dataset_name`` must stay AHEAD of ``enabled``: a Dataset node's config is
# ``{dataset_id, dataset_name, enabled}``, so with ``enabled`` matching first
# every level of a dataset factor would slug to "true" and the whole design
# would collapse onto one cell label. ``dataset_id`` is deliberately absent --
# it's a uuid, unreadable in a label, and the name already identifies the row.
_DICT_SLUG_PRIORITY_KEYS = ("model", "provider", "execution_pattern", "server_name", "dataset_name", "enabled")

# How many items of a list-valued level (an MCP node's ``tool_names``
# allow-list, bound as a "Tools allowed" factor) name the slug before it's
# truncated. A cell label already concatenates every factor, so an unbounded
# join would let one 20-tool level produce a label no filesystem or table
# column wants; three names plus a count says which subset this is without
# growing without limit. Order is left as declared rather than sorted -- the
# level is stored verbatim in ``design_spec``, so its label is stable across
# regenerations either way, and sorting would only hide the (already
# meaningless) case of two levels holding the same set.
_MAX_LIST_SLUG_ITEMS = 3
_NON_FACTOR_KEYS = {"replicate", "seed", "rep", "trial", "iteration"}


def _slugify(value: Any) -> str:
    if isinstance(value, list | tuple):
        # "none", not the empty string's "x" fallback: an empty allow-list is
        # a real, deliberate level ("this server's tools withheld for this
        # cell"), and it's the one most worth reading off a cell label.
        if not value:
            return "none"
        slugs = [_slugify(v) for v in value]
        head = "-".join(slugs[:_MAX_LIST_SLUG_ITEMS])
        extra = len(slugs) - _MAX_LIST_SLUG_ITEMS
        return head if extra <= 0 else f"{head}-plus{extra}"
    if isinstance(value, dict):
        for key in _DICT_SLUG_PRIORITY_KEYS:
            if key in value and value[key] not in (None, ""):
                return _slugify(value[key])
        digest = hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:8]
        return f"cfg-{digest}"
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "x"


def cell_label_for(combination: dict[str, Any]) -> str:
    """Return the deterministic label for one unique factor combination."""
    return "__".join(f"{name}_{_slugify(value)}" for name, value in sorted(combination.items()))


def replicate_label_for(combination: dict[str, Any], *, replicate: int = 1) -> str:
    """Return the label for one replicate within a factor-combination cell.

    Replicate 1 retains the unsuffixed historical label; later replicates use
    ``__repN``. This keeps existing data stable while making the cell/replicate
    distinction explicit at every call site.
    """
    cell_label = cell_label_for(combination)
    return cell_label if replicate <= 1 else f"{cell_label}__rep{replicate}"


def _cell_key(factor_values: dict[str, Any] | None, label: str) -> str:
    factors = {key: value for key, value in (factor_values or {}).items() if key.lower() not in _NON_FACTOR_KEYS}
    return json.dumps(factors, sort_keys=True, default=str) if factors else re.sub(r"__rep\d+$", "", label)


_CARRIED_FORWARD_FIELDS = ("run_id", "workspace_id", "factor_values", "metric_values", "artifacts")


@dataclass(frozen=True)
class DesignImpact:
    """The non-mutating comparison shown before a user regenerates cells."""

    has_generated_design: bool
    regeneration_required: bool
    current_cell_count: int
    proposed_cell_count: int
    added_cell_count: int
    retained_cell_count: int
    removed_cell_count: int
    current_replicate_count: int
    proposed_replicate_count: int
    added_replicate_count: int
    retained_replicate_count: int
    removed_replicate_count: int


def material_design_spec(design_spec: dict[str, Any] | None) -> dict[str, Any]:
    """Return only the declaration fields that determine which cells exist."""
    spec = design_spec or {}
    factors = spec.get("factors") or []
    # A level label controls how a treatment is named in a downloaded design
    # matrix, not which treatment executes. Keep it out of revision/impact
    # comparisons so renaming a long system prompt never churns cell rows.
    material_factors = [
        {key: value for key, value in factor.items() if key != "level_labels"} if isinstance(factor, dict) else factor
        for factor in factors
    ]
    return {"factors": material_factors, "replicates": spec.get("replicates") or 1}


def _planned_replicates(factors: list[dict[str, Any]], replicates: int) -> list[tuple[str, dict[str, Any]]]:
    if replicates < 1:
        raise DesignValidationError(f"replicates must be at least 1, got {replicates}")
    return [
        (replicate_label_for(combo, replicate=replicate), combo)
        for combo in generate_design(factors)
        for replicate in range(1, replicates + 1)
    ]


async def get_design_impact(
    db: AsyncSession, *, experiment_id: uuid.UUID, design_spec: dict[str, Any] | None
) -> DesignImpact:
    """Compare the declared factorial matrix to its materialized revision."""
    material = material_design_spec(design_spec)
    planned = _planned_replicates(material["factors"], material["replicates"]) if material["factors"] else []
    planned_labels = {label for label, _ in planned}
    planned_cell_keys = {_cell_key(combo, label) for label, combo in planned}
    current = await get_current_revision(db, experiment_id)
    if current is None:
        return DesignImpact(
            has_generated_design=False,
            regeneration_required=bool(planned),
            current_cell_count=0,
            proposed_cell_count=len(planned_cell_keys),
            added_cell_count=len(planned_cell_keys),
            retained_cell_count=0,
            removed_cell_count=0,
            current_replicate_count=0,
            proposed_replicate_count=len(planned),
            added_replicate_count=len(planned),
            retained_replicate_count=0,
            removed_replicate_count=0,
        )
    current_replicates = await list_replicates(db, experiment_id=experiment_id, revision_id=current.id)
    current_labels = {replicate.replicate_label for replicate in current_replicates}
    current_cell_keys = {
        _cell_key(replicate.factor_values, replicate.replicate_label) for replicate in current_replicates
    }
    return DesignImpact(
        has_generated_design=True,
        regeneration_required=material_design_spec(current.design_spec) != material or current_labels != planned_labels,
        current_cell_count=len(current_cell_keys),
        proposed_cell_count=len(planned_cell_keys),
        added_cell_count=len(planned_cell_keys - current_cell_keys),
        retained_cell_count=len(planned_cell_keys & current_cell_keys),
        removed_cell_count=len(current_cell_keys - planned_cell_keys),
        current_replicate_count=len(current_replicates),
        proposed_replicate_count=len(planned),
        added_replicate_count=len(planned_labels - current_labels),
        retained_replicate_count=len(planned_labels & current_labels),
        removed_replicate_count=len(current_labels - planned_labels),
    )


async def generate_design_cells(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    factors: list[dict[str, Any]],
    replicates: int = 1,
    randomization_seed: int | None = None,
    design_spec: dict[str, Any] | None = None,
) -> list[FactorialReplicateResult]:
    """Compute the design and materialize ``replicates`` child rows per
    combination (default 1), under the experiment's current design revision.

    A new revision is opened exactly when the new design would *drop* a cell
    the current revision has — not on every change. Re-clicking generate,
    changing ``randomization_seed``, widening a factor's levels or raising
    ``replicates`` all leave every existing cell part of the new design, so
    they keep the current revision and merge into its rows: no near-empty
    duplicate revisions pile up, and (as before) the cells you already scored
    are untouched, right down to their row ids.

    Dropping a cell is the case that needs a revision (see
    services.design_revisions). This is the fix for cells outliving the design
    that created them: shrinking a design from 6 cells to 2 used to leave all 6
    in place — still counted in "0/6 scored", still picked up by "run all
    cells" — because generation was purely additive and nothing ever removed a
    combination. The old cells now stay under their own superseded revision,
    where they remain readable as history but are invisible to every
    current-design reader. Results for a label the two revisions share are
    carried forward into the new one, so shrinking a design only costs you the
    combinations you actually removed.

    ``randomization_seed``, when set, only shuffles the *order* of the
    returned list (assignment-order independence, standard factorial-design
    practice) — it never affects which combinations/replicates are generated
    or their (deterministic) cell_label.
    """
    # An empty factor declaration is a meaningful replacement when an
    # existing design's final factor was removed: it retires every current
    # cell into history and leaves an empty current revision.  Do not call
    # ``generate_design`` in that case -- its non-empty validation remains
    # correct for callers trying to construct a factorial cross-product.
    planned = _planned_replicates(factors, replicates) if factors else []
    planned_labels = {label for label, _ in planned}

    current = await get_current_revision(db, experiment_id)
    existing = (
        {
            replicate.replicate_label: replicate
            for replicate in await list_replicates(db, experiment_id=experiment_id, revision_id=current.id)
        }
        if current is not None
        else {}
    )

    # Only a design that leaves cells behind needs a new revision; one that
    # adds to (or exactly matches) the current design has nothing to orphan.
    dropped = set(existing) - planned_labels

    if current is not None and not dropped:
        # Keep the revision and merge into its rows -- factor_values is
        # re-merged because a level's *value* can change without changing its
        # slugified label.
        revision_id = current.id
        carry_over: dict[str, FactorialReplicateResult] = {}
        if current.design_spec != design_spec:
            current.design_spec = design_spec
            await db.flush()
    else:
        revision = await supersede_and_create(db, experiment_id=experiment_id, design_spec=design_spec)
        revision_id = revision.id
        carry_over = existing

    replicate_results = []
    for label, combo in planned:
        fields: dict[str, Any] = {"factor_values": combo}
        previous = carry_over.get(label)
        if previous is not None:
            # Same combination, same label -- the observation is as valid
            # under the new design as it was under the old one, so it moves
            # across rather than being re-run and re-billed.
            for field in _CARRIED_FORWARD_FIELDS:
                value = getattr(previous, field)
                if value:
                    fields[field] = value
            # factor_values from the new design wins: it's what was actually
            # planned this time, and the carried dict is the same combination
            # anyway.
            fields["factor_values"] = combo
        replicate_result = await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label=label,
            fields=fields,
            revision_id=revision_id,
        )
        replicate_results.append(replicate_result)
    if randomization_seed is not None:
        random.Random(randomization_seed).shuffle(replicate_results)
    return replicate_results
