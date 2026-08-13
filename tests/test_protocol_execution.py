"""Unit tests for topological_order/find_gated_pairs (pure) and
_run_gated_worker (mocked -- never a real LLM call in an automated test)."""

from __future__ import annotations

import uuid

import pytest

from asaree.services import protocol_execution as pe
from asaree.services.protocol_execution import ProtocolValidationError, find_gated_pairs, topological_order


def _graph(node_ids: list[str], edges: list[tuple[str, str]]) -> dict:
    return {
        "nodes": [{"id": nid, "type": "agent", "data": {}} for nid in node_ids],
        "edges": [{"id": f"{s}-{t}", "source": s, "target": t} for s, t in edges],
    }


def _node(node_id: str, node_type: str, config: dict | None = None, label: str = "") -> dict:
    return {"id": node_id, "type": node_type, "data": {"label": label, "config": config or {}}}


def _edges(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": f"{s}-{t}", "source": s, "target": t} for s, t in pairs]


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
        "nodes": [{"id": "a", "type": "agent", "data": {}}],
        "edges": [{"id": "a-ghost", "source": "a", "target": "ghost"}],
    }
    order = [n["id"] for n in topological_order(graph)]
    assert order == ["a"]


# --- Critic Gate topology validation -----------------------------------------


def test_valid_gated_pair_passes_and_is_mapped() -> None:
    graph = {
        "nodes": [_node("w1", "agent"), _node("g1", "critic_gate"), _node("n1", "agent")],
        "edges": _edges(("w1", "g1"), ("g1", "n1")),
    }
    order = [n["id"] for n in topological_order(graph)]
    assert order.index("w1") < order.index("g1") < order.index("n1")
    assert find_gated_pairs(graph) == {"w1": graph["nodes"][1]}


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
        return "worker output v1", None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(kwargs["worker_output"])
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {"status": "completed", "output_text": "worker output v1", "error": None, "attempts": 1}
    assert gate_run["approved"] is True
    assert gate_run["revisions_used"] == 0
    assert critic_calls == ["worker output v1"]


async def test_gated_worker_rejected_then_approved_on_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    instructions = []
    critic_calls = []

    async def fake_run_agent_node(node, *, user_input, **_kwargs):
        instructions.append(user_input)
        return f"worker output v{len(instructions)}", None

    async def fake_run_critic(gate, *, worker_output, **_kwargs):
        critic_calls.append(worker_output)
        if len(critic_calls) == 1:
            return {"approved": False, "feedback": "fix the header", "rejection_scope": "partial"}, None
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    # max_revisions=2 (not 1): with only 1 revision allowed, the 2nd/final
    # attempt would skip the critic entirely (see the force-accept test
    # below) -- this test needs room for a real "rejected, revised,
    # re-reviewed, approved" cycle before the final attempt.
    worker_run, gate_run = await _run(*_worker_gate(max_revisions=2))
    assert worker_run == {"status": "completed", "output_text": "worker output v2", "error": None, "attempts": 2}
    assert gate_run["approved"] is True
    assert gate_run["revisions_used"] == 1
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
        return f"worker output v{len(attempts)}", None

    async def fake_run_critic(gate, *, worker_output, **_kwargs):
        critic_calls.append(worker_output)
        return {"approved": False, "feedback": "still wrong", "rejection_scope": "full"}, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate(max_revisions=1))
    # 2 total attempts (max_revisions=1), but the critic is only ever
    # consulted once -- the final attempt force-accepts without spending a
    # second critic call on a verdict the pipeline would ignore anyway.
    assert worker_run["attempts"] == 2
    assert len(critic_calls) == 1
    assert gate_run == {
        "status": "completed",
        "output_text": "worker output v2",
        "approved": None,
        "revisions_used": 1,
        "forced": True,
    }


async def test_gated_worker_disabled_skips_critic_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return "worker output", None

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(1)
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate(enabled=False))
    assert worker_run["attempts"] == 1
    assert critic_calls == []
    assert gate_run["approved"] is None


async def test_gated_worker_worker_failure_stops_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    critic_calls = []

    async def fake_run_agent_node(node, **kwargs):
        return None, "the LLM call failed"

    async def fake_run_critic(gate, **kwargs):
        critic_calls.append(1)
        return {"approved": True, "feedback": "", "rejection_scope": ""}, None

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run == {"status": "failed", "output_text": None, "error": "the LLM call failed", "attempts": 1}
    assert gate_run == {"status": "skipped"}
    assert critic_calls == []


async def test_gated_worker_critic_failure_fails_the_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_agent_node(node, **kwargs):
        return "worker output", None

    async def fake_run_critic(gate, **kwargs):
        return None, "critic run timed out"

    monkeypatch.setattr(pe, "_run_agent_node", fake_run_agent_node)
    monkeypatch.setattr(pe, "_run_critic", fake_run_critic)

    worker_run, gate_run = await _run(*_worker_gate())
    assert worker_run["status"] == "failed"
    assert "critic failed" in worker_run["error"]
    assert gate_run == {"status": "failed", "output_text": None, "error": "critic run timed out"}
