"""The backward-compatibility contract for the published spinal use case.

``tests/fixtures/spinal_graph.json`` is the real canvas of experiment
``27cb9ebc-7902-45b3-89f3-dd0c2b2516e7`` -- "Spinal Fusion Discharge --
Domain-General 2^3 Factorial Benchmark" -- exported verbatim from the dev
database. An application note describing that exact protocol has been submitted
for publication, so its behavior is a **published result**, not an
implementation detail: a reviewer re-running it must get what the paper says.

Every assertion here therefore pins something a *later* change would otherwise
alter silently. In particular ``test_the_assembled_prompt_matches_its_snapshot``
pins the assembled prompt text, because the prompt is the one input to a run
that no revision pins (``ProtocolRun`` already pins ``design_revision_id`` and
``protocol_revision_id``).

**The prompt golden is a snapshot, not a contract.** The published numbers came
from the prompt format as it stood at submission; that text survives in the
stored results and in git history, not at runtime, and a rerun today assembles
the current format. So the golden is expected to move as the handoff design is
built out -- it exists so those changes arrive as a reviewable diff instead of
silently. Updating it is legitimate. Updating it without reading it is not.

Everything else here -- the graph shape, the gate pairings, the dataset
resolution, the ``goal`` fallback, the seeded-dataset block -- is still a
contract, and a failure there is a regression, not a golden to refresh.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from asaree.services import protocol_execution as pe
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    find_gated_pairs,
    sink_node_ids,
    topological_order,
    validate_coordination_strategy,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "spinal_graph.json"

# One assembled prompt per stored contract, byte-for-byte. Kept as files
# rather than string literals only because the spinal agents' task_brief is
# ~1.6KB of JSON: inlining it twice would bury the assertions it sits between.
# A golden here is edited by hand or not at all -- there is deliberately no
# regeneration flag, because "the code changed so the golden changed" is the
# exact reasoning this file exists to refuse.
_GOLDEN_PROMPTS = Path(__file__).parent / "fixtures" / "prompts"

# The experiment's own declaration, as stored in ResearchExperiment.design_spec.
# Only the parts the executor reads are reproduced -- the strategy (which
# decides every validation path below) and the metrics (which reach the agent
# as evaluation criteria).
_SPINAL_DESIGN_SPEC: dict[str, Any] = {
    "coordination_strategy": {"slug": "critic_gate", "params": {}},
    "metrics": [
        {"name": "average_precision"},
        {"name": "roc_auc"},
        {"name": "f1"},
        {"name": "balanced_accuracy"},
        {"name": "accuracy"},
    ],
}

# Fixed so the pinned prompt strings below are reproducible. The real run uses
# the experiment's own id and a generated cell label; neither changes the
# prompt's *shape*, which is what is under test.
_EXPERIMENT_ID = uuid.UUID("27cb9ebc-7902-45b3-89f3-dd0c2b2516e7")
_CELL_LABEL = "c1r1"

# The five agents, in pipeline order, with the critic gate each one is paired
# with. Named explicitly rather than discovered, so a fixture that silently
# loses an agent fails here instead of quietly testing four.
#
# The main flow alternates agent -> gate -> agent, so an agent's own upstream is
# the PREVIOUS AGENT'S GATE, never that agent directly:
#   SF-DC -> Critic(DC) -> SF-FTE -> Critic(FTE) -> SF-FS -> Critic(FS)
#         -> SF-MLM -> Critic(MLM) -> SF-Score
# SF-Score is ungated -- it is the deliverable, and there is nothing after it to
# consume a review.
_AGENTS = [
    ("node-msza682j-w2vslmwn", "SF-DC", "node-msza682j-qvpuwtef"),
    ("node-msza682j-hiy7igv5", "SF-FTE", "node-msza682j-c6k6eyan"),
    ("node-msza682j-7qbpg0yc", "SF-FS", "node-msza682j-ffxyvn78"),
    ("node-msza682j-i5qx7sfa", "SF-MLM", "node-msza682j-gvf1u5gg"),
    ("node-msza682j-tqhfa0mx", "SF-Score", None),
]

# The registration's own name, which is NOT the Dataset node's canvas label
# ("Spinal Fusion Dataset"). ``_resolve_dataset_configs`` reads
# ``config.dataset_name``, and that string is what ``seed_cell_workspace`` looks
# up -- so a change that started passing the label instead would fail to find
# the registration at run time.
_DATASET_NAME = "spinal-fusion"


@pytest.fixture(scope="module")
def graph() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text())


# -- shape ---------------------------------------------------------------


def test_fixture_is_the_published_protocol(graph: dict[str, Any]) -> None:
    """A guard on the fixture itself: every assertion below is only meaningful
    if this is still the 27-node, 5-agent, 4-gate spinal canvas."""
    assert len(graph["nodes"]) == 27
    assert len(graph["edges"]) == 36
    by_type: dict[str, int] = {}
    for node in graph["nodes"]:
        by_type[str(node.get("type"))] = by_type.get(str(node.get("type")), 0) + 1
    assert by_type["agent"] == 5
    assert by_type["critic_gate"] == 4
    assert by_type["dataset"] == 1
    assert by_type["script"] == 1
    assert by_type["llm_azure_foundry"] == 1


# -- validation ----------------------------------------------------------


def test_the_canvas_validates_under_critic_gate(graph: dict[str, Any]) -> None:
    """The whole-guard check that both the publish endpoint and run_protocol
    call. Phase 1 tightens ``sequential`` only; if a change ever tightens
    ``critic_gate``'s topology, this is what says so."""
    validate_coordination_strategy(_SPINAL_DESIGN_SPEC, graph=graph)


