"""Pipeline bypass outcomes without a database or provider connection."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from asaree.services import protocol_execution as pe


async def test_inactive_agent_passes_output_through_and_records_bypass(monkeypatch):
    graph = {
        "nodes": [
            {"id": name, "type": "agent", "data": {"active": name != "b", "config": {}}} for name in ("a", "b", "c")
        ]
        + [{"id": "model", "type": "model_openai", "data": {"config": {}}}],
        "edges": [
            {"id": "a-b", "source": "a", "target": "b"},
            {"id": "b-c", "source": "b", "target": "c"},
        ]
        + [
            {"id": f"model-{name}", "source": "model", "target": name, "targetHandle": "model"}
            for name in ("a", "b", "c")
        ],
    }
    protocol = SimpleNamespace(id=uuid.uuid4(), experiment_id=None, graph=graph)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        protocol_id=protocol.id,
        owner_id=uuid.uuid4(),
        status="pending",
        node_runs={},
        cancel_requested_at=None,
        row_result_id=None,
        replicate_result_id=None,
        dataset_row=None,
        attempt_result=None,
        protocol_revision_id=None,
        design_revision_id=None,
        target_node_id=None,
        factor_values=None,
        replicate_label=None,
    )
    db = MagicMock()
    selected = MagicMock()
    selected.scalar_one_or_none.return_value = run
    db.execute = AsyncMock(return_value=selected)

    @asynccontextmanager
    async def session():
        yield db

    async def status(_db, _run_id, *, status, **kwargs):
        run.status = status

    async def update(_db, _run_id, node_id, fields):
        run.node_runs[node_id] = {**run.node_runs.get(node_id, {}), **fields}

    calls = []

    async def execute(node, **kwargs):
        calls.append(node["id"])
        return f"Output from {node['id']}", None, None, None

    monkeypatch.setattr(pe, "get_session", session)
    monkeypatch.setattr(pe, "get_protocol_run", AsyncMock(return_value=run))
    monkeypatch.setattr(pe, "get_protocol", AsyncMock(return_value=protocol))
    monkeypatch.setattr(pe, "hydrate_registry", AsyncMock())
    monkeypatch.setattr(pe, "set_status", status)
    monkeypatch.setattr(pe, "update_node_run", update)
    monkeypatch.setattr(pe, "_run_agent_node", execute)
    monkeypatch.setattr(
        pe,
        "_node_run_context",
        AsyncMock(
            return_value=(
                {},
                SimpleNamespace(seeded=(), unsplit_name=""),
            )
        ),
    )
    monkeypatch.setattr("asaree.services.agent_messenger.record_sequential_transcript", AsyncMock())

    await pe.run_protocol(run.id)
    assert run.status == "completed"
    assert calls == ["a", "c"]
    assert run.node_runs["b"] == {
        "status": "completed",
        "bypassed": True,
        "output_text": "Output from a",
        "error": None,
        "run_id": None,
    }
    assert "bypassed" not in run.node_runs["a"]
    assert "bypassed" not in run.node_runs["c"]
