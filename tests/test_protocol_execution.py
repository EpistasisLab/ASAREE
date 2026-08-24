"""Unit tests for topological_order/find_gated_pairs (pure) and
_run_gated_worker (mocked -- never a real LLM call in an automated test)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for experiments' FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services import protocol_execution as pe
from asaree.services.experiments import create_experiment, delete_experiment
from asaree.services.factorial_cells import get_cell, upsert_cell
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    apply_factor_bindings,
    find_gated_pairs,
    plan_cell_runs,
    plan_single_cell_run,
    sink_node_ids,
    topological_order,
    validate_coordination_strategy,
)
from asaree.services.protocol_runs import create_protocol_run, get_protocol_run, request_protocol_run_cancellation
from asaree.services.protocols import create_protocol, delete_protocol


def _graph(node_ids: list[str], edges: list[tuple[str, str]]) -> dict:
    # "step" is a deliberately-unregistered node type -- not "agent" (needs
    # an LLM connector) and not "mcp_tool" (now handle-restricted to its own
    # Tool connector, see _MCP_TOOL_NODE_TYPES) -- so these pure DAG-shape
    # tests (topological order, cycle detection, sink detection) can wire
    # plain edges freely with zero setup. topological_order only applies
    # type-specific validation to types it recognizes, so an unrecognized
    # type sails through with just the generic Kahn's-algorithm order check.
    return {
        "nodes": [{"id": nid, "type": "step", "data": {}} for nid in node_ids],
        "edges": [{"id": f"{s}-{t}", "source": s, "target": t} for s, t in edges],
    }


def _node(node_id: str, node_type: str, config: dict | None = None, label: str = "") -> dict:
    return {"id": node_id, "type": node_type, "data": {"label": label, "config": config or {}}}


def _edges(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": f"{s}-{t}", "source": s, "target": t} for s, t in pairs]


def _llm_node(node_id: str = "llm", config: dict | None = None) -> dict:
    # llm_anthropic -- one arbitrary member of the LLM node-type family
    # (pe._LLM_NODE_TYPES); which one doesn't matter for these DAG-shape/
    # validation tests, only that it's a family member.
    return {"id": node_id, "type": "llm_anthropic", "data": {"label": "", "config": config or {}}}


def _llm_edge(source: str, target: str, handle: str = "ai") -> dict:
    # `handle` is only ever overridden to exercise the pre-rename "llm"
    # spelling that migration 3f1a7c9b2e04 rewrites -- see
    # test_legacy_llm_handle_still_resolves.
    return {"id": f"{source}-{target}-ai", "source": source, "target": target, "targetHandle": handle}


def _memory_node(node_id: str = "memory") -> dict:
    return {"id": node_id, "type": "memory", "data": {"label": "", "config": {}}}


def _memory_edge(source: str, target: str) -> dict:
    return {"id": f"{source}-{target}-memory", "source": source, "target": target, "targetHandle": "memory"}


def _pattern_node(node_id: str = "pattern") -> dict:
    # pattern_reason_act -- one arbitrary member of the pattern node-type
    # family (pe._PATTERN_NODE_TYPES), same reasoning as _llm_node above.
    return {"id": node_id, "type": "pattern_reason_act", "data": {"label": "", "config": {}}}


def _pattern_edge(source: str, target: str) -> dict:
    return {
        "id": f"{source}-{target}-architectural_pattern",
        "source": source,
        "target": target,
        "targetHandle": "architectural_pattern",
    }


def _tool_node(node_id: str = "tool1", server_name: str = "srv", tool_names: list[str] | None = None) -> dict:
    return {
        "id": node_id,
        "type": "mcp_tool",
        "data": {
            "label": "",
            "config": {"server_id": "s1", "server_name": server_name, "tool_names": tool_names or ["do_thing"]},
        },
    }


def _tool_edge(source: str, target: str) -> dict:
    return {"id": f"{source}-{target}-tool", "source": source, "target": target, "targetHandle": "tool"}


def _dataset_node(node_id: str = "dataset1", dataset_name: str = "spinal-fusion-v1") -> dict:
    return {
        "id": node_id,
        "type": "dataset",
        "data": {"label": "", "config": {"dataset_id": "d1", "dataset_name": dataset_name}},
    }


def _dataset_edge(source: str, target: str, handle: str = "resource") -> dict:
    # Dataset has its own Resource connector handle (see
    # _NODE_TYPE_TO_HANDLE). `handle="tool"` reproduces a graph saved before
    # that slot existed, when it shared the Tool handle -- still accepted,
    # see _LEGACY_DATASET_HANDLES.
    return {"id": f"{source}-{target}-dataset", "source": source, "target": target, "targetHandle": handle}


def _script_node(node_id: str = "script1", code: str = "print('hi')") -> dict:
    return {
        "id": node_id,
        "type": "script",
        "data": {"label": "", "config": {"name": "scoring-script", "language": "python", "code": code}},
    }


def _script_edge(source: str, target: str) -> dict:
    # Script likewise shares the Tool connector handle -- see _dataset_edge's
    # own comment above.
    return {"id": f"{source}-{target}-script", "source": source, "target": target, "targetHandle": "tool"}


def _agent_with_llm(node_id: str, llm_id: str = "llm") -> tuple[dict, dict]:
    """A minimal valid agent + its required LLM connector edge -- the
    boilerplate every connector-validation test below needs just to get
    past the "every agent needs exactly one AI connection" rule so it can
    test the thing it actually cares about."""
    return _node(node_id, "agent"), _llm_edge(llm_id, node_id)


def test_linear_order() -> None:
    order = [n["id"] for n in topological_order(_graph(["a", "b", "c"], [("a", "b"), ("b", "c")]))]
    assert order == ["a", "b", "c"]


def test_branching_respects_dependencies() -> None:
    graph = _graph(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
    order = [n["id"] for n in topological_order(graph)]
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_cycle_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="cycle"):
        topological_order(_graph(["a", "b"], [("a", "b"), ("b", "a")]))


def test_empty_graph_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="no nodes"):
        topological_order({"nodes": [], "edges": []})


def test_dangling_edge_ignored_not_a_cycle() -> None:
    # An edge referencing a node that doesn't exist (e.g. a stale edge after
    # a node was deleted client-side without the edge being cleaned up) is
    # simply not counted -- it must not be misread as a cycle.
    graph = {
        "nodes": [{"id": "a", "type": "step", "data": {}}],
        "edges": [{"id": "a-ghost", "source": "a", "target": "ghost"}],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert order == ["a"]


# --- Critic Gate topology validation -----------------------------------------


def test_valid_gated_pair_passes_and_is_mapped() -> None:
    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("w1", "agent"), _node("g1", "critic_gate"), _node("n1", "agent")],
        "edges": _edges(("w1", "g1"), ("g1", "n1"))
        + [_llm_edge(llm["id"], "w1"), _llm_edge(llm["id"], "g1"), _llm_edge(llm["id"], "n1")],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert order.index("w1") < order.index("g1") < order.index("n1")
    gate_node = next(n for n in graph["nodes"] if n["id"] == "g1")
    assert find_gated_pairs(graph) == {"w1": gate_node}


def test_critic_gate_with_two_incoming_edges_raises() -> None:
    graph = {
        "nodes": [_node("w1", "agent"), _node("w2", "agent"), _node("g1", "critic_gate")],
        "edges": _edges(("w1", "g1"), ("w2", "g1")),
    }
    with pytest.raises(ProtocolValidationError, match="exactly one incoming connection"):
        topological_order(graph)


def test_critic_gate_upstream_not_agent_raises() -> None:
    graph = {
        "nodes": [_node("t1", "mcp_tool"), _node("g1", "critic_gate")],
        "edges": _edges(("t1", "g1")),
    }
    with pytest.raises(ProtocolValidationError, match="must come from an Agent node"):
        topological_order(graph)


def test_gated_worker_fanout_raises() -> None:
    # w1 feeds both its critic_gate AND some other node directly -- ambiguous
    # (anything wanting the reviewed output must consume it after the gate).
    graph = {
        "nodes": [_node("w1", "agent"), _node("g1", "critic_gate"), _node("n1", "agent")],
        "edges": _edges(("w1", "g1"), ("w1", "n1")),
    }
    with pytest.raises(ProtocolValidationError, match="can't have any other outgoing connections"):
        topological_order(graph)


# --- _build_user_input -------------------------------------------------------


def test_build_user_input_prefers_prompt_over_goal() -> None:
    config = {"prompt": "Summarize this quarter's results", "goal": "Analyze financials"}
    node = _node("a", "agent", config, label="Analyst")
    assert pe._build_user_input(node, {"nodes": [node], "edges": []}, {}) == "Summarize this quarter's results"


def test_build_user_input_falls_back_to_goal_when_prompt_blank() -> None:
    node = _node("a", "agent", {"prompt": "", "goal": "Analyze financials"}, label="Analyst")
    assert pe._build_user_input(node, {"nodes": [node], "edges": []}, {}) == "Analyze financials"


def test_build_user_input_falls_back_to_label_when_both_blank() -> None:
    node = _node("a", "agent", {"prompt": "", "goal": ""}, label="Analyst")
    assert pe._build_user_input(node, {"nodes": [node], "edges": []}, {}) == "Analyst"


def test_build_user_input_appends_upstream_context_after_prompt() -> None:
    upstream = _node("u", "agent", {"goal": "produce a draft"}, label="Drafter")
    downstream = _node("d", "agent", {"prompt": "Polish the draft"}, label="Editor")
    graph = {"nodes": [upstream, downstream], "edges": _edges(("u", "d"))}
    node_runs = {"u": {"output_text": "draft text here"}}
    result = pe._build_user_input(downstream, graph, node_runs)
    assert result == "Polish the draft\n\nUpstream context:\n[u]: draft text here"


def test_build_user_input_appends_dataset_context_when_wired() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    result = pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0"
    )
    assert "Dataset context:" in result
    assert 'name="spinal-fusion-v1"' in result
    assert f'experiment_id="{uuid.UUID(int=1)}"' in result
    assert 'cell_label="tier_a__rep_0"' in result


def test_build_user_input_appends_dataset_context_from_legacy_tool_handle() -> None:
    # A graph saved before the Resource connector existed still has its
    # dataset edge on the Tool handle (see _LEGACY_DATASET_HANDLES) -- it
    # keeps resolving identically, so an old protocol runs unchanged even if
    # it's never opened in the canvas (which would rewrite the handle).
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {
        "nodes": [_llm_node(), agent, dataset],
        "edges": [agent_llm_edge, _dataset_edge("dataset1", "a", handle="tool")],
    }
    topological_order(graph)  # legacy handle is still a valid wiring, not a validation error
    result = pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0"
    )
    assert 'name="spinal-fusion-v1"' in result


def test_build_user_input_omits_dataset_context_when_disabled() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = {
        "id": "dataset1",
        "type": "dataset",
        "data": {"label": "", "config": {"dataset_id": "d1", "dataset_name": "spinal-fusion-v1", "enabled": False}},
    }
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    result = pe._build_user_input(
        agent, graph, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="tier_a__rep_0"
    )
    assert "Dataset context" not in result


def test_build_user_input_omits_dataset_context_when_unwired() -> None:
    node = _node("a", "agent", {"goal": "do the work"}, label="Worker")
    result = pe._build_user_input(
        node, {"nodes": [node], "edges": []}, {}, experiment_id=uuid.UUID(int=1), effective_cell_label="cell"
    )
    assert "Dataset context" not in result


def test_build_user_input_omits_dataset_context_without_experiment_id() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    # No experiment_id/effective_cell_label given -- nothing to build the
    # open_workspace instruction from, so the block is silently omitted
    # rather than emitting a malformed instruction.
    result = pe._build_user_input(agent, graph, {})
    assert "Dataset context" not in result


def test_build_user_input_appends_script_block_when_wired() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node(code="print('hello')")
    graph = {"nodes": [agent, script], "edges": [agent_llm_edge, _script_edge("script1", "a")]}
    result = pe._build_user_input(agent, graph, {})
    assert "Script to pass verbatim" in result
    assert "print('hello')" in result


def test_build_user_input_omits_script_block_when_unwired() -> None:
    node = _node("a", "agent", {"goal": "do the work"}, label="Worker")
    result = pe._build_user_input(node, {"nodes": [node], "edges": []}, {})
    assert "Script to pass verbatim" not in result


# --- _run_gated_worker (mocked -- no real LLM calls) -------------------------


def _worker_gate(max_revisions: int = 1, enabled: bool = True) -> tuple[dict, dict]:
    worker = _node("w1", "agent", {"goal": "do the work"}, label="Worker")
    gate = _node("g1", "critic_gate", {"enabled": enabled, "max_revisions": max_revisions}, label="Gate")
    return worker, gate


async def _run(worker: dict, gate: dict) -> tuple[dict, dict]:
    graph = {"nodes": [worker, gate], "edges": [{"id": "e1", "source": worker["id"], "target": gate["id"]}]}
    return await pe._run_gated_worker(
        worker,
        gate,
        protocol_id=uuid.uuid4(),
        protocol_run_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        graph=graph,
        node_runs={},
    )


async def test_gated_worker_approved_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return "worker output v1", None, None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(kwargs["worker_output"])
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {
        "status": "completed",
        "output_text": "worker output v1",
        "error": None,
        "attempts": 1,
        "run_id": None,
    }
    assert gate_run["approved"] is True
    assert gate_run["revisions_used"] == 0
    assert gate_run["feedback"] == ""
    assert gate_run["run_id"] == "critic-run-1"
    assert critic_calls == ["worker output v1"]


async def test_gated_worker_rejected_then_approved_on_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    instructions = []
    critic_calls = []

    async def fake_run_agent_node(node, *, user_input, **_kwargs):
        instructions.append(user_input)
        return f"worker output v{len(instructions)}", None, None

    async def fake_run_critic(gate, *, worker_output, **_kwargs):
        critic_calls.append(worker_output)
        if len(critic_calls) == 1:
            return {"approved": False, "feedback": "fix the header", "rejection_scope": "partial"}, None, "critic-run-1"
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None, "critic-run-2"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    # max_revisions=2 (not 1): with only 1 revision allowed, the 2nd/final
    # attempt would skip the critic entirely (see the force-accept test
    # below) -- this test needs room for a real "rejected, revised,
    # re-reviewed, approved" cycle before the final attempt.
    worker_run, gate_run = await _run(*_worker_gate(max_revisions=2))
    assert worker_run == {
        "status": "completed",
        "output_text": "worker output v2",
        "error": None,
        "attempts": 2,
        "run_id": None,
    }
    assert gate_run["approved"] is True
    assert gate_run["revisions_used"] == 1
    # The persisted verdict is the one that actually approved it, not the
    # earlier rejection that triggered the revision.
    assert gate_run["feedback"] == ""
    assert gate_run["run_id"] == "critic-run-2"
    assert len(critic_calls) == 2
    # The revised instruction carries the critic's feedback and the scope
    # framing forward -- not just a bare rerun of the original goal.
    assert "fix the header" in instructions[1]
    assert "targeted correction" in instructions[1]  # "partial" scope clause
    assert "worker output v1" in instructions[1]  # previous output included for reference


async def test_gated_worker_force_accepts_without_final_critic_call(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []
    critic_calls = []

    async def fake_run_agent_node(node, *, user_input, **_kwargs):
        attempts.append(user_input)
        return f"worker output v{len(attempts)}", None, None

    async def fake_run_critic(gate, *, worker_output, **_kwargs):
        critic_calls.append(worker_output)
        return {"approved": False, "feedback": "still wrong", "rejection_scope": "full"}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate(max_revisions=1))
    # 2 total attempts (max_revisions=1), but the critic is only ever
    # consulted once -- the final attempt force-accepts without spending a
    # second critic call on a verdict the pipeline would ignore anyway.
    assert worker_run["attempts"] == 2
    assert len(critic_calls) == 1
    # The forced-accept branch still surfaces the *last* critic verdict
    # (the rejection that forced this final attempt) instead of discarding
    # it once it stops being used to build the next instruction.
    assert gate_run == {
        "status": "completed",
        "output_text": "worker output v2",
        "approved": None,
        "revisions_used": 1,
        "forced": True,
        "feedback": "still wrong",
        "rejection_scope": "full",
        "run_id": "critic-run-1",
    }


async def test_gated_worker_disabled_skips_critic_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return "worker output", None, None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(1)
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate(enabled=False))
    assert worker_run["attempts"] == 1
    assert critic_calls == []
    assert gate_run["approved"] is None


async def test_gated_worker_worker_failure_stops_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return None, "the LLM call failed", None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(1)
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {
        "status": "failed",
        "output_text": None,
        "error": "the LLM call failed",
        "attempts": 1,
        "run_id": None,
    }
    assert gate_run == {"status": "skipped"}
    assert critic_calls == []


async def test_gated_worker_critic_failure_fails_the_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_agent_node(node, **kwargs):
        return "worker output", None, None

    async def fake_run_critic(gate, **kwargs):
        return None, "critic run timed out", "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run["status"] == "failed"
    assert "critic failed" in worker_run["error"]
    # Even on failure, the critic's own run_id is surfaced -- lets a user
    # drill into a timed-out/errored critic run for debugging.
    assert gate_run == {
        "status": "failed",
        "output_text": None,
        "error": "critic run timed out",
        "run_id": "critic-run-1",
    }


async def test_gated_worker_cancelled_mid_worker_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """_AGENT_CANCELLED (Motoro's own RunStatus.CANCELLED, detected in
    _run_agent_node) must be distinguished from a plain error -- it needs to
    surface as "cancelled", not "failed", so run_protocol's cancelled flag
    (not its failed flag) is what fires. The critic is never called (there's
    no output to review)."""
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return None, pe._AGENT_CANCELLED, "worker-run-1"

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(1)
        return {"approved": True}, None, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {
        "status": "cancelled",
        "output_text": None,
        "error": None,
        "attempts": 1,
        "run_id": "worker-run-1",
    }
    assert gate_run == {"status": "skipped"}
    assert critic_calls == []


async def test_gated_worker_cancelled_mid_critic_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancellation caught while the CRITIC is reviewing is different from
    one caught during the worker's own run: the worker already produced a
    real, complete output, so its own node_run still reports "completed"
    with that output -- only the gate's own node_run is "cancelled"."""

    async def fake_run_agent_node(node, **kwargs):
        return "real worker output", None, "worker-run-1"

    async def fake_run_critic(gate, **kwargs):
        return None, pe._AGENT_CANCELLED, "critic-run-1"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {
        "status": "completed",
        "output_text": "real worker output",
        "error": None,
        "attempts": 1,
        "run_id": "worker-run-1",
    }
    assert gate_run == {"status": "cancelled", "output_text": None, "error": None, "run_id": "critic-run-1"}


