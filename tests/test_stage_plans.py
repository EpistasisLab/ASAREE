"""User-declared workspace stages — the plan, and the compatibility it protects.

The staged pipeline used to *be* the three constants ``dc``/``fte``/``fs``, so
anything else got an empty workspace and ``unknown stage 'clean'``. It is now a
:class:`~asaree_workspace_core.stages.StagePlan`, with those three as the
``tabular_ml`` preset and the default.

Two things are worth more than the feature here, and most of this file is about
them: the preset must be that constant *exactly* (the published spinal
application note depends on it, and ``test_spinal_compat`` pins the lineage
itself), and a declared plan must not be able to reach back into a design
revision that has already produced results.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from asaree_workspace_core import (
    DEFAULT_STAGE_PLAN,
    SEED_VERSION,
    STAGE_VERSION,
    TABULAR_ML,
    Stage,
    StagePlan,
    StagePlanError,
    Workspace,
    WorkspaceError,
    resolve_stage_plan,
)

from asaree.services import protocol_execution as pe

_WORKSPACE_ID = "11111111-1111-1111-1111-111111111111/cell_1"

#: A three-stage pipeline that is nothing like tabular ML: no gates at all on
#: two of the stages, a row-preserving one, and ids/versions the old constants
#: never had. Used to prove the generalization is real rather than a rename.
_CUSTOM: list[dict[str, Any]] = [
    {"id": "ingest", "label": "Ingest", "version_id": "v1_ingest"},
    {"id": "annotate", "label": "Annotate", "version_id": "v2_annotate", "gate": {"rows": "preserved"}},
    {"id": "summarize", "label": "Summarize", "version_id": "v3_summarize", "fixed_input": True},
]


@pytest.fixture
def frames(tmp_path: Path) -> tuple[str, str]:
    rng = np.random.RandomState(0)
    frame = pd.DataFrame({"a": rng.normal(size=40), "b": rng.normal(size=40), "target": rng.randint(0, 2, 40)})
    train, test = tmp_path / "train.parquet", tmp_path / "test.parquet"
    frame.iloc[:20].to_parquet(train)
    frame.iloc[20:].reset_index(drop=True).to_parquet(test)
    return str(train), str(test)


def _open(tmp_path: Path, frames: tuple[str, str], **kwargs: Any) -> Workspace:
    return Workspace.open(
        _WORKSPACE_ID,
        target_column="target",
        seed_train_path=frames[0],
        seed_test_path=frames[1],
        root=str(tmp_path / "workspaces"),
        **kwargs,
    )


def _stage_through(ws: Workspace, stage: str, **frame_kwargs: Any) -> None:
    """Run one stage as a passthrough and accept it."""
    x_train, y_train, x_test, y_test = ws.read_stage_input(stage)
    ws.write_stage(
        stage,
        X_train=frame_kwargs.get("X_train", x_train),
        y_train=frame_kwargs.get("y_train", y_train),
        X_test=frame_kwargs.get("X_test", x_test),
        y_test=frame_kwargs.get("y_test", y_test),
        learned={"note": f"{stage} passthrough"},
        rationale=f"{stage} passthrough",
    )
    ws.accept_stage(stage)


# -- the preset IS the old constant --------------------------------------


def test_the_default_plan_is_the_old_three_constants() -> None:
    """Not "equivalent to" -- identical, including order and gates.

    ``test_spinal_compat.test_the_stage_lineage_is_unchanged`` walks the lineage
    through the new code path; this pins the plan those module constants are now
    a view of, so a well-meaning edit to the preset fails here by name.
    """
    assert DEFAULT_STAGE_PLAN is TABULAR_ML
    assert TABULAR_ML.ids == ["dc", "fte", "fs"]
    assert list(STAGE_VERSION.items()) == [("dc", "v1_dc"), ("fte", "v2_fte"), ("fs", "v3_fs")]
    assert TABULAR_ML.version_by_stage == STAGE_VERSION
    assert [s.gate for s in TABULAR_ML.stages] == [{"missing": "none"}, {}, {"columns": "subset_of_input"}]
    # fs re-reads one unchanging input pair; dc and fte chain through a working
    # copy. Getting this backwards would break the fs tools' call-order
    # independence without breaking any lineage assertion.
    assert [s.fixed_input for s in TABULAR_ML.stages] == [False, False, True]
    assert all(s.scratch for s in TABULAR_ML.stages)


def test_an_absent_plan_resolves_to_the_preset() -> None:
    for spec in (None, "", {}, [], "tabular_ml", {"preset": "tabular_ml"}):
        assert resolve_stage_plan(spec) is TABULAR_ML


def test_a_workspace_with_no_declared_plan_stays_on_format_1(tmp_path: Path, frames: tuple[str, str]) -> None:
    """The compatibility lever: the plan is recorded only when it is NOT the
    default, so a spinal-shaped workspace's state file is byte-shaped exactly as
    it was before plans existed."""
    ws = _open(tmp_path, frames)
    _stage_through(ws, "dc")
    raw = json.loads(ws.state_path.read_text())
    assert "stage_plan" not in raw
    assert "format_version" not in raw  # still the flat, pre-slot document
    assert ws.stage_plan is TABULAR_ML


def test_declaring_the_default_plan_explicitly_also_records_nothing(
    tmp_path: Path, frames: tuple[str, str]
) -> None:
    ws = _open(tmp_path, frames, stage_plan="tabular_ml")
    assert "stage_plan" not in json.loads(ws.state_path.read_text())
    assert ws.stage_plan is TABULAR_ML


# -- a declared plan really is the pipeline ------------------------------


def test_a_custom_plan_stages_end_to_end_and_records_provenance(
    tmp_path: Path, frames: tuple[str, str]
) -> None:
    """A plan with different ids, different versions and (mostly) no gates still
    gets the whole mechanism: ordered lineage, accept advancing HEAD, and a
    per-stage manifest."""
    ws = _open(tmp_path, frames, stage_plan=_CUSTOM)
    assert ws.stage_plan.ids == ["ingest", "annotate", "summarize"]
    assert ws.stage_plan.name == "custom"

    # Same lineage rule as the preset: a stage cannot read an input its
    # predecessor never accepted.
    with pytest.raises(WorkspaceError):
        ws.read_stage_input("annotate")

    for stage in ("ingest", "annotate", "summarize"):
        _stage_through(ws, stage)
        assert ws.has_accepted(stage)
        assert ws.load_state()["head"] == ws.stage_plan.stage(stage).version_id

    assert [v["id"] for v in ws.load_state()["versions"]] == [
        SEED_VERSION,
        "v1_ingest",
        "v2_annotate",
        "v3_summarize",
    ]
    manifest = json.loads((ws.manifests_dir / "annotate.json").read_text())
    assert manifest["input_version"] == "v1_ingest"
    assert manifest["output_version"] == "v2_annotate"
    assert manifest["learned"] == {"note": "annotate passthrough"}


def test_a_custom_plan_is_recorded_and_survives_reopening(tmp_path: Path, frames: tuple[str, str]) -> None:
    _open(tmp_path, frames, stage_plan=_CUSTOM)
    # Re-opened knowing only the workspace id -- which is all the MCP tool has.
    reopened = Workspace(_WORKSPACE_ID, root=str(tmp_path / "workspaces"))
    assert reopened.stage_plan.ids == ["ingest", "annotate", "summarize"]
    assert json.loads(reopened.state_path.read_text())["stage_plan"]["name"] == "custom"


def test_reopening_without_naming_a_plan_adopts_the_recorded_one(
    tmp_path: Path, frames: tuple[str, str]
) -> None:
    """``stage_plan=None`` means "whatever this cell already uses", not "the
    default" -- otherwise the MCP tool's own ``open_workspace()``, which knows a
    workspace id but not the experiment's design, would fight the plan ASAREE
    seeded the cell with."""
    _open(tmp_path, frames, stage_plan=_CUSTOM)
    adopted = _open(tmp_path, frames)
    assert adopted.stage_plan.ids == ["ingest", "annotate", "summarize"]


def test_reopening_with_a_different_plan_is_refused(tmp_path: Path, frames: tuple[str, str]) -> None:
    _open(tmp_path, frames, stage_plan=_CUSTOM)
    with pytest.raises(WorkspaceError) as excinfo:
        _open(tmp_path, frames, stage_plan="tabular_ml")
    assert "already staged through" in str(excinfo.value)


def test_an_unknown_stage_names_the_resolved_plans_stages(tmp_path: Path, frames: tuple[str, str]) -> None:
    """The error a researcher actually sees. It used to say "expected one of
    ['dc','fte','fs']" whatever the pipeline was."""
    ws = _open(tmp_path, frames, stage_plan=_CUSTOM)
    with pytest.raises(WorkspaceError) as excinfo:
        ws.read_stage_input("dc")
    message = str(excinfo.value)
    assert "unknown stage: 'dc'" in message
    assert "ingest, annotate, summarize" in message
    assert "custom" in message