def test_it_is_not_a_conversation(graph: dict[str, Any]) -> None:
    """``critic_gate`` runs as a pipeline, so it keeps every pipeline
    requirement -- the acyclic walk and the single sink both still apply."""
    assert pe.is_conversation_strategy(_SPINAL_DESIGN_SPEC) is False


def test_the_pipeline_order_covers_every_node(graph: dict[str, Any]) -> None:
    ordered = topological_order(graph)
    assert len(ordered) == 27
    ids = [n["id"] for n in ordered]
    # Agents keep their declared pipeline order: DC -> FTE -> FS -> MLM -> Score.
    agent_positions = [ids.index(node_id) for node_id, _label, _gate in _AGENTS]
    assert agent_positions == sorted(agent_positions)


def test_every_gated_pair_is_still_found(graph: dict[str, Any]) -> None:
    """Four of the five agents are gated; SF-Score is not. The gate mechanism is
    what the "Critic enabled" factor toggles, so losing a pair would silently
    turn one arm of the 2^3 design into the other."""
    pairs = find_gated_pairs(graph)
    expected = {node_id: gate for node_id, _label, gate in _AGENTS if gate}
    assert {k: v["id"] for k, v in pairs.items()} == expected


def test_there_is_exactly_one_sink(graph: dict[str, Any]) -> None:
    """``plan_cell_runs`` requires exactly one, and its output_text is the
    cell's result. SF-Score is the deliverable."""
    assert sink_node_ids(graph) == ["node-msza682j-tqhfa0mx"]


# -- ambient meta --------------------------------------------------------