# --- apply_factor_bindings / sink_node_ids (pure) ----------------------------


def test_apply_factor_bindings_substitutes_bound_field() -> None:
    graph = {
        "nodes": [
            {
                "id": "w1",
                "type": "agent",
                "data": {
                    "config": {"model_config_data": {"temperature": 0.7}},
                    "factor_bindings": {"config.model_config_data.temperature": "Temperature"},
                },
            }
        ],
        "edges": [],
    }
    patched = apply_factor_bindings(graph, {"Temperature": 0.2})
    assert patched["nodes"][0]["data"]["config"]["model_config_data"]["temperature"] == 0.2
    # A deep copy, not a mutation -- the original graph is untouched.
    assert graph["nodes"][0]["data"]["config"]["model_config_data"]["temperature"] == 0.7


def test_apply_factor_bindings_skips_factor_not_in_values() -> None:
    graph = {
        "nodes": [
            {
                "id": "w1",
                "type": "agent",
                "data": {
                    "config": {"model_config_data": {"temperature": 0.7}},
                    "factor_bindings": {"config.model_config_data.temperature": "Temperature"},
                },
            }
        ],
        "edges": [],
    }
    patched = apply_factor_bindings(graph, {"SomeOtherFactor": 1})
    assert patched["nodes"][0]["data"]["config"]["model_config_data"]["temperature"] == 0.7