# -- the gate schema is closed -------------------------------------------


def test_an_unknown_gate_rule_is_rejected_when_the_plan_resolves() -> None:
    """At plan-resolution time, not when a run trips over it: a typo'd rule that
    resolved to "no check" would look like a passing experiment."""
    with pytest.raises(StagePlanError) as excinfo:
        resolve_stage_plan([{"id": "clean", "label": "Clean", "gate": {"leakage": "none"}}])
    assert "unknown gate rule 'leakage'" in str(excinfo.value)
    assert "missing" in str(excinfo.value)  # names the rules that do exist


def test_a_known_rule_with_an_unknown_value_is_rejected() -> None:
    with pytest.raises(StagePlanError) as excinfo:
        resolve_stage_plan([{"id": "clean", "label": "Clean", "gate": {"missing": "few"}}])
    assert "missing='few'" in str(excinfo.value)


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ({"name": "empty", "stages": []}, "at least one stage"),
        ([{"id": "Clean Up", "label": "x"}], "must be lowercase"),
        ([{"id": "a", "label": "A"}, {"id": "a", "label": "A2"}], "repeats a stage id"),
        (
            [{"id": "a", "label": "A", "version_id": "v1"}, {"id": "b", "label": "B", "version_id": "v1"}],
            "repeats a version_id",
        ),
        ([{"id": "a", "label": "A", "version_id": "../escape"}], "unsafe version_id"),
        ("nope", "unknown stage plan preset"),
        (7, "must be a preset name"),
    ],
)
def test_a_malformed_plan_is_rejected(spec: Any, fragment: str) -> None:
    with pytest.raises(StagePlanError) as excinfo:
        resolve_stage_plan(spec)
    assert fragment in str(excinfo.value)