def test_ambient_meta_key_set_is_stable(graph: dict[str, Any]) -> None:
    """``_ambient_meta_for``'s keys are a wire contract with Motoro, which lifts
    each one onto every MCP tool call under ``motoro.ambient.`` (see its
    ``mcp/adapters.py``). A renamed key doesn't fail loudly -- the tool just
    stops receiving it and asks the model for an argument instead, which is
    exactly the transcription error the ambient channel exists to remove.

    Called with ``workspace_id=None`` so this stays a pure function test: the
    workspace-derived keys (``data_path``/``target_column``) need a real
    directory on disk and are covered by the workspace tests instead.
    """
    dc_node_id = _AGENTS[0][0]
    meta = pe._ambient_meta_for(graph, dc_node_id, None)
    # SF-DC has the Dataset connector wired; the Script node is on SF-Score.
    assert meta == {"dataset_names": [_DATASET_NAME]}

    score_node_id = _AGENTS[4][0]
    score_meta = pe._ambient_meta_for(graph, score_node_id, None)
    # No workspace id, so the script can't be materialized to a path and the
    # code falls back into the prompt -- see _build_user_input's script branch.
    assert "dataset_names" not in score_meta


def test_the_dataset_reaches_the_three_staging_agents(graph: dict[str, Any]) -> None:
    """One registration, wired to DC/FTE/FS but NOT to MLM/Score.

    The two model-fitting agents deliberately ride on the ambient
    ``workspace_id`` instead (see ``_ambient_meta_for``'s ``data_path``
    comment) -- gating the bound path on a wired Dataset node would leave
    exactly those two with no path, which is why it is keyed off the workspace
    alone. A change that "tidied up" the wiring by giving every agent its own
    Dataset connector, or by giving only DC one, would break a different half
    of this each way.
    """
    for node_id, label, _gate in _AGENTS[:3]:
        configs = pe._resolve_dataset_configs(graph, node_id)
        assert [c["dataset_name"] for c in configs] == [_DATASET_NAME], label
    for node_id, label, _gate in _AGENTS[3:]:
        assert pe._resolve_dataset_configs(graph, node_id) == [], label


# -- prompt assembly (the golden values) ---------------------------------


def _prompt(graph: dict[str, Any], node_id: str, **kwargs: Any) -> str:
    node = next(n for n in graph["nodes"] if n["id"] == node_id)
    return pe._build_user_input(
        node,
        graph,
        kwargs.pop("node_runs", {}),
        experiment_id=_EXPERIMENT_ID,
        effective_cell_label=_CELL_LABEL,
        **kwargs,
    )


@pytest.mark.parametrize(("node_id", "label"), [(n, lbl) for n, lbl, _g in _AGENTS])
def test_the_agent_prompt_seed_falls_back_to_goal(graph: dict[str, Any], node_id: str, label: str) -> None:
    """**Every spinal agent has an empty ``config.prompt`` and relies on the
    ``goal`` fallback.**

    ``_build_user_input``'s seed is ``config.prompt or config.goal or label``.
    The docstring frames ``prompt`` as "the one field meant to change per run"
    and ``goal`` as a persistent objective "only used here as prompt's own
    fallback" -- which reads as though the fallback were a convenience. For
    this experiment it is the *only* route: a change that required ``prompt``,
    or that stopped falling through to ``goal``, would replace all five
    agents' instructions with their bare canvas labels and the pipeline would
    do nothing at all.
    """
    node = next(n for n in graph["nodes"] if n["id"] == node_id)
    config = node["data"].get("config") or {}
    assert config.get("prompt") == "", f"{label} now sets a prompt -- fixture changed?"
    goal = config.get("goal") or ""
    assert goal, f"{label} has neither prompt nor goal -- fixture changed?"
    assert _prompt(graph, node_id).startswith(goal)