def test_apply_factor_bindings_substitutes_critic_enabled_boolean() -> None:
    graph = {
        "nodes": [
            {
                "id": "g1",
                "type": "critic_gate",
                "data": {"config": {"enabled": True}, "factor_bindings": {"config.enabled": "CriticOn"}},
            }
        ],
        "edges": [],
    }
    patched = apply_factor_bindings(graph, {"CriticOn": False})
    assert patched["nodes"][0]["data"]["config"]["enabled"] is False


def test_sink_node_ids_linear_chain_single_sink() -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("b", "c")])
    assert sink_node_ids(graph) == ["c"]


def test_sink_node_ids_fanout_multiple_sinks() -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("a", "c")])
    assert set(sink_node_ids(graph)) == {"b", "c"}


# --- plan_cell_runs / run_protocol cell writeback (real Postgres) -----------


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"protocol-exec-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Protocol Execution Test User",
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        uid = user.id
    yield uid
    async with get_session() as db:
        db_user = await db.get(User, uid)
        if db_user is not None:
            await db.delete(db_user)


async def test_plan_cell_runs_raises_without_experiment(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        with pytest.raises(ProtocolValidationError, match="no linked experiment"):
            await plan_cell_runs(db, protocol_id=uuid.uuid4(), experiment_id=None, owner_id=owner_id, graph=graph)


async def test_plan_cell_runs_raises_on_multi_sink_graph(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("a", "c")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-multisink-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
    try:
        async with get_session() as db:
            with pytest.raises(ProtocolValidationError, match="exactly one final node"):
                await plan_cell_runs(
                    db, protocol_id=uuid.uuid4(), experiment_id=experiment_id, owner_id=owner_id, graph=graph
                )
    finally:
        async with get_session() as db:
            await delete_experiment(db, experiment_id)


async def test_plan_cell_runs_creates_one_run_per_pending_cell_skips_scored(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-pending-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db, name=f"cell-run-pending-protocol-{uuid.uuid4().hex}", owner_id=owner_id, experiment_id=experiment_id
        )
        protocol_id = protocol.id
        await upsert_cell(db, experiment_id=experiment_id, cell_label="cell-1", fields={"factor_values": {"x": 1}})
        await upsert_cell(db, experiment_id=experiment_id, cell_label="cell-2", fields={"factor_values": {"x": 2}})
        # Already scored -- must be skipped, not re-run/re-billed.
        await upsert_cell(
            db,
            experiment_id=experiment_id,
            cell_label="cell-3",
            fields={"factor_values": {"x": 3}, "metric_values": {"roc_auc": 0.9}},
        )

    try:
        async with get_session() as db:
            runs, skipped = await plan_cell_runs(
                db, protocol_id=protocol_id, experiment_id=experiment_id, owner_id=owner_id, graph=graph
            )
        assert skipped == 1
        assert {r.cell_label for r in runs} == {"cell-1", "cell-2"}
        assert all(r.protocol_id == protocol_id for r in runs)
        by_label = {r.cell_label: r for r in runs}
        assert by_label["cell-1"].factor_values == {"x": 1}
        assert by_label["cell-2"].factor_values == {"x": 2}
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRuns
            await delete_experiment(db, experiment_id)


async def test_plan_single_cell_run_raises_without_experiment(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        with pytest.raises(ProtocolValidationError, match="no linked experiment"):
            await plan_single_cell_run(
                db, protocol_id=uuid.uuid4(), experiment_id=None, owner_id=owner_id, graph=graph, cell_label="cell-1"
            )


async def test_plan_single_cell_run_raises_on_multi_sink_graph(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b", "c"], [("a", "b"), ("a", "c")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"single-cell-multisink-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
    try:
        async with get_session() as db:
            with pytest.raises(ProtocolValidationError, match="exactly one final node"):
                await plan_single_cell_run(
                    db,
                    protocol_id=uuid.uuid4(),
                    experiment_id=experiment_id,
                    owner_id=owner_id,
                    graph=graph,
                    cell_label="cell-1",
                )
    finally:
        async with get_session() as db:
            await delete_experiment(db, experiment_id)


async def test_plan_single_cell_run_raises_on_unknown_cell_label(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"single-cell-unknown-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db, name=f"single-cell-unknown-protocol-{uuid.uuid4().hex}", owner_id=owner_id, experiment_id=experiment_id
        )
        protocol_id = protocol.id
    try:
        async with get_session() as db:
            with pytest.raises(ProtocolValidationError, match="No such cell"):
                await plan_single_cell_run(
                    db,
                    protocol_id=protocol_id,
                    experiment_id=experiment_id,
                    owner_id=owner_id,
                    graph=graph,
                    cell_label="does-not-exist",
                )
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_plan_single_cell_run_does_not_skip_an_already_scored_cell(owner_id: uuid.UUID) -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"single-cell-scored-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db, name=f"single-cell-scored-protocol-{uuid.uuid4().hex}", owner_id=owner_id, experiment_id=experiment_id
        )
        protocol_id = protocol.id
        # Already scored -- plan_cell_runs would skip this one; picking it by
        # name is a deliberate re-run, so plan_single_cell_run must not.
        await upsert_cell(
            db,
            experiment_id=experiment_id,
            cell_label="cell-1",
            fields={"factor_values": {"x": 1}, "metric_values": {"roc_auc": 0.9}},
        )

    try:
        async with get_session() as db:
            run = await plan_single_cell_run(
                db,
                protocol_id=protocol_id,
                experiment_id=experiment_id,
                owner_id=owner_id,
                graph=graph,
                cell_label="cell-1",
            )
        assert run.protocol_id == protocol_id
        assert run.cell_label == "cell-1"
        assert run.factor_values == {"x": 1}
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun
            await delete_experiment(db, experiment_id)


async def test_run_protocol_substitutes_factor_and_writes_back_to_cell(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end (minus the actual LLM call): a run created with
    cell_label/factor_values set gets the substituted value resolvable via
    the worker's LLM connector, and the sink node's output lands on the
    right cell via the real upsert_cell -- proves apply_factor_bindings is
    actually wired into run_protocol, not just correct in isolation. Model
    config lives on the connected `llm` node now, not the agent's own
    config -- the factor binding targets that node instead."""
    received_configs = []
    received_workspace_ids = []

    async def fake_run_agent_node(node, *, graph, workspace_id=None, **kwargs):
        received_configs.append(pe._resolve_llm_config(graph, node["id"]))
        received_workspace_ids.append(workspace_id)
        return f"output for {node['id']}", None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    async with get_session() as db:
        experiment = await create_experiment(db, name=f"cell-run-e2e-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"cell-run-e2e-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph={
                "nodes": [
                    {
                        "id": "llm1",
                        "type": "llm_anthropic",
                        "data": {
                            "config": {"temperature": 0.9},
                            "factor_bindings": {"config.temperature": "Temperature"},
                        },
                    },
                    {"id": "worker", "type": "agent", "data": {"config": {}}},
                ],
                "edges": [{"id": "llm1-worker", "source": "llm1", "target": "worker", "targetHandle": "ai"}],
            },
        )
        protocol_id = protocol.id
        await upsert_cell(
            db, experiment_id=experiment_id, cell_label="only-cell", fields={"factor_values": {"Temperature": 0.1}}
        )
        run = await create_protocol_run(
            db,
            protocol_id=protocol_id,
            owner_id=owner_id,
            cell_label="only-cell",
            factor_values={"Temperature": 0.1},
        )
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        assert received_configs[0]["temperature"] == 0.1
        assert received_workspace_ids[0] == f"{experiment_id}/only-cell"

        async with get_session() as db:
            cell = await get_cell(db, experiment_id=experiment_id, cell_label="only-cell")
            assert cell is not None
            assert cell.run_id == run_id
            assert cell.factor_values == {"Temperature": 0.1}
            assert cell.workspace_id == f"{experiment_id}/only-cell"
            assert cell.artifacts is not None
            assert cell.artifacts["output_text"] == "output for worker"
            assert cell.artifacts["protocol_run_id"] == str(run_id)
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun
            await delete_experiment(db, experiment_id)


async def _run_single_cell_protocol(owner_id: uuid.UUID) -> tuple[uuid.UUID, str, uuid.UUID, uuid.UUID]:
    """Shared setup for the two promote_cell_score_metrics wiring tests below
    -- a minimal one-agent, one-cell protocol run, ready for pe.run_protocol.
    Returns (experiment_id, cell_label, protocol_id, run_id)."""
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"score-promote-wiring-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"score-promote-wiring-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph={
                "nodes": [
                    {"id": "llm1", "type": "llm_anthropic", "data": {"config": {}}},
                    {"id": "worker", "type": "agent", "data": {"config": {}}},
                ],
                "edges": [{"id": "llm1-worker", "source": "llm1", "target": "worker", "targetHandle": "ai"}],
            },
        )
        protocol_id = protocol.id
        await upsert_cell(db, experiment_id=experiment_id, cell_label="only-cell", fields={"factor_values": {}})
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id, cell_label="only-cell")
        run_id = run.id
    return experiment_id, "only-cell", protocol_id, run_id


async def test_run_protocol_calls_score_metric_promotion_on_cell_completion(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wiring test for the auto-promotion added to run_protocol's post-write
    block: a completed cell run calls promote_cell_score_metrics with this
    run's own experiment_id/cell_label/protocol_run_id. The extraction logic
    itself (flattening a run_model_script result into metric_values) is
    covered separately, purely, in test_metric_promotion.py -- this only
    proves run_protocol actually reaches for it."""

    async def fake_run_agent_node(node, *, graph, workspace_id=None, **kwargs):
        return "worker output", None, None

    calls = []

    async def fake_promote(db, *, experiment_id, cell_label, protocol_run_id):
        calls.append((experiment_id, cell_label, protocol_run_id))

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "promote_cell_score_metrics", fake_promote)

    experiment_id, cell_label, protocol_id, run_id = await _run_single_cell_protocol(owner_id)
    try:
        await pe.run_protocol(run_id)
        assert calls == [(experiment_id, cell_label, run_id)]
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_run_protocol_survives_score_metric_promotion_failure(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort means best-effort: a real exception out of
    promote_cell_score_metrics (e.g. Motoro's run_steps table is
    unreachable) must not fail an otherwise-successful cell run, and the
    cell's own artifacts write must still land."""

    async def fake_run_agent_node(node, *, graph, workspace_id=None, **kwargs):
        return "worker output", None, None

    async def fake_promote(db, *, experiment_id, cell_label, protocol_run_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "promote_cell_score_metrics", fake_promote)

    experiment_id, cell_label, protocol_id, run_id = await _run_single_cell_protocol(owner_id)
    try:
        await pe.run_protocol(run_id)  # must not raise

        async with get_session() as db:
            run = await get_protocol_run(db, run_id)
            assert run is not None
            assert run.status == "completed"
            cell = await get_cell(db, experiment_id=experiment_id, cell_label=cell_label)
            assert cell is not None
            assert cell.artifacts is not None
            assert cell.artifacts["output_text"] == "worker output"
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


# --- node deactivate passthrough (pure helpers + wired-in run_protocol) -----


def test_upstream_output_text_no_upstream_is_empty() -> None:
    graph = _graph(["a"], [])
    assert pe._upstream_output_text(graph, "a", {}) == ""


def test_upstream_output_text_joins_completed_upstream_nodes() -> None:
    graph = _graph(["a", "b", "c"], [("a", "c"), ("b", "c")])
    node_runs = {
        "a": {"status": "completed", "output_text": "from a"},
        "b": {"status": "completed", "output_text": "from b"},
    }
    assert pe._upstream_output_text(graph, "c", node_runs) == "from a\n\nfrom b"


def test_upstream_output_text_skips_upstream_with_no_output() -> None:
    graph = _graph(["a", "b"], [("a", "b")])
    assert pe._upstream_output_text(graph, "b", {"a": {"status": "failed", "output_text": None}}) == ""


# --- workspace_id computation (pure) -----------------------------------------


def test_compute_workspace_id_for_a_real_cell_run() -> None:
    experiment_id = uuid.uuid4()
    protocol_run_id = uuid.uuid4()
    assert pe._compute_workspace_id(experiment_id, "tier_a__rep_0", protocol_run_id) == f"{experiment_id}/tier_a__rep_0"


def test_compute_workspace_id_adhoc_when_no_cell_label() -> None:
    # A manual "Run" click or single-node Play on an experiment-linked
    # protocol -- still gets a stable, per-run workspace so accept/reset
    # semantics still make sense outside the factorial grid.
    experiment_id = uuid.uuid4()
    protocol_run_id = uuid.uuid4()
    assert pe._compute_workspace_id(experiment_id, None, protocol_run_id) == f"{experiment_id}/adhoc-{protocol_run_id}"


def test_compute_workspace_id_none_without_experiment_id() -> None:
    # An unlinked protocol has no dataset to seed a workspace from.
    assert pe._compute_workspace_id(None, "some-cell", uuid.uuid4()) is None


def test_compute_workspace_id_sanitizes_a_real_cell_label() -> None:
    # Regression test: a real cell_label is built from free-text design_spec
    # factor names (e.g. "Azure Foundry:Model", "Critic enabled") and can
    # contain spaces/colons that asaree_workspace_core's own _SAFE_COMPONENT
    # regex rejects. Before this fix, _compute_workspace_id passed the raw
    # label through unsanitized, so run_model_script (which resolves
    # workspace_id purely from ambient _meta) looked for a workspace
    # directory that was never created under that exact raw name -- DC/FTE/
    # FS/MLM completed fine (their agents improvised their own sanitized
    # cell_label when calling open_workspace), but every Score stage failed
    # with "workspace not initialized".
    experiment_id = uuid.uuid4()
    protocol_run_id = uuid.uuid4()
    raw_label = "Azure Foundry:Effort_medium__Azure Foundry:Model_claude-sonnet-5__Critic enabled_false"
    expected = "Azure_Foundry_Effort_medium__Azure_Foundry_Model_claude-sonnet-5__Critic_enabled_false"
    assert pe._compute_workspace_id(experiment_id, raw_label, protocol_run_id) == f"{experiment_id}/{expected}"


def test_default_system_prompt_uses_the_node_own_label() -> None:
    # ASAREE's own explicit default -- never Motoro's own fallback,
    # which would use the internal "protocol-{id}-{id}" agent_name instead.
    assert pe._default_system_prompt("SF-DC", "Agent") == "You are SF-DC."


def test_default_system_prompt_falls_back_to_the_placeholder_when_unlabeled() -> None:
    assert pe._default_system_prompt(None, "Agent") == "You are Agent."
    assert pe._default_system_prompt("", "Critic Gate") == "You are Critic Gate."


def test_is_node_active_defaults_true_when_absent() -> None:
    assert pe._is_node_active(_node("a", "agent")) is True


def test_is_node_active_false_when_explicitly_deactivated() -> None:
    node = _node("a", "agent")
    node["data"]["active"] = False
    assert pe._is_node_active(node) is False


def test_deactivated_gated_worker_raises() -> None:
    worker = _node("w1", "agent")
    worker["data"]["active"] = False
    graph = {
        "nodes": [worker, _node("g1", "critic_gate")],
        "edges": _edges(("w1", "g1")),
    }
    with pytest.raises(ProtocolValidationError, match="can't be deactivated"):
        topological_order(graph)


async def test_run_protocol_deactivated_node_passes_through(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deactivated middle node never calls _run_agent_node -- its
    node_runs output_text is the upstream node's output, verbatim. Real
    Postgres rows (same convention as every other run_protocol-level test
    in this file), only the LLM call itself is mocked."""
    call_count = 0

    async def fake_run_agent_node(node, **kwargs):
        nonlocal call_count
        call_count += 1
        return f"real output from {node['id']}", None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    middle = _node("b", "agent")
    middle["data"]["active"] = False
    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("a", "agent"), middle, _node("c", "agent")],
        "edges": _edges(("a", "b"), ("b", "c"))
        + [_llm_edge(llm["id"], "a"), _llm_edge(llm["id"], "b"), _llm_edge(llm["id"], "c")],
    }

    async with get_session() as db:
        protocol = await create_protocol(db, name=f"deactivate-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph)
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        assert call_count == 2  # only "a" and "c" -- "b" is deactivated

        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "completed"
            assert fetched.node_runs["b"]["status"] == "completed"
            assert fetched.node_runs["b"]["output_text"] == "real output from a"
            assert fetched.node_runs["c"]["output_text"] == "real output from c"
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


async def test_run_protocol_honors_cancellation_between_nodes(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression/behavior test for Stop: cancellation is polled from the DB
    once per node boundary, not mid-node -- the node that's already in
    flight when cancel_requested_at gets set (here, "a") still runs to
    completion; only the nodes after it ("b", "c") are skipped, and the
    overall run lands on "cancelled" rather than "completed"."""
    call_count = 0

    async def fake_run_agent_node(node, **kwargs):
        nonlocal call_count
        call_count += 1
        if node["id"] == "a":
            # Simulates a concurrent Stop click landing while "a" is still
            # running -- a genuinely separate request/transaction in real
            # usage, modeled here as a second, independent session.
            async with get_session() as db:
                await request_protocol_run_cancellation(db, run_id)
        return f"output from {node['id']}", None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("a", "agent"), _node("b", "agent"), _node("c", "agent")],
        "edges": _edges(("a", "b"), ("b", "c"))
        + [_llm_edge(llm["id"], "a"), _llm_edge(llm["id"], "b"), _llm_edge(llm["id"], "c")],
    }

    async with get_session() as db:
        protocol = await create_protocol(db, name=f"cancel-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph)
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        assert call_count == 1  # only "a" -- "b" and "c" are skipped once cancellation is seen

        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "cancelled"
            assert fetched.node_runs["a"]["status"] == "completed"
            assert fetched.node_runs["a"]["output_text"] == "output from a"
            assert fetched.node_runs["b"] == {"status": "skipped"}
            assert fetched.node_runs["c"] == {"status": "skipped"}
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


async def test_run_protocol_honors_mid_node_cancellation(owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct from the between-nodes test above: this simulates a Stop
    click landing WHILE "a" is still executing (Motoro's own
    cancel_event interrupts it mid-phase, see _execute_run_cancellable),
    represented here by _run_agent_node returning the _AGENT_CANCELLED
    sentinel directly rather than a real output. "a" itself must be
    recorded "cancelled" (not "completed" with blank output, and not
    "failed"), and "b"/"c" are still skipped via the same cancelled flag."""
    call_count = 0

    async def fake_run_agent_node(node, **kwargs):
        nonlocal call_count
        call_count += 1
        if node["id"] == "a":
            return None, pe._AGENT_CANCELLED, uuid.uuid4()
        return f"output from {node['id']}", None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    llm = _llm_node()
    graph = {
        "nodes": [llm, _node("a", "agent"), _node("b", "agent"), _node("c", "agent")],
        "edges": _edges(("a", "b"), ("b", "c"))
        + [_llm_edge(llm["id"], "a"), _llm_edge(llm["id"], "b"), _llm_edge(llm["id"], "c")],
    }

    async with get_session() as db:
        protocol = await create_protocol(
            db, name=f"mid-node-cancel-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        assert call_count == 1  # only "a" -- interrupted mid-flight, "b"/"c" never start

        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "cancelled"
            assert fetched.node_runs["a"]["status"] == "cancelled"
            assert fetched.node_runs["a"]["output_text"] is None
            assert fetched.node_runs["b"] == {"status": "skipped"}
            assert fetched.node_runs["c"] == {"status": "skipped"}
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


async def test_poll_cancel_flag_sets_event_once_cancellation_requested(owner_id: uuid.UUID) -> None:
    """Isolated test of the poller itself, not the whole run_protocol path
    -- confirms it actually notices a cancellation raised on the row (by a
    separate request, modeled here as a separate session) and sets the
    event. Uses a short interval so the test doesn't sleep the production
    1.5s each time."""
    async with get_session() as db:
        protocol = await create_protocol(db, name=f"poll-test-{uuid.uuid4().hex}", owner_id=owner_id)
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        cancel_event = asyncio.Event()
        poller = asyncio.create_task(pe._poll_cancel_flag(run_id, cancel_event, interval=0.05))
        await asyncio.sleep(0.15)
        assert not cancel_event.is_set()  # nothing requested yet -- poller shouldn't fire spuriously

        async with get_session() as db:
            await request_protocol_run_cancellation(db, run_id)

        await asyncio.wait_for(poller, timeout=1.0)
        assert cancel_event.is_set()
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


# --- LLM / Tool / Memory connector validation (pure) -------------------------


def test_agent_missing_llm_connection_raises() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    with pytest.raises(ProtocolValidationError, match="exactly one AI connection"):
        topological_order(graph)


def test_agent_duplicate_llm_connection_raises() -> None:
    llm1, llm2 = _llm_node("llm1"), _llm_node("llm2")
    graph = {
        "nodes": [llm1, llm2, _node("a", "agent")],
        "edges": [_llm_edge("llm1", "a"), _llm_edge("llm2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="exactly one AI connection"):
        topological_order(graph)


def test_critic_gate_missing_llm_connection_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate")],
        "edges": [worker_llm_edge, {"id": "w1-g1", "source": "w1", "target": "g1"}],
    }
    with pytest.raises(ProtocolValidationError, match="exactly one AI connection"):
        topological_order(graph)


def test_legacy_llm_handle_still_resolves() -> None:
    # The AI connector's handle id was "llm" before it was renamed to "ai"
    # (migration 3f1a7c9b2e04 rewrites stored graphs). An un-migrated edge --
    # or one autosaved by a browser tab still running the pre-rename JS --
    # must resolve identically: same wiring, same model config, no "exactly
    # one AI connection" error from the edge being read as a main pipeline
    # edge instead.
    llm = _llm_node(config={"provider": "anthropic", "model": "claude-sonnet-4-5"})
    agent = _node("a", "agent")
    graph = {
        "nodes": [llm, agent],
        "edges": [_llm_edge("llm", "a", handle="llm")],
    }
    assert [n["id"] for n in topological_order(graph)] == ["llm", "a"]
    assert pe._resolve_llm_config(graph, "a")["model"] == "claude-sonnet-4-5"


def test_llm_connection_from_non_llm_source_raises() -> None:
    graph = {
        "nodes": [_node("t1", "step"), _node("a", "agent")],
        "edges": [{"id": "t1-a-ai", "source": "t1", "target": "a", "targetHandle": "ai"}],
    }
    with pytest.raises(ProtocolValidationError, match="must come from an AI node"):
        topological_order(graph)


def test_tool_connection_from_non_mcp_tool_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _tool_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="must come from an MCP Tool or Script node"):
        topological_order(graph)


def test_tool_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    tool = _tool_node()
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), tool],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _tool_edge("tool1", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, or Resource connection",
    ):
        topological_order(graph)


def test_multiple_memory_connections_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    mem1, mem2 = _memory_node("m1"), _memory_node("m2")
    graph = {
        "nodes": [llm, agent, mem1, mem2],
        "edges": [agent_llm_edge, _memory_edge("m1", "a"), _memory_edge("m2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="at most one Memory connection"):
        topological_order(graph)


def test_memory_connection_from_non_memory_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _memory_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="must come from a Memory node"):
        topological_order(graph)


def test_mcp_tool_node_with_plain_outgoing_edge_raises() -> None:
    # An mcp_tool node is always a Tool-connector source -- there's no more
    # "standalone pipeline step" role, so a plain edge out of one (even
    # alongside a real Tool connection) is rejected the same way an LLM/
    # Memory/Pattern node's plain edge already is.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node()
    graph = {
        "nodes": [llm, agent, tool, _node("b", "agent")],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a"), {"id": "tool1-b", "source": "tool1", "target": "b"}],
    }
    with pytest.raises(ProtocolValidationError, match="Tool node .* can only connect to a node's Tool slot"):
        topological_order(graph)


def test_llm_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, {"id": "llm-b", "source": "llm", "target": "b"}],
    }
    with pytest.raises(ProtocolValidationError, match="AI node .* can only connect to a node's AI slot"):
        topological_order(graph)


def test_memory_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    memory = _memory_node()
    graph = {
        "nodes": [llm, agent, memory, _node("b", "agent")],
        "edges": [agent_llm_edge, {"id": "memory-b", "source": "memory", "target": "b"}],
    }
    with pytest.raises(ProtocolValidationError, match="Memory node .* can only connect to a node's Memory slot"):
        topological_order(graph)


def test_multiple_dataset_connections_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    ds1, ds2 = _dataset_node("ds1"), _dataset_node("ds2")
    graph = {
        "nodes": [llm, agent, ds1, ds2],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="at most one Dataset connection"):
        topological_order(graph)


def test_dataset_connections_split_across_legacy_and_resource_handles_raises() -> None:
    # The cap counts dataset sources across BOTH handles, so a graph that's
    # half-migrated (one old Tool-handle edge, one new Resource one) can't
    # sneak two datasets onto the same agent.
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    ds1, ds2 = _dataset_node("ds1"), _dataset_node("ds2")
    graph = {
        "nodes": [llm, agent, ds1, ds2],
        "edges": [agent_llm_edge, _dataset_edge("ds1", "a"), _dataset_edge("ds2", "a", handle="tool")],
    }
    with pytest.raises(ProtocolValidationError, match="at most one Dataset connection"):
        topological_order(graph)


def test_dataset_connection_from_non_dataset_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _dataset_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="Resource connection must come from a Dataset node"):
        topological_order(graph)


def test_dataset_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    dataset = _dataset_node()
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), dataset],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _dataset_edge("dataset1", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, or Resource connection",
    ):
        topological_order(graph)


def test_dataset_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node()
    graph = {
        "nodes": [llm, agent, dataset, _node("b", "agent")],
        "edges": [
            agent_llm_edge,
            _dataset_edge("dataset1", "a"),
            {"id": "dataset1-b", "source": "dataset1", "target": "b"},
        ],
    }
    with pytest.raises(ProtocolValidationError, match="Dataset node .* can only connect to a node's Resource slot"):
        topological_order(graph)


def test_multiple_script_connections_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    s1, s2 = _script_node("s1"), _script_node("s2")
    graph = {
        "nodes": [llm, agent, s1, s2],
        "edges": [agent_llm_edge, _script_edge("s1", "a"), _script_edge("s2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="at most one Script connection"):
        topological_order(graph)


def test_script_connection_from_non_script_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _script_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="must come from an MCP Tool or Script node"):
        topological_order(graph)


def test_script_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    script = _script_node()
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), script],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _script_edge("script1", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, or Resource connection",
    ):
        topological_order(graph)


def test_script_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node()
    graph = {
        "nodes": [llm, agent, script, _node("b", "agent")],
        "edges": [
            agent_llm_edge,
            _script_edge("script1", "a"),
            {"id": "script1-b", "source": "script1", "target": "b"},
        ],
    }
    with pytest.raises(ProtocolValidationError, match="Script node .* can only connect to a node's Tool slot"):
        topological_order(graph)


def test_multiple_execution_pattern_connections_raises() -> None:
    # Capped at one -- execution_pattern is a single value, unlike Tool
    # (repeatable) or Memory (already max-1 for a different reason).
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    p1, p2 = _pattern_node("p1"), _pattern_node("p2")
    graph = {
        "nodes": [llm, agent, p1, p2],
        "edges": [agent_llm_edge, _pattern_edge("p1", "a"), _pattern_edge("p2", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="at most one execution-pattern connection"):
        topological_order(graph)


def test_architectural_pattern_connection_from_non_pattern_source_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {
        "nodes": [llm, agent, _node("b", "agent")],
        "edges": [agent_llm_edge, _pattern_edge("b", "a")],
    }
    with pytest.raises(ProtocolValidationError, match="must come from an Architectural Pattern node"):
        topological_order(graph)


def test_architectural_pattern_connection_on_critic_gate_raises() -> None:
    llm = _llm_node()
    worker, worker_llm_edge = _agent_with_llm("w1")
    gate_llm_edge = _llm_edge("llm", "g1")
    pattern = _pattern_node()
    graph = {
        "nodes": [llm, worker, _node("g1", "critic_gate"), pattern],
        "edges": [
            worker_llm_edge,
            gate_llm_edge,
            {"id": "w1-g1", "source": "w1", "target": "g1"},
            _pattern_edge("pattern", "g1"),
        ],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Only Agent nodes can have a Tool, Memory, Architectural Pattern, or Resource connection",
    ):
        topological_order(graph)


def test_architectural_pattern_node_with_plain_outgoing_edge_raises() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    pattern = _pattern_node()
    graph = {
        "nodes": [llm, agent, pattern, _node("b", "agent")],
        "edges": [agent_llm_edge, {"id": "pattern-b", "source": "pattern", "target": "b"}],
    }
    with pytest.raises(
        ProtocolValidationError,
        match="Architectural Pattern node .* can only connect to a node's Architectural Pattern slot",
    ):
        topological_order(graph)


def test_valid_llm_tool_memory_wiring_passes() -> None:
    llm = _llm_node(config={"provider": "anthropic", "model": "claude-sonnet-5"})
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node()
    memory = _memory_node()
    pattern = _pattern_node()
    graph = {
        "nodes": [llm, agent, tool, memory, pattern],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a"), _memory_edge("memory", "a"), _pattern_edge("pattern", "a")],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert set(order) == {"llm", "a", "tool1", "memory", "pattern"}


def test_llm_connection_accepts_any_provider_node_type() -> None:
    # Membership, not equality -- llm_openai/llm_azure_foundry are just as
    # valid an LLM connector source as _llm_node()'s default llm_anthropic.
    agent1, agent1_llm_edge = _agent_with_llm("a1", llm_id="openai")
    agent2, agent2_llm_edge = _agent_with_llm("a2", llm_id="foundry")
    openai_llm = {"id": "openai", "type": "llm_openai", "data": {"label": "", "config": {}}}
    foundry_llm = {"id": "foundry", "type": "llm_azure_foundry", "data": {"label": "", "config": {}}}
    graph = {
        "nodes": [agent1, agent2, openai_llm, foundry_llm],
        "edges": [agent1_llm_edge, agent2_llm_edge],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert set(order) == {"a1", "a2", "openai", "foundry"}


def test_architectural_pattern_connection_accepts_any_pattern_node_type() -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    baseline_pattern = {"id": "baseline", "type": "pattern_single_agent_baseline", "data": {"label": "", "config": {}}}
    graph = {
        "nodes": [llm, agent, baseline_pattern],
        "edges": [agent_llm_edge, _pattern_edge("baseline", "a")],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert set(order) == {"llm", "a", "baseline"}


# --- LLM / Tool connector resolution (pure) -----------------------------------


def test_resolve_llm_config_returns_connected_node_config() -> None:
    llm = _llm_node(config={"provider": "anthropic", "model": "claude-sonnet-5", "temperature": 0.5})
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [llm, agent], "edges": [agent_llm_edge]}
    assert pe._resolve_llm_config(graph, "a") == {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "temperature": 0.5,
    }


def test_resolve_llm_config_empty_when_unconnected() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_llm_config(graph, "a") == {}


def test_resolve_dataset_config_returns_connected_node_config() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    dataset = _dataset_node(dataset_name="spinal-fusion-v1")
    graph = {"nodes": [agent, dataset], "edges": [agent_llm_edge, _dataset_edge("dataset1", "a")]}
    assert pe._resolve_dataset_config(graph, "a") == {"dataset_id": "d1", "dataset_name": "spinal-fusion-v1"}


def test_resolve_dataset_config_empty_when_unconnected() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_dataset_config(graph, "a") == {}


def test_resolve_script_config_returns_connected_node_config() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    script = _script_node(code="print('hi')")
    graph = {"nodes": [agent, script], "edges": [agent_llm_edge, _script_edge("script1", "a")]}
    assert pe._resolve_script_config(graph, "a") == {
        "name": "scoring-script",
        "language": "python",
        "code": "print('hi')",
    }


def test_resolve_script_config_empty_when_unconnected() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_script_config(graph, "a") == {}


def test_resolve_tool_config_collects_all_connected_tool_nodes() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    tool1 = _tool_node("tool1", server_name="srv-a", tool_names=["fn_a"])
    tool2 = _tool_node("tool2", server_name="srv-b", tool_names=["fn_b"])
    graph = {
        "nodes": [agent, tool1, tool2],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a"), _tool_edge("tool2", "a")],
    }
    resolved = pe._resolve_tool_config(graph, "a")
    # tool_names must come back namespaced ("server.tool") -- that's the
    # shape run_tools.gather_tools matches against Motoro's registry;
    # a bare name never matches and silently strands the agent with zero
    # tools (see the regression test below).
    assert resolved == {"server_names": ["srv-a", "srv-b"], "tool_names": ["srv-a.fn_a", "srv-b.fn_b"]}


def test_resolve_tool_config_empty_when_no_tool_connections() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_tool_config(graph, "a") == {"server_names": [], "tool_names": []}


def test_resolve_tool_config_allows_multiple_tools_from_one_server() -> None:
    """One mcp_tool node == one server connection, which can allow-list
    several of that server's tools -- not one node per tool."""
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node(server_name="srv-a", tool_names=["fn_a", "fn_b", "fn_c"])
    graph = {"nodes": [agent, tool], "edges": [agent_llm_edge, _tool_edge("tool1", "a")]}
    resolved = pe._resolve_tool_config(graph, "a")
    assert resolved == {"server_names": ["srv-a"], "tool_names": ["srv-a.fn_a", "srv-a.fn_b", "srv-a.fn_c"]}




def test_resolve_tool_config_skips_disabled_tool_node() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    tool1 = _tool_node("tool1", server_name="srv-a", tool_names=["fn_a"])
    tool1["data"]["config"]["enabled"] = False
    tool2 = _tool_node("tool2", server_name="srv-b", tool_names=["fn_b"])
    graph = {
        "nodes": [agent, tool1, tool2],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a"), _tool_edge("tool2", "a")],
    }
    resolved = pe._resolve_tool_config(graph, "a")
    assert resolved == {"server_names": ["srv-b"], "tool_names": ["srv-b.fn_b"]}


def test_resolve_tool_config_namespaces_tool_names_for_gather_tools() -> None:
    """Regression test for the bug where every canvas-run agent silently got
    zero MCP tools: run_tools.gather_tools matches tool_names against
    Motoro's registry, whose entries are namespaced "server.tool"
    (MCPServerRegistry.get_all_tools). A bare tool_name never matches that,
    so the agent's LLM would see no tools at all -- no error, it just falls
    back to reporting the blocker as its final answer."""
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node(server_name="asaree-workspace", tool_names=["open_workspace", "accept_stage"])
    graph = {"nodes": [agent, tool], "edges": [agent_llm_edge, _tool_edge("tool1", "a")]}
    resolved = pe._resolve_tool_config(graph, "a")
    assert resolved["tool_names"] == ["asaree-workspace.open_workspace", "asaree-workspace.accept_stage"]


def test_resolve_tool_config_skips_tools_from_node_with_no_server_name() -> None:
    """A tool_names value can't be namespaced without a server_name to
    prefix it with, so those tools are dropped rather than smuggled through
    bare (which would silently fail the same way as the bug above)."""
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node(server_name=None, tool_names=["fn_a"])
    graph = {"nodes": [agent, tool], "edges": [agent_llm_edge, _tool_edge("tool1", "a")]}
    resolved = pe._resolve_tool_config(graph, "a")
    assert resolved == {"server_names": [], "tool_names": []}


def test_resolve_pattern_config_returns_connected_node_config() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    pattern = _pattern_node()
    pattern["data"]["config"] = {"max_iterations": 20, "include_scratchpad": False}
    graph = {"nodes": [agent, pattern], "edges": [agent_llm_edge, _pattern_edge("pattern", "a")]}
    assert pe._resolve_pattern_config(graph, "a") == {
        "execution_pattern": "reason_act",
        "pattern_params": {"reason_act": {"max_iterations": 20, "include_scratchpad": False}},
    }


def test_resolve_pattern_config_maps_baseline_slug() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    baseline = {
        "id": "baseline",
        "type": "pattern_single_agent_baseline",
        "data": {"label": "", "config": {"max_iterations": 5}},
    }
    graph = {"nodes": [agent, baseline], "edges": [agent_llm_edge, _pattern_edge("baseline", "a")]}
    assert pe._resolve_pattern_config(graph, "a") == {
        "execution_pattern": "single_agent_baseline",
        "pattern_params": {"single_agent_baseline": {"max_iterations": 5}},
    }


def test_resolve_pattern_config_empty_when_unconnected() -> None:
    # Optional connector -- an unconnected agent resolves to {}, letting
    # PatternConfig(execution_pattern=None) fall through to Motoro's
    # own "reason_act" default, not an ASAREE-side hardcoded one.
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    assert pe._resolve_pattern_config(graph, "a") == {}


def test_resolve_pattern_config_override_wins_over_wired_connector() -> None:
    """A factor bound to the agent's own data.pattern_override (via a plain
    _set_path top-level key, same as any other binding) switches the
    resolved pattern entirely -- this is how a Pattern factor varies the
    node type itself across cells, since the wired connector node alone
    can't."""
    agent, agent_llm_edge = _agent_with_llm("a")
    agent["data"]["pattern_override"] = {
        "execution_pattern": "single_agent_baseline",
        "pattern_params": {"single_agent_baseline": {"max_iterations": 3}},
    }
    pattern = _pattern_node()
    graph = {"nodes": [agent, pattern], "edges": [agent_llm_edge, _pattern_edge("pattern", "a")]}
    assert pe._resolve_pattern_config(graph, "a") == {
        "execution_pattern": "single_agent_baseline",
        "pattern_params": {"single_agent_baseline": {"max_iterations": 3}},
    }


def test_resolve_pattern_config_override_wins_when_unconnected() -> None:
    agent = {
        "id": "a",
        "type": "agent",
        "data": {"label": "", "config": {}, "pattern_override": {"execution_pattern": "reason_act"}},
    }
    graph = {"nodes": [agent], "edges": []}
    assert pe._resolve_pattern_config(graph, "a") == {"execution_pattern": "reason_act"}


async def test_run_protocol_tool_source_node_never_gets_its_own_turn(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An mcp_tool node is always a pure config source (see
    _PURE_CONFIG_SOURCE_TYPES) -- it must complete instantly with no output,
    the same way an llm/memory/pattern node does, never routed to
    _run_agent_node."""

    async def fake_run_agent_node(node, *, graph, **kwargs):
        return f"output for {node['id']}", None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    tool = _tool_node()
    graph = {
        "nodes": [llm, agent, tool],
        "edges": [agent_llm_edge, _tool_edge("tool1", "a")],
    }

    async with get_session() as db:
        protocol = await create_protocol(
            db, name=f"tool-source-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)

        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "completed"
            assert fetched.node_runs["tool1"] == {"status": "completed", "output_text": None, "error": None}
            assert fetched.node_runs["a"]["output_text"] == "output for a"
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun


# --- Coordination strategy validation (pure) ---------------------------------


def test_coordination_strategy_absent_is_a_noop() -> None:
    validate_coordination_strategy(None, has_gated_pair=False)
    validate_coordination_strategy({}, has_gated_pair=False)


def test_coordination_strategy_sequential_is_a_noop() -> None:
    validate_coordination_strategy({"coordination_strategy": {"slug": "sequential"}}, has_gated_pair=False)
    validate_coordination_strategy({"coordination_strategy": {"slug": "sequential"}}, has_gated_pair=True)


def test_coordination_strategy_critic_gate_requires_a_gated_pair() -> None:
    with pytest.raises(ProtocolValidationError, match="no Critic Gate node wired in"):
        validate_coordination_strategy({"coordination_strategy": {"slug": "critic_gate"}}, has_gated_pair=False)


def test_coordination_strategy_critic_gate_passes_with_a_gated_pair() -> None:
    validate_coordination_strategy({"coordination_strategy": {"slug": "critic_gate"}}, has_gated_pair=True)


def test_coordination_strategy_placeholder_slug_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="isn't implemented yet"):
        validate_coordination_strategy(
            {"coordination_strategy": {"slug": "supervisor_architecture"}}, has_gated_pair=False
        )


def test_coordination_strategy_unknown_slug_raises() -> None:
    with pytest.raises(ProtocolValidationError, match="Unknown coordination strategy"):
        validate_coordination_strategy({"coordination_strategy": {"slug": "not-a-real-slug"}}, has_gated_pair=False)


async def test_run_protocol_rejects_placeholder_coordination_strategy(owner_id: uuid.UUID) -> None:
    llm = _llm_node()
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [llm, agent], "edges": [agent_llm_edge]}

    async with get_session() as db:
        experiment = await create_experiment(
            db,
            name=f"coord-strategy-test-{uuid.uuid4().hex}",
            owner_id=owner_id,
            design_spec={"coordination_strategy": {"slug": "swarm_architecture"}},
        )
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"coord-strategy-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph=graph,
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "failed"
            assert fetched.error is not None
            assert "isn't implemented yet" in fetched.error
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


# --- validate_single_node_runnable / single-node "Play" runs -----------------


def test_validate_single_node_runnable_missing_node_raises() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [agent], "edges": [agent_llm_edge]}
    with pytest.raises(ProtocolValidationError, match="No such node"):
        pe.validate_single_node_runnable(graph, "does-not-exist")


def test_validate_single_node_runnable_rejects_non_agent_type() -> None:
    node = _node("g1", "critic_gate")
    graph = {"nodes": [node], "edges": []}
    with pytest.raises(ProtocolValidationError, match="Only Agent nodes"):
        pe.validate_single_node_runnable(graph, "g1")


def test_validate_single_node_runnable_rejects_a_node_with_upstream_input() -> None:
    upstream = _node("u", "agent")
    downstream, downstream_llm_edge = _agent_with_llm("d")
    graph = {"nodes": [upstream, downstream], "edges": _edges(("u", "d")) + [downstream_llm_edge]}
    with pytest.raises(ProtocolValidationError, match="upstream input"):
        pe.validate_single_node_runnable(graph, "d")


def test_validate_single_node_runnable_rejects_zero_llm_connections() -> None:
    graph = {"nodes": [_node("a", "agent")], "edges": []}
    with pytest.raises(ProtocolValidationError, match="must have exactly one AI connection"):
        pe.validate_single_node_runnable(graph, "a")


def test_validate_single_node_runnable_rejects_llm_edge_from_wrong_node_type() -> None:
    agent = _node("a", "agent")
    not_an_llm = _node("x", "agent")
    graph = {"nodes": [agent, not_an_llm], "edges": [_llm_edge("x", "a")]}
    with pytest.raises(ProtocolValidationError, match="must come from an AI node"):
        pe.validate_single_node_runnable(graph, "a")


def test_validate_single_node_runnable_accepts_a_valid_standalone_agent() -> None:
    agent, agent_llm_edge = _agent_with_llm("a")
    graph = {"nodes": [agent, _llm_node()], "edges": [agent_llm_edge]}
    assert pe.validate_single_node_runnable(graph, "a") is agent


async def test_run_single_node_ignores_an_unrelated_broken_sibling_node(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of a narrower, per-node check: a single-node Play run
    must not fail because some OTHER node elsewhere in the same graph is
    unrelated and broken (e.g. missing its own LLM connector) -- only
    topological_order's full-graph walk cares about that."""

    async def fake_run_agent_node(node, *, user_input, **_kwargs):
        return f"solo output for {node['id']} given {user_input!r}", None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    target, target_llm_edge = _agent_with_llm("target")
    target["data"]["config"] = {"prompt": "do the one thing", "goal": ""}
    broken_sibling = _node("broken", "agent")  # no LLM connector at all

    graph = {"nodes": [target, broken_sibling, _llm_node()], "edges": [target_llm_edge]}

    async with get_session() as db:
        protocol = await create_protocol(
            db, name=f"single-node-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id, target_node_id="target")
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "completed"
            assert fetched.node_runs.keys() == {"target"}  # the broken sibling is never touched
            assert fetched.node_runs["target"]["output_text"] == "solo output for target given 'do the one thing'"
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)


async def test_run_single_node_computes_adhoc_workspace_id_when_experiment_linked(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single-node Play run never has a real cell_label, but still gets a
    stable per-run workspace when its protocol is linked to an experiment
    (see _compute_workspace_id's own "adhoc" fallback)."""
    received_workspace_ids = []

    async def fake_run_agent_node(node, *, workspace_id=None, **_kwargs):
        received_workspace_ids.append(workspace_id)
        return f"solo output for {node['id']}", None, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)

    target, target_llm_edge = _agent_with_llm("target")
    graph = {"nodes": [target, _llm_node()], "edges": [target_llm_edge]}

    async with get_session() as db:
        experiment = await create_experiment(db, name=f"single-node-ws-e2e-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"single-node-ws-test-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
            graph=graph,
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id, target_node_id="target")
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        assert received_workspace_ids == [f"{experiment_id}/adhoc-{run_id}"]
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
            await delete_experiment(db, experiment_id)


async def test_run_single_node_with_upstream_input_fails_cleanly(owner_id: uuid.UUID) -> None:
    upstream = _node("u", "agent")
    downstream, downstream_llm_edge = _agent_with_llm("d")
    graph = {"nodes": [upstream, downstream, _llm_node()], "edges": _edges(("u", "d")) + [downstream_llm_edge]}

    async with get_session() as db:
        protocol = await create_protocol(
            db, name=f"single-node-upstream-test-{uuid.uuid4().hex}", owner_id=owner_id, graph=graph
        )
        protocol_id = protocol.id
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id, target_node_id="d")
        run_id = run.id

    try:
        await pe.run_protocol(run_id)
        async with get_session() as db:
            fetched = await pe.get_protocol_run(db, run_id)
            assert fetched is not None
            assert fetched.status == "failed"
            assert fetched.error is not None
            assert "upstream input" in fetched.error
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)