def test_a_blank_label_falls_back_to_the_stage_id() -> None:
    """A plan editor with the label field left empty is not an error -- the id is
    a usable caption. A ``Stage`` built in code still needs one."""
    assert resolve_stage_plan([{"id": "clean", "label": ""}]).stages[0].label == "clean"
    with pytest.raises(StagePlanError) as excinfo:
        Stage(id="clean", label="  ", version_id="v1_clean")
    assert "needs a label" in str(excinfo.value)


def test_a_version_id_defaults_from_position_and_id() -> None:
    plan = resolve_stage_plan([{"id": "clean", "label": "Clean"}, {"id": "select", "label": "Select"}])
    assert plan.version_by_stage == {"clean": "v1_clean", "select": "v2_select"}


def test_previous_is_the_stage_whose_output_this_one_reads() -> None:
    plan = resolve_stage_plan(_CUSTOM)
    assert plan.previous("ingest") is None
    assert plan.previous("annotate").id == "ingest"  # type: ignore[union-attr]
    assert plan.previous("summarize").id == "annotate"  # type: ignore[union-attr]


def test_a_stage_plan_round_trips_through_json() -> None:
    """A plan is stored in ``state.json`` and in a design revision, so it has to
    survive serialization unchanged -- an inline plan that resolved differently
    on the way back would silently redefine a cell's pipeline."""
    plan = resolve_stage_plan(_CUSTOM)
    assert resolve_stage_plan(json.loads(json.dumps(plan.as_dict()))).as_dict() == plan.as_dict()
    assert resolve_stage_plan(TABULAR_ML.as_dict()).as_dict() == TABULAR_ML.as_dict()


# -- the structural gate, driven by the plan -----------------------------


def _gate(ws: Workspace, stage: str, train: pd.DataFrame, test: pd.DataFrame) -> tuple[list[str], list[str]]:
    from asaree.mcp_servers import workspace_server as srv

    return srv._structural_checks(ws, stage, "target", train, test, ws.load_state())


def test_the_dc_gate_still_rejects_missing_values(tmp_path: Path, frames: tuple[str, str]) -> None:
    """``missing: none`` on the preset's first stage is the old hardcoded
    ``if stage == "dc"`` check, reached through the plan."""
    ws = _open(tmp_path, frames)
    train = pd.read_parquet(frames[0])
    test = pd.read_parquet(frames[1])
    checks, errors = _gate(ws, "dc", train, test)
    assert not errors
    assert "zero missing values after dc" in checks

    holed = train.copy()
    holed.loc[0, "a"] = np.nan
    _, errors = _gate(ws, "dc", holed, test)
    assert any("left missing values" in e for e in errors)