def test_the_seeded_dataset_block_is_unchanged(graph: dict[str, Any]) -> None:
    """The exact wording an agent reads when ASAREE pre-opened its workspace.
    This text tells the model NOT to call ``open_workspace`` and not to
    fabricate path arguments; it is load-bearing prompt engineering, and the
    spinal DC agent runs down this branch on every cell."""
    # One seeded dataset, which is the spinal shape: the block below must stay
    # byte-identical to what it was before workspaces gained named slots. The
    # slot key is deliberately absent from it -- naming a slot only makes sense
    # once there are several, and this branch is the one the spinal DC agent
    # takes on every cell.
    text = _prompt(graph, _AGENTS[0][0], seeded_datasets=((_DATASET_NAME, "dataset:default"),))
    assert (
        "Dataset context:\n"
        f"Your data is already open: the dataset {_DATASET_NAME!r} is loaded into this "
        "cell's workspace at HEAD. Do NOT call open_workspace -- the workspace tools and "
        "the sklearn tools all resolve it from ambient run context, so omit any "
        "data_path/workspace_id/target_column argument and never build one out of an id "
        "another tool reported (a workspace id is not a file path). Start with the "
        "analysis itself. (workspace_status() reports the current state if you need it.)"
    ) in text


def test_an_upstream_agents_output_reaches_the_one_it_feeds(graph: dict[str, Any]) -> None:
    """The handoff itself, on the real graph.

    SF-FTE's prompt has to carry the DC gate's output without SF-FTE asking for
    it: the edge is the request. How that block is *framed* has moved (the raw
    ``[node-id]:`` prefix under an "Upstream context:" heading became a canvas
    label and a fence) and the golden below pins the current framing -- but the
    output arriving at all is not a formatting choice, and this pipeline does
    nothing if it stops.
    """
    dc_gate_id = _AGENTS[0][2]  # Critic (DC) -- FTE's actual upstream, not SF-DC
    fte_id = _AGENTS[1][0]
    node_runs = {dc_gate_id: {"status": "completed", "output_text": "DC accepted v1_dc."}}
    text = _prompt(graph, fte_id, node_runs=node_runs)
    assert "DC accepted v1_dc." in text
    assert pe._upstream_context(graph, fte_id, node_runs) in text


# The node the golden below is captured on, and the run state it sees. SF-FTE
# exercises every part of the assembly at once: a ``goal`` fallback seed, a real
# upstream block (its upstream is the DC gate), and a wired Dataset connector.
_GOLDEN_NODE_ID = _AGENTS[1][0]
_GOLDEN_NODE_RUNS = {_AGENTS[0][2]: {"status": "completed", "output_text": "DC accepted v1_dc."}}

def _assert_golden(graph: dict[str, Any]) -> None:
    golden = _GOLDEN_PROMPTS / "spinal_fte.txt"
    # Exactly what ``run_protocol``'s walk passes, so the golden is what a real
    # run gives SF-FTE rather than what a bare builder call produces. The walk
    # used to also pass an audience sentence and an ``upstream_kind``, both
    # derived from the topology; both are withdrawn, so there is nothing left
    # here for the walk to add.
    actual = _prompt(graph, _GOLDEN_NODE_ID, node_runs=_GOLDEN_NODE_RUNS)
    assert actual == golden.read_text()


def test_the_assembled_prompt_matches_its_snapshot(graph: dict[str, Any]) -> None:
    """A change-detector on the whole assembled prompt for a real node.

    The prompt is the one input to a run that no revision pins, so an
    obviously-good improvement to ``_build_user_input`` would otherwise move
    every future run's text with nothing failing to stop it. This pins it so an
    intended change arrives as a visible diff in review rather than as a silent
    behavior shift nobody looked at.

    Updating it is fine. Updating it without reading it is not.
    """
    _assert_golden(graph)


def test_an_upstream_node_with_no_output_contributes_nothing(graph: dict[str, Any]) -> None:
    """A skipped or output-less upstream must not leave an empty ``[id]:``
    stub -- the "Critic enabled: False" arm of the design relies on this."""
    dc_gate_id = _AGENTS[0][2]
    fte_id = _AGENTS[1][0]
    text = _prompt(graph, fte_id, node_runs={dc_gate_id: {"status": "completed", "output_text": None}})
    assert dc_gate_id not in text
    assert pe._upstream_context(graph, fte_id, {dc_gate_id: {"status": "completed", "output_text": None}}) == ""


