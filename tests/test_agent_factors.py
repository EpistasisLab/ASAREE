"""Agent factors preserve required roles before execution or batch creation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from asaree.services import protocol_execution as pe


def _graph(*ids: str) -> dict[str, Any]:
    return {
        "nodes": [
            {"id": nid, "type": "agent", "data": {"label": nid, "active": True, "config": {"prompt": "Task"}}}
            for nid in ids
        ] + [{"id": "model", "type": "model_openai", "data": {"config": {"provider": "openai", "model": "gpt-5"}}}],
        "edges": [
            {"id": f"model-{nid}", "source": "model", "target": nid, "targetHandle": "model"} for nid in ids
        ] + [{"id": f"{a}-{b}", "source": a, "target": b} for a, b in zip(ids, ids[1:], strict=False)],
    }


def _disable(graph: dict[str, Any], *ids: str) -> None:
    for node in graph["nodes"]:
        if node["id"] in ids:
            node["data"]["active"] = False


def test_on_off_factor_cannot_disable_the_last_executable_agent() -> None:
    graph = _graph("writer")
    graph["nodes"][0]["data"]["factor_bindings"] = {"active": "Writer enabled"}
    patched = pe.apply_factor_bindings(graph, {"Writer enabled": False})
    with pytest.raises(pe.ProtocolValidationError, match="at least one executable Agent"):
        pe.validate_coordination_strategy(None, graph=patched)
    assert graph["nodes"][0]["data"]["active"] is True


def test_sequential_cells_can_disable_a_stage_when_another_agent_remains() -> None:
    graph = _graph("first", "middle", "last")
    _disable(graph, "middle")
    pe.validate_coordination_strategy(None, graph=graph)


@pytest.mark.parametrize("marked", [False, True])
def test_peer_lead_cannot_be_disabled(marked: bool) -> None:
    graph = _graph("lead", "peer")
    graph["nodes"][0]["data"]["conversation_lead"] = marked
    _disable(graph, "lead")
    with pytest.raises(pe.ProtocolValidationError, match="conversation lead.*must remain active"):
        pe.validate_coordination_strategy({"coordination_strategy": {"slug": "peer_collaboration"}}, graph=graph)


async def test_peer_factor_can_disable_a_collaborator() -> None:
    graph = _graph("lead", "peer")
    _disable(graph, "peer")
    pe.validate_coordination_strategy({"coordination_strategy": {"slug": "peer_collaboration"}}, graph=graph)
    assert await pe.resolve_agent_card(graph, "peer", owner_id=uuid.uuid4()) is None


def test_gated_worker_cannot_be_disabled_by_a_cell_factor() -> None:
    graph = _graph("worker", "next")
    graph["nodes"].append({"id": "gate", "type": "critic_gate", "data": {"config": {"enabled": True}}})
    graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "worker-next"] + [
        {"id": "worker-gate", "source": "worker", "target": "gate"},
        {"id": "gate-next", "source": "gate", "target": "next"},
    ]
    _disable(graph, "worker")
    with pytest.raises(pe.ProtocolValidationError, match="gated by a Critic Gate.*deactivated"):
        pe.validate_coordination_strategy(None, graph=graph)


@pytest.fixture
def planning(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], AsyncMock]:
    graph = _graph("first", "last")
    for node in graph["nodes"]:
        if node["type"] == "agent":
            node["data"]["factor_bindings"] = {"active": "Enabled"}
    experiment = SimpleNamespace(
        design_spec={"factors": [{"name": "Enabled", "level_type": "boolean", "levels": [False, True]}]},
        locked_at=None, measurement_plan=None,
    )
    replicates = [SimpleNamespace(
        id=uuid.uuid4(), design_revision_id=uuid.uuid4(), replicate_label=f"replicate-{enabled}",
        factor_values={"Enabled": enabled}, metric_values={}, run_id=None,
    ) for enabled in (True, False)]
    monkeypatch.setattr(pe, "get_experiment", AsyncMock(return_value=experiment))
    monkeypatch.setattr(pe, "get_design_impact", AsyncMock(return_value=SimpleNamespace(regeneration_required=False)))
    monkeypatch.setattr(pe, "validate_experiment_measurement_plan", AsyncMock(return_value=[]))
    monkeypatch.setattr(pe, "blocking_measurement_plan_issues", lambda _report: [])
    for name in ("validate_skill_factors", "validate_knowledge_factors", "validate_dataset_factors"):
        monkeypatch.setattr(pe, name, AsyncMock())
    monkeypatch.setattr(pe, "list_replicates", AsyncMock(return_value=replicates))
    monkeypatch.setattr(pe, "get_replicate", AsyncMock(return_value=replicates[1]))
    create = AsyncMock()
    monkeypatch.setattr(pe, "create_protocol_run", create)
    return graph, create


async def test_batch_rejects_an_invalid_cell_before_creating_any_runs(
    planning: tuple[dict[str, Any], AsyncMock],
) -> None:
    graph, create = planning
    with pytest.raises(pe.ProtocolValidationError, match="replicate-False.*disables every Agent"):
        await pe.plan_cell_runs(
            AsyncMock(), protocol_id=uuid.uuid4(), experiment_id=uuid.uuid4(), owner_id=uuid.uuid4(), graph=graph
        )
    create.assert_not_awaited()


async def test_single_replicate_rejects_an_invalid_cell_before_creating_a_run(
    planning: tuple[dict[str, Any], AsyncMock],
) -> None:
    graph, create = planning
    with pytest.raises(pe.ProtocolValidationError, match="disables every Agent"):
        await pe.plan_single_replicate_run(
            AsyncMock(), protocol_id=uuid.uuid4(), experiment_id=uuid.uuid4(), owner_id=uuid.uuid4(), graph=graph,
            replicate_label="replicate-False",
        )
    create.assert_not_awaited()


async def test_batch_can_run_a_valid_selection_without_rejecting_unselected_cells(
    planning: tuple[dict[str, Any], AsyncMock],
) -> None:
    graph, create = planning
    runs, skipped = await pe.plan_cell_runs(
        AsyncMock(), protocol_id=uuid.uuid4(), experiment_id=uuid.uuid4(), owner_id=uuid.uuid4(), graph=graph,
        replicate_labels={"replicate-True"},
    )
    assert len(runs) == 1
    assert skipped == 0
    create.assert_awaited_once()
    assert create.await_args.kwargs["factor_values"] == {"Enabled": True}