def test_the_fs_gate_still_rejects_columns_its_input_never_had(
    tmp_path: Path, frames: tuple[str, str]
) -> None:
    """``columns: subset_of_input`` generalizes "subset of v2_fte" by asking the
    plan what fs's input is -- which, for the preset, is v2_fte."""
    ws = _open(tmp_path, frames)
    _stage_through(ws, "dc")
    _stage_through(ws, "fte")
    train = pd.read_parquet(frames[0])
    test = pd.read_parquet(frames[1])

    narrowed_train = train[["a", "target"]]
    narrowed_test = test[["a", "target"]]
    checks, errors = _gate(ws, "fs", narrowed_train, narrowed_test)
    assert not errors
    assert any("subset of v2_fte" in c for c in checks)

    invented_train = train.assign(a_squared=train["a"] ** 2)
    invented_test = test.assign(a_squared=test["a"] ** 2)
    _, errors = _gate(ws, "fs", invented_train, invented_test)
    assert any("columns not in v2_fte" in e for e in errors)


def test_a_stage_with_no_gate_gets_only_the_universal_checks(
    tmp_path: Path, frames: tuple[str, str]
) -> None:
    """The escape hatch that makes non-tabular staged work possible. Missing
    values and a widened matrix are both fine here; a train/test column mismatch
    is not, because nothing downstream survives it."""
    ws = _open(tmp_path, frames, stage_plan=_CUSTOM)
    train = pd.read_parquet(frames[0])
    test = pd.read_parquet(frames[1])
    holed = train.copy()
    holed.loc[0, "a"] = np.nan
    widened = holed.assign(extra=1.0)

    checks, errors = _gate(ws, "ingest", widened, test.assign(extra=1.0))
    assert not errors
    assert checks == [
        "train/test column sets match (3 features)",
        "target 'target' present in both partitions",
    ]

    _, errors = _gate(ws, "ingest", widened, test)
    assert any("train/test column mismatch" in e for e in errors)


def test_the_rows_preserved_rule_compares_against_the_stage_input(
    tmp_path: Path, frames: tuple[str, str]
) -> None:
    ws = _open(tmp_path, frames, stage_plan=_CUSTOM)
    _stage_through(ws, "ingest")
    train = pd.read_parquet(frames[0])
    test = pd.read_parquet(frames[1])

    checks, errors = _gate(ws, "annotate", train, test)
    assert not errors
    assert any("preserved row counts" in c for c in checks)

    _, errors = _gate(ws, "annotate", train.iloc[:5], test)
    assert any("changed row counts" in e for e in errors)


def test_the_gate_reports_a_missing_input_rather_than_raising(
    tmp_path: Path, frames: tuple[str, str]
) -> None:
    """A promote must be able to *report* "there is nothing to compare against"
    -- raising out of the middle of accept_stage would leave the scratch dir in
    limbo."""
    ws = _open(tmp_path, frames)
    train = pd.read_parquet(frames[0])
    test = pd.read_parquet(frames[1])
    _, errors = _gate(ws, "fs", train, test)
    assert any("no v2_fte version to check against" in e for e in errors)


# -- the plan a run uses is the one its design pinned --------------------


def test_stage_plan_spec_reads_the_declaration_and_defaults_to_none() -> None:
    assert pe.stage_plan_spec(None) is None
    assert pe.stage_plan_spec({}) is None
    assert pe.stage_plan_spec({"stage_plan": ""}) is None
    assert pe.stage_plan_spec({"stage_plan": "tabular_ml"}) == "tabular_ml"
    assert pe.stage_plan_spec({"stage_plan": {"name": "x", "stages": _CUSTOM}}) == {"name": "x", "stages": _CUSTOM}