def test_a_deactivated_node_passes_its_input_through_verbatim(graph: dict[str, Any]) -> None:
    """``_upstream_output_text`` is the node-disable semantic: the upstream
    text, with no prompt or header mixed in. Distinct from
    ``_build_user_input`` on purpose."""
    dc_gate_id = _AGENTS[0][2]
    fte_id = _AGENTS[1][0]
    node_runs = {dc_gate_id: {"status": "completed", "output_text": "DC accepted v1_dc."}}
    assert pe._upstream_output_text(graph, fte_id, node_runs) == "DC accepted v1_dc."


def test_the_score_agents_script_is_inlined_when_no_workspace_exists(graph: dict[str, Any]) -> None:
    """SF-Score has the Script node wired. With a workspace the code reaches it
    as an ambient ``script_path``; without one it is inlined in the prompt, and
    that fallback is what an unlinked run still relies on."""
    text = _prompt(graph, _AGENTS[4][0], script_bound=False)
    script = next(n for n in graph["nodes"] if n.get("type") == "script")
    code = (script["data"].get("config") or {}).get("code") or ""
    assert code, "fixture's Script node lost its code"
    assert code in text


def test_a_script_bound_prompt_does_not_inline_the_code(graph: dict[str, Any]) -> None:
    """The whole point of the Reference route: what executes is byte-for-byte
    what the user wrote, with no transcription for the model to get wrong."""
    text = _prompt(graph, _AGENTS[4][0], script_bound=True)
    script = next(n for n in graph["nodes"] if n.get("type") == "script")
    code = (script["data"].get("config") or {}).get("code") or ""
    assert code not in text


# -- factor bindings -----------------------------------------------------


def test_the_model_and_effort_factors_still_bind(graph: dict[str, Any]) -> None:
    """Two of the three factors in the 2^3 design bind onto the shared LLM
    node. ``apply_factor_bindings`` runs before any validation, so a broken
    binding would make every cell run the same arm."""
    patched = pe.apply_factor_bindings(graph, {"Azure Foundry:Model": "claude-opus-5", "Azure Foundry:Effort": "xhigh"})
    llm = next(n for n in patched["nodes"] if n.get("type") == "llm_azure_foundry")
    config = llm["data"]["config"]
    assert config["model"] == "claude-opus-5"
    assert config["effort"] == "xhigh"
    # The original is untouched -- apply_factor_bindings deep-copies, which is
    # what lets replicates of different arms share one stored graph.
    original = next(n for n in graph["nodes"] if n.get("type") == "llm_azure_foundry")
    assert original["data"]["config"]["model"] != "claude-opus-5"


def test_binding_an_absent_factor_is_a_no_op(graph: dict[str, Any]) -> None:
    patched = pe.apply_factor_bindings(graph, {})
    assert patched == graph


# -- the strategies this experiment does NOT use -------------------------


