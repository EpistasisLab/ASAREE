"""Unit tests for topological_order/find_gated_pairs (pure) and
_run_gated_worker (mocked -- never a real LLM call in an automated test)."""

from __future__ import annotations

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
    sink_node_ids,
    topological_order,
)
from asaree.services.protocol_runs import create_protocol_run
from asaree.services.protocols import create_protocol, delete_protocol


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


async def test_run_protocol_substitutes_factor_and_writes_back_to_cell(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end (minus the actual LLM call): a run created with
    cell_label/factor_values set gets the substituted value handed to
    _run_agent_node, and the sink node's output lands on the right cell via
    the real upsert_cell -- proves apply_factor_bindings is actually wired
    into run_protocol, not just correct in isolation."""
    received_configs = []

    async def fake_run_agent_node(node, **kwargs):
        received_configs.append(node["data"]["config"])
        return f"output for {node['id']}", None

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
                        "id": "worker",
                        "type": "agent",
                        "data": {
                            "config": {"model_config_data": {"temperature": 0.9}},
                            "factor_bindings": {"config.model_config_data.temperature": "Temperature"},
                        },
                    }
                ],
                "edges": [],
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

        assert received_configs[0]["model_config_data"]["temperature"] == 0.1

        async with get_session() as db:
            cell = await get_cell(db, experiment_id=experiment_id, cell_label="only-cell")
            assert cell is not None
            assert cell.run_id == run_id
            assert cell.factor_values == {"Temperature": 0.1}
            assert cell.artifacts is not None
            assert cell.artifacts["output_text"] == "output for worker"
            assert cell.artifacts["protocol_run_id"] == str(run_id)
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)  # cascades the created ProtocolRun
            await delete_experiment(db, experiment_id)
