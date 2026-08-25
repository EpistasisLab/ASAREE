"""Design generation — the "design experiments" half of ASAREE's vision
(project_plan/core_asaree_use_case.md §10), the part safe to build as a
genuine platform primitive: pure combinatorics over arbitrary factors, no
use-case-specific assumptions. The other half (orchestration — the
critic-gating loop, workspace staging) stays notebook-side, deliberately.

Two responsibilities, kept separate: computing the design (pure, no I/O) and
materializing it as ``FactorialCellResult`` rows (via the existing
``upsert_cell`` merge, so generating a design twice — e.g. after adding a
replicate level — only creates the new combinations; already-populated cells
are untouched, since the merge only ever sets ``factor_values`` and nothing
else).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_cell_result import FactorialCellResult
from asaree.services.factorial_cells import upsert_cell


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


def _slugify(value: Any) -> str:
    if isinstance(value, dict):
        for key in _DICT_SLUG_PRIORITY_KEYS:
            if key in value and value[key] not in (None, ""):
                return _slugify(value[key])
        digest = hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:8]
        return f"cfg-{digest}"
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "x"


def cell_label_for(combination: dict[str, Any], *, replicate: int = 1) -> str:
    """A deterministic, sorted label for one combination — stable regardless
    of the order factors were declared in, so the same combination always
    lands on the same cell. ``replicate`` is 1-indexed; replicate 1's label is
    left exactly as it always was (no suffix) so existing single-replicate
    experiments are unaffected and a later replicates increase only adds new
    cells (2, 3, ...) rather than renaming the original one."""
    base = "__".join(f"{name}_{_slugify(value)}" for name, value in sorted(combination.items()))
    return base if replicate <= 1 else f"{base}__rep{replicate}"


async def generate_design_cells(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    factors: list[dict[str, Any]],
    replicates: int = 1,
    randomization_seed: int | None = None,
) -> list[FactorialCellResult]:
    """Compute the design and materialize ``replicates`` cell rows per
    combination (default 1 — today's exact behavior, unchanged).

    Idempotent: re-running this (e.g. after widening a factor's levels or
    raising ``replicates``) only creates the new combinations/replicates —
    ``upsert_cell`` merges ``factor_values`` onto an existing row rather than
    resetting it, so a cell that already has results is left alone.

    ``randomization_seed``, when set, only shuffles the *order* of the
    returned list (assignment-order independence, standard factorial-design
    practice) — it never affects which combinations/replicates are generated
    or their (deterministic) cell_label.
    """
    if replicates < 1:
        raise DesignValidationError(f"replicates must be at least 1, got {replicates}")
    combinations = generate_design(factors)
    cells = []
    for combo in combinations:
        for replicate in range(1, replicates + 1):
            cell = await upsert_cell(
                db,
                experiment_id=experiment_id,
                cell_label=cell_label_for(combo, replicate=replicate),
                fields={"factor_values": combo},
            )
            cells.append(cell)
    if randomization_seed is not None:
        random.Random(randomization_seed).shuffle(cells)
    return cells