def test_the_stage_lineage_is_unchanged(tmp_path: Path) -> None:
    """``v0_raw -> v1_dc -> v2_fte -> v3_fs``, the lineage the paper's three
    staging agents produce.

    Pinned because Phase 6 replaces the hardcoded ``STAGE_VERSION`` with a
    resolved stage plan, and ``tabular_ml`` must be that constant exactly: the
    stage ids, their version ids, their ORDER, and the rule that a stage cannot
    read an input its predecessor never accepted. A generalization that got any
    of those subtly wrong would still pass its own new tests.
    """
    import numpy as np
    import pandas as pd
    from asaree_workspace_core import SEED_VERSION, STAGE_VERSION, Workspace, WorkspaceError

    assert list(STAGE_VERSION.items()) == [("dc", "v1_dc"), ("fte", "v2_fte"), ("fs", "v3_fs")]
    assert SEED_VERSION == "v0_raw"

    rng = np.random.RandomState(0)
    frame = pd.DataFrame({"a": rng.normal(size=40), "b": rng.normal(size=40), "target": rng.randint(0, 2, 40)})
    train_path, test_path = tmp_path / "train.parquet", tmp_path / "test.parquet"
    frame.iloc[:20].to_parquet(train_path)
    frame.iloc[20:].reset_index(drop=True).to_parquet(test_path)

    root = str(tmp_path / "workspaces")
    ws = Workspace.open(
        f"{_EXPERIMENT_ID}/{_CELL_LABEL}",
        target_column="target",
        seed_train_path=str(train_path),
        seed_test_path=str(test_path),
        root=root,
    )
    assert ws.load_state()["head"] == SEED_VERSION
    assert ws.target_column == "target"

    # A later stage cannot skip ahead of one that never accepted. This is the
    # invariant that makes the pipeline a pipeline rather than three
    # independent steps, and it is what "Critic enabled: False" still relies on
    # -- disabling the review must not let a stage run out of order.
    with pytest.raises(WorkspaceError):
        ws.read_stage_input("fte")

    for stage in ("dc", "fte", "fs"):
        # A passthrough stage: the point is the lineage and the gate order, not
        # the transform. write_stage fits nothing itself -- the caller has
        # already applied a train-fit transform to both splits.
        x_train, y_train, x_test, y_test = ws.read_stage_input(stage)
        ws.write_stage(
            stage,
            X_train=x_train,
            y_train=y_train,
            X_test=x_test,
            y_test=y_test,
            learned={},
            rationale=f"{stage} passthrough",
        )
        ws.accept_stage(stage)
        assert ws.has_accepted(stage)
        assert ws.load_state()["head"] == STAGE_VERSION[stage]

    versions = [v["id"] for v in ws.load_state()["versions"]]
    assert versions == ["v0_raw", "v1_dc", "v2_fte", "v3_fs"]

    # Re-opening keeps every accepted stage -- what makes seeding safe to call
    # unconditionally at the start of every agent turn.
    reopened = Workspace.open(
        f"{_EXPERIMENT_ID}/{_CELL_LABEL}",
        target_column="target",
        seed_train_path=str(train_path),
        seed_test_path=str(test_path),
        root=root,
    )
    assert reopened.load_state()["head"] == "v3_fs"


def test_a_v1_state_file_has_no_format_version(tmp_path: Path) -> None:
    """Phase 4 adds ``format_version`` to ``state.json`` and must treat its
    ABSENCE as v1, upgrading in memory only. This records what absence looks
    like, so that reader is written against the real shape rather than a
    guess."""
    import numpy as np
    import pandas as pd
    from asaree_workspace_core import Workspace

    rng = np.random.RandomState(0)
    frame = pd.DataFrame({"a": rng.normal(size=10), "target": rng.randint(0, 2, 10)})
    train_path, test_path = tmp_path / "tr.parquet", tmp_path / "te.parquet"
    frame.to_parquet(train_path)
    frame.to_parquet(test_path)
    ws = Workspace.open(
        f"{_EXPERIMENT_ID}/{_CELL_LABEL}",
        target_column="target",
        seed_train_path=str(train_path),
        seed_test_path=str(test_path),
        root=str(tmp_path / "workspaces"),
    )
    state = ws.load_state()
    assert "format_version" not in state
    assert set(state) == {"target_column", "head", "versions"}


def test_declaring_peer_collaboration_on_this_canvas_is_rejected(graph: dict[str, Any]) -> None:
    """A guard against the Phase 2 UX letting someone switch this experiment's
    strategy: the spinal canvas has no unfed peer-connected agent and no lead
    marker, so a conversation cannot start. Failing here is correct -- the
    point is that it fails at declaration time rather than mid-run."""
    with pytest.raises(ProtocolValidationError):
        validate_coordination_strategy({"coordination_strategy": {"slug": "peer_collaboration"}}, graph=graph)