def _staged_graph(*stage_servers: str, enabled: bool = True) -> dict[str, Any]:
    """One agent per stage server, chained so the topological order is the
    argument order -- the shape a staged pipeline canvas actually has."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for i, server in enumerate(stage_servers):
        agent = f"a{i}"
        nodes.append({"id": agent, "type": "agent", "data": {"label": agent.upper(), "config": {}}})
        nodes.append(
            {
                "id": f"t{i}",
                "type": "mcp_tool",
                "data": {"config": {"enabled": enabled, "server_name": server}},
            }
        )
        edges.append({"source": f"t{i}", "target": agent, "sourceHandle": "tool", "targetHandle": "tool"})
        if i:
            edges.append({"source": f"a{i - 1}", "target": agent})
    return {"nodes": nodes, "edges": edges}


def test_a_spinal_shaped_canvas_derives_to_the_default_plan() -> None:
    """The backward-compatibility contract, stated as a no-op. A canvas wiring
    all three stage servers must derive to ``None``, not to an inline copy of
    ``tabular_ml``: ``None`` is the same value that canvas resolved before
    deriving existed, so nothing new lands in ``state.json``. See
    tests/test_spinal_compat.py."""
    assert pe.derive_stage_plan(_staged_graph("asaree-sklearn-dc", "asaree-sklearn-fte", "asaree-sklearn-fs")) is None


def test_a_canvas_with_no_stage_server_derives_to_nothing() -> None:
    """Every generic agent team. They never stage, so a derived plan would be a
    pipeline for artifacts that are never produced."""
    assert pe.derive_stage_plan({"nodes": [], "edges": []}) is None
    assert pe.derive_stage_plan(_staged_graph("scikit-learn-mcp")) is None


def test_a_partial_pipeline_derives_only_the_stages_it_wires() -> None:
    """The bug deriving fixes. A canvas wiring DC and FS but no FTE used to get
    the full triple regardless, so FS looked for a v2_fte version nothing had
    accepted and the run stalled on a lineage error with no visible connection
    to the wiring. Versions are renumbered by position so there is no gap."""
    plan = pe.derive_stage_plan(_staged_graph("asaree-sklearn-dc", "asaree-sklearn-fs"))
    assert plan is not None
    assert [(s["id"], s["version_id"]) for s in plan["stages"]] == [("dc", "v1_dc"), ("fs", "v2_fs")]
    # The preset's meaning for a stage it knows survives renumbering -- the same
    # server writes the stage either way, so its gate still applies.
    assert plan["stages"][0]["gate"] == {"missing": "none"}
    assert plan["stages"][1]["fixed_input"] is True
    resolve_stage_plan(plan)  # and the derived shape is one the core accepts


def test_a_disabled_tool_node_is_not_a_stage() -> None:
    """Disabling the node is how a user takes a stage out of the pipeline, so
    the derived plan has to agree with the tool grant that reads the same flag."""
    assert pe.derive_stage_plan(_staged_graph("asaree-sklearn-dc", enabled=False)) is None


def test_a_declared_plan_wins_over_the_canvas() -> None:
    """The SDK escape hatch. Deriving is how the GUI gets a plan, because there
    is no field for one; a notebook that names a plan outright is describing a
    pipeline the canvas could not express, so the canvas must not override it."""
    graph = _staged_graph("asaree-sklearn-dc", "asaree-sklearn-fs")
    assert pe.stage_plan_spec({"stage_plan": "tabular_ml"}, graph=graph) == "tabular_ml"
    assert pe.stage_plan_spec({}, graph=graph) == pe.derive_stage_plan(graph)
    # No graph at all (the notebook path) is still the default.
    assert pe.stage_plan_spec({}) is None


def test_a_malformed_plan_is_rejected_before_anything_runs() -> None:
    """Seeding failures are logged and swallowed inside a run, so a bad plan
    caught only there would look like a working experiment on the default
    pipeline."""
    with pytest.raises(pe.ProtocolValidationError) as excinfo:
        pe.validate_stage_plan({"stage_plan": [{"id": "clean", "label": "Clean", "gate": {"leakage": "none"}}]})
    assert "stage plan is not usable" in str(excinfo.value)
    pe.validate_stage_plan({"stage_plan": _CUSTOM})  # a usable one raises nothing
    pe.validate_stage_plan(None)


@pytest.mark.asyncio
async def test_a_plan_edit_does_not_reach_a_run_pinned_to_an_older_design(
    tmp_path: Path, frames: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retro-change guard. A replicate planned under one design must stage
    through that design's pipeline even if the user edits ``stage_plan``
    afterwards -- results already on disk were produced by the old stages, and
    ``Workspace.open`` would refuse the new ones anyway.
    """
    seen: list[Any] = []

    async def _seed(**kwargs: Any) -> Any:
        seen.append(kwargs.get("stage_plan"))
        raise AssertionError("stop here -- only the plan that was passed matters")

    monkeypatch.setattr(pe, "seed_cell_workspace", _seed)

    async def _reg(name: str, owner_id: uuid.UUID) -> dict[str, Any]:
        return {"train_path": frames[0], "test_path": frames[1], "target_column": "target"}

    monkeypatch.setattr(pe, "fetch_owned_registration", _reg)

    graph = {
        "nodes": [
            {"id": "a", "type": "agent", "data": {"label": "A", "config": {}}},
            {"id": "d", "type": "dataset", "data": {"config": {"dataset_name": "spinal"}}},
        ],
        "edges": [{"source": "d", "target": "a", "targetHandle": "dataset"}],
    }
    with pytest.raises(AssertionError):
        await pe._resolve_node_dataset(
            graph, "a", _WORKSPACE_ID, uuid.uuid4(), stage_plan=pe.stage_plan_spec({"stage_plan": _CUSTOM})
        )
    assert seen == [_CUSTOM]


