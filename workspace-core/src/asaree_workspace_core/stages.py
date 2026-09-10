"""Stage plans — which staged steps a workspace has, and what each one must hold.

A *stage plan* is an ordered list of :class:`Stage` descriptors. It replaces the
three hardcoded constants that used to define the pipeline (``STAGE_VERSION``
here, ``_STAGES``/``SCRATCH_STAGES``/``FIXED_INPUT_STAGES`` in ASAREE's
workspace MCP server), so that "clean -> engineer -> select" is one *preset*
rather than the only shape a workspace can have. Anything else used to get an
empty workspace and ``unknown stage 'x'; expected one of ['dc','fte','fs']``.

The default is :data:`TABULAR_ML`, which is that same triple, byte for byte, and
is what a plan-less workspace or a plan-less experiment resolves to. It is
**immutable and not user-editable**: a published result depends on what its
stages meant, so a user who wants a variant copies it into an inline plan rather
than editing the preset under everyone else's feet.

Gate rules
----------
The old gate checks were not "stages are strings" -- they were real domain
invariants (DC leaves no missing values; FS may only ever narrow FTE's columns).
A naive generalization drops them silently, which is worse than not
generalizing, so gates are a **closed declarative schema**
(:data:`GATE_RULES`): each rule is a known key with a known value, validated
when the plan is resolved rather than when a run trips over it. There is no
expression language and no callback -- a plan is data, and it has to survive a
round trip through JSON on disk and through a design revision.

``gate: {}`` means no *structural* check beyond the two universal ones (matching
train/test feature columns, and the target present in both). That is the escape
hatch that makes non-tabular staged work possible: provenance, accept/reset and
the lineage rule all still apply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: The closed gate-rule schema: rule key -> the values that key accepts.
#:
#: * ``missing`` -- ``none``: no missing values in any feature column. This is
#:   DC's invariant; a stage whose whole job is imputation has not done it if a
#:   NaN survives.
#: * ``columns`` -- ``subset_of_input``: the stage's feature columns must be a
#:   subset of the columns it read. This is FS's invariant (selection narrows,
#:   it never invents), and generalizes the old hardcoded "subset of v2_fte"
#:   by pointing at whatever the plan says this stage's input is.
#:   ``non_increasing`` is the weaker count-only form, for a stage that renames
#:   as it narrows.
#: * ``rows`` -- ``preserved``: row counts equal the stage input's, on both
#:   partitions. Nothing in ``tabular_ml`` uses it (DC drops rows on purpose),
#:   but a stage that must not resample is a common enough shape to name.
GATE_RULES: dict[str, tuple[str, ...]] = {
    "missing": ("none",),
    "columns": ("subset_of_input", "non_increasing"),
    "rows": ("preserved",),
}

_STAGE_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_VERSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class StagePlanError(Exception):
    """Raised for a malformed stage plan or an unknown gate rule.

    Deliberately not a ``WorkspaceError``: a bad plan is a design-time mistake
    in an experiment, caught before anything is staged, whereas a
    ``WorkspaceError`` is about the state of a workspace on disk.
    """


@dataclass(frozen=True)
class Stage:
    """One staged step: what it is called, where it writes, and what must hold.

    * ``id`` is the token every tool call names (``accept_stage("dc")``) and the
      manifest file's stem, so it has to be a safe path component.
    * ``version_id`` is the workspace version this stage produces. It is
      separate from ``id`` because the ``v1_``/``v2_``/``v3_`` prefixes make a
      version list read in pipeline order on disk, which is worth keeping.
    * ``scratch`` -- this stage hands off through a disposable scratch directory
      rather than committing to the permanent tree mid-attempt. True for
      everything today; the flag stays because a stage that writes straight to a
      version is the older flow and the server still supports it.
    * ``fixed_input`` -- every tool in this stage re-reads the same unchanging
      input pair instead of chaining through a working copy, so the stage's
      tools are independent of each other's call order (FS's convention).
    """

    id: str
    label: str
    version_id: str
    scratch: bool = True
    fixed_input: bool = False
    gate: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _STAGE_ID.match(self.id):
            raise StagePlanError(
                f"stage id {self.id!r} must be lowercase letters, digits and underscores, "
                "starting with a letter -- it is used as a path component and a tool argument"
            )
        if not _VERSION_ID.match(self.version_id):
            raise StagePlanError(f"stage {self.id!r} has an unsafe version_id: {self.version_id!r}")
        if not str(self.label).strip():
            raise StagePlanError(f"stage {self.id!r} needs a label")
        for key, value in self.gate.items():
            if key not in GATE_RULES:
                raise StagePlanError(
                    f"stage {self.id!r} has unknown gate rule {key!r} -- "
                    f"known rules: {', '.join(sorted(GATE_RULES))}"
                )
            if value not in GATE_RULES[key]:
                raise StagePlanError(
                    f"stage {self.id!r} gate rule {key}={value!r} is not one of "
                    f"{', '.join(GATE_RULES[key])}"
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "version_id": self.version_id,
            "scratch": self.scratch,
            "fixed_input": self.fixed_input,
            "gate": dict(self.gate),
        }


@dataclass(frozen=True)
class StagePlan:
    """An ordered stage list, resolved for one experiment (and one workspace).

    Ordering is the pipeline: each stage reads the previous stage's *accepted*
    output, or the ``v0_raw`` seed for the first one. That rule is what makes
    this a pipeline rather than a set of independent steps, and it holds for a
    custom plan exactly as it does for the preset.
    """

    name: str
    stages: tuple[Stage, ...]

    def __post_init__(self) -> None:
        if not self.stages:
            raise StagePlanError("a stage plan needs at least one stage")
        ids = [s.id for s in self.stages]
        if len(set(ids)) != len(ids):
            raise StagePlanError(f"stage plan {self.name!r} repeats a stage id: {', '.join(sorted(ids))}")
        versions = [s.version_id for s in self.stages]
        if len(set(versions)) != len(versions):
            # Two stages writing one version would make "the accepted output of
            # the prior stage" ambiguous, and accepting one would silently
            # advance HEAD past the other.
            raise StagePlanError(f"stage plan {self.name!r} repeats a version_id: {', '.join(sorted(versions))}")

    @property
    def ids(self) -> list[str]:
        return [s.id for s in self.stages]

    @property
    def version_by_stage(self) -> dict[str, str]:
        return {s.id: s.version_id for s in self.stages}

    def has(self, stage_id: str) -> bool:
        return any(s.id == stage_id for s in self.stages)

    def stage(self, stage_id: str) -> Stage:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise StagePlanError(
            f"unknown stage {stage_id!r}; this workspace's plan ({self.name}) has: {', '.join(self.ids)}"
        )

    def index(self, stage_id: str) -> int:
        return self.ids.index(self.stage(stage_id).id)

    def previous(self, stage_id: str) -> Stage | None:
        """The stage whose accepted output *stage_id* reads, or None for the first."""
        idx = self.index(stage_id)
        return self.stages[idx - 1] if idx else None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "stages": [s.as_dict() for s in self.stages]}


#: The frozen ``dc``/``fte``/``fs`` triple, readable as an artifact.
#:
#: This is the published spinal pipeline's plan, so its stage ids, version ids,
#: order and gates are a compatibility surface, not a default to tidy up -- see
#: ``tests/test_spinal_compat.py::test_the_stage_lineage_is_unchanged``.
TABULAR_ML = StagePlan(
    name="tabular_ml",
    stages=(
        Stage(
            id="dc",
            label="Data cleaning",
            version_id="v1_dc",
            gate={"missing": "none"},
        ),
        Stage(
            id="fte",
            label="Feature transformation & engineering",
            version_id="v2_fte",
        ),
        Stage(
            id="fs",
            label="Feature selection",
            version_id="v3_fs",
            fixed_input=True,
            gate={"columns": "subset_of_input"},
        ),
    ),
)

#: Every built-in preset, by name. Presets are immutable: a variant is an
#: inline plan, so that a published experiment's stages can't be redefined
#: after the fact by editing something shared.
PRESETS: dict[str, StagePlan] = {TABULAR_ML.name: TABULAR_ML}

DEFAULT_STAGE_PLAN = TABULAR_ML


def stage_from_dict(raw: Any) -> Stage:
    """One stage descriptor from JSON, with a defaulted version id.

    ``version_id`` may be omitted, in which case it is derived from the stage's
    position and id (``v1_clean``) -- the same convention the preset follows, so
    a hand-written plan reads on disk like the built-in one does.
    """
    if not isinstance(raw, dict):
        raise StagePlanError(f"a stage must be an object, got {type(raw).__name__}")
    stage_id = str(raw.get("id") or "").strip()
    gate = raw.get("gate") or {}
    if not isinstance(gate, dict):
        raise StagePlanError(f"stage {stage_id!r}: gate must be an object of rule=value, or omitted")
    return Stage(
        id=stage_id,
        label=str(raw.get("label") or stage_id or "").strip(),
        version_id=str(raw.get("version_id") or "").strip() or f"v{int(raw.get('position') or 1)}_{stage_id}",
        scratch=bool(raw.get("scratch", True)),
        fixed_input=bool(raw.get("fixed_input", False)),
        gate={str(k): str(v) for k, v in gate.items()},
    )


def resolve_stage_plan(spec: Any) -> StagePlan:
    """The stage plan named or described by *spec*.

    Accepts, in order of how often it happens:

    * ``None`` / ``""`` / absent -- :data:`DEFAULT_STAGE_PLAN`. Every experiment
      and every workspace that predates stage plans lands here, which is why the
      preset has to be exactly the old constant.
    * a preset name (``"tabular_ml"``).
    * ``{"name": ..., "stages": [...]}`` -- an inline plan, the shape
      :meth:`StagePlan.as_dict` writes.
    * a bare list of stage descriptors -- the same thing without the wrapper,
      named ``"custom"``.
    """
    if spec is None or spec == "" or spec == {} or spec == []:
        return DEFAULT_STAGE_PLAN
    if isinstance(spec, StagePlan):
        return spec
    if isinstance(spec, str):
        preset = PRESETS.get(spec)
        if preset is None:
            raise StagePlanError(
                f"unknown stage plan preset {spec!r} -- known presets: {', '.join(sorted(PRESETS))}"
            )
        return preset
    if isinstance(spec, list):
        raw_stages, name = spec, "custom"
    elif isinstance(spec, dict):
        if isinstance(spec.get("preset"), str):
            return resolve_stage_plan(spec["preset"])
        raw_stages = spec.get("stages") or []
        name = str(spec.get("name") or "custom").strip() or "custom"
        if not isinstance(raw_stages, list):
            raise StagePlanError("a stage plan's 'stages' must be a list")
    else:
        raise StagePlanError(f"a stage plan must be a preset name, an object or a list, got {type(spec).__name__}")
    stages = tuple(
        stage_from_dict({"position": i + 1, **raw} if isinstance(raw, dict) else raw)
        for i, raw in enumerate(raw_stages)
    )
    return StagePlan(name=name, stages=stages)