@pytest.mark.asyncio
async def test_a_superseded_revisions_snapshot_keeps_the_plan_it_was_designed_with() -> None:
    """The durable half of the same guard, against real Postgres. Editing the
    live ``design_spec`` must not rewrite what an older revision declared --
    ``run_protocol`` resolves the plan from the run's pinned revision, so if the
    snapshot drifted, a queued replicate would stage through a pipeline its own
    design never had.
    """
    import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for the FK
    from asaree.models.database import dispose_engine, get_session
    from asaree.models.user import User
    from asaree.services.design_revisions import get_revision, supersede_and_create
    from asaree.services.experiments import create_experiment

    try:
        async with get_session() as db:
            user = User(
                email=f"stage-plan-test-{uuid.uuid4().hex}@example.com",
                hashed_password="not-a-real-hash",
                display_name="Stage Plan Test User",
            )
            db.add(user)
            await db.flush()
            await db.refresh(user)
            owner_id = user.id
            experiment = await create_experiment(db, name=f"stage-plan-{uuid.uuid4().hex}", owner_id=owner_id)
            experiment.design_spec = {"stage_plan": _CUSTOM}
            await db.flush()
            pinned = await supersede_and_create(
                db, experiment_id=experiment.id, design_spec=experiment.design_spec
            )
            pinned_id, experiment_id = pinned.id, experiment.id

        # The user edits the plan and regenerates: a new current revision, the
        # old one superseded.
        async with get_session() as db:
            experiment = await db.get(type(experiment), experiment_id)  # type: ignore[assignment]
            assert experiment is not None
            experiment.design_spec = {"stage_plan": "tabular_ml"}
            await db.flush()
            await supersede_and_create(db, experiment_id=experiment_id, design_spec=experiment.design_spec)

        async with get_session() as db:
            snapshot = await get_revision(db, pinned_id)
            assert snapshot is not None
            assert snapshot.superseded_at is not None
            assert pe.stage_plan_spec(snapshot.design_spec) == _CUSTOM
            live = await db.get(type(experiment), experiment_id)
            assert live is not None
            assert pe.stage_plan_spec(live.design_spec) == "tabular_ml"
            await db.delete(live)
            db_user = await db.get(User, owner_id)
            if db_user is not None:
                await db.delete(db_user)
    finally:
        await dispose_engine()


def test_a_stage_plan_is_a_frozen_value(tmp_path: Path, frames: tuple[str, str]) -> None:
    """Presets are immutable and not user-editable: a variant is a copy into an
    inline plan, so a published experiment's stages cannot be redefined out from
    under it."""
    with pytest.raises(Exception):  # noqa: B017 — dataclasses raise FrozenInstanceError
        TABULAR_ML.stages[0].label = "Something else"  # type: ignore[misc]
    with pytest.raises(Exception):  # noqa: B017
        TABULAR_ML.name = "mine"  # type: ignore[misc]
    # And a resolved copy is a distinct object, so editing one cannot leak.
    copy = resolve_stage_plan(TABULAR_ML.as_dict())
    assert copy is not TABULAR_ML
    assert copy.as_dict() == TABULAR_ML.as_dict()


def test_stage_plan_and_stage_construct_directly_too() -> None:
    """The dataclasses are part of the API, not just a JSON parser's output."""
    plan = StagePlan(
        name="two",
        stages=(Stage(id="a", label="A", version_id="v1_a"), Stage(id="b", label="B", version_id="v2_b")),
    )
    assert resolve_stage_plan(plan) is plan
    assert plan.index("b") == 1
