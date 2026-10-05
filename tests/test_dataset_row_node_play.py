"""PostgreSQL coverage for row-selected per-node Play previews."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motoro.models import database as core_database
from motoro.models.agent import Agent
from motoro.models.run import AgentRun, RunStatus
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import asaree.api.protocols as protocol_api
import asaree.services.dataset_row_workspaces as row_workspaces
import asaree.services.protocol_execution as execution
import asaree.services.run_tools as run_tools
from asaree.deps import get_current_user
from asaree.models import database
from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_run import ProtocolRun
from asaree.models.user import User
from asaree.services.experiments import create_experiment, delete_experiment
from asaree.services.factorial_cells import upsert_replicate
from asaree.services.factorial_row_results import ensure_row_result
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocols import create_protocol, delete_protocol
from asaree.services.users import get_user_by_email


@pytest_asyncio.fixture
async def node_play_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    core_engine = create_async_engine(os.environ["DATABASE_URL"])
    core_sessions = async_sessionmaker(core_engine, expire_on_commit=False)
    monkeypatch.setattr(core_database, "_get_session_factory", lambda: core_sessions)
    protocol_id = experiment_id = dataset_id = None
    raw = b"question,reference\nfirst,gold-a\nsecond,gold-b\nthird,gold-c\n"
    raw_path = tmp_path / "node-play.csv"
    raw_path.write_bytes(raw)
    dataset_name = f"node-play-{uuid.uuid4()}"
    graph: dict = {
        "nodes": [
            {"id": "source", "type": "dataset", "data": {"config": {"dataset_name": dataset_name}}},
            {"id": "agent", "type": "agent", "data": {"config": {"name": "prediction"}}},
            {"id": "script", "type": "script", "data": {"config": {
                "name": "inspect-row",
                "code": "import csv, json\nfrom asaree.script_context import training_input\n"
                       "item = training_input()\nprint(json.dumps(list(csv.DictReader(item.path.open()))))\n",
            }}},
            {"id": "model", "type": "model_openai", "data": {"config": {}}},
            {"id": "upstream", "type": "agent", "data": {"config": {"name": "upstream"}}},
            {"id": "upstream-model", "type": "model_openai", "data": {"config": {}}},
            {"id": "unbound", "type": "agent", "data": {"config": {"name": "unbound"}}},
            {"id": "unbound-model", "type": "model_openai", "data": {"config": {}}},
        ],
        "edges": [
            {"id": "dataset-agent", "source": "source", "target": "agent", "targetHandle": "dataset",
             "data": {"dataset_input": {"mode": "per_row", "columns": ["question"]}}},
            {"id": "script-agent", "source": "script", "target": "agent", "targetHandle": "tool"},
            {"id": "model-agent", "source": "model", "target": "agent", "targetHandle": "model"},
            {"id": "model-upstream", "source": "upstream-model", "target": "upstream", "targetHandle": "model"},
            {"id": "model-unbound", "source": "unbound-model", "target": "unbound", "targetHandle": "model"},
            {"id": "agent-upstream", "source": "agent", "target": "upstream"},
        ],
    }
    try:
        async with sessions.begin() as db:
            user = await get_user_by_email(db, "test@test.com")
            assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
            dataset = RegisteredDataset(
                id=uuid.uuid4(), name=dataset_name, owner_id=user.id, raw_path=str(raw_path),
                raw_sha256=hashlib.sha256(raw).hexdigest(),
                target_column="reference",
            )
            db.add(dataset)
            graph["nodes"][0]["data"]["config"]["dataset_id"] = str(dataset.id)
            experiment = await create_experiment(
                db, name=f"node-play-{uuid.uuid4()}", owner_id=user.id,
                design_spec={"factors": [], "metrics": []},
            )
            experiment.measurement_plan = {
                "metrics": [{"id": "prediction", "name": "Prediction"}],
                "producers": [{"id": "prediction-output", "producer_id": "asaree.agent_output",
                               "kind": "reported", "outputs": {"value": "prediction"},
                               "config": {"agent_node_id": "agent"}}],
                "inputs": [],
            }
            protocol = await create_protocol(
                db, name=f"node-play-{uuid.uuid4()}", owner_id=user.id,
                experiment_id=experiment.id, graph=graph,
            )
            revision = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = revision.id
            protocol_id, experiment_id, dataset_id = protocol.id, experiment.id, dataset.id
            ids = user.id, protocol.id, revision.id, experiment.id, dataset.id
        async with sessions() as db:
            user = await db.get(User, ids[0])
            protocol = await db.get(Protocol, ids[1])
            assert user is not None and protocol is not None
            # Worker sessions must commit status/node updates on successful exit.
            monkeypatch.setattr(database, "_get_session_factory", lambda: sessions)
            monkeypatch.setattr(execution, "get_session", database.get_session)
            monkeypatch.setattr(row_workspaces, "_workspace_root", lambda: tmp_path / "dataset-workspaces")
            monkeypatch.setattr(execution, "WORKSPACE_ROOT", str(tmp_path / "workspaces"))
            monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(tmp_path / "workspaces"))
            yield {"sessions": sessions, "user": user, "protocol": protocol,
                   "core_sessions": core_sessions,
                   "revision_id": ids[2], "experiment_id": ids[3], "dataset_id": ids[4],
                   "graph": graph, "tmp_path": tmp_path}
    finally:
        if protocol_id is not None:
            async with core_sessions.begin() as db:
                agents = (await db.scalars(select(Agent).where(
                    Agent.owner_id == ids[0], Agent.name.like(f"protocol-{protocol_id}-%"),
                ))).all()
                for agent in agents:
                    await db.delete(agent)
        if protocol_id is not None:
            async with sessions.begin() as db:
                await delete_protocol(db, protocol_id)
                if experiment_id is not None:
                    await delete_experiment(db, experiment_id)
                if dataset_id is not None:
                    dataset = await db.get(RegisteredDataset, dataset_id)
                    if dataset is not None:
                        await db.delete(dataset)
        await engine.dispose()
        await core_engine.dispose()


@pytest_asyncio.fixture
async def node_play_client(node_play_context):
    ctx = node_play_context
    app = FastAPI()
    app.include_router(protocol_api.router)

    async def current_user():
        return ctx["user"]

    async def session():
        async with ctx["sessions"]() as db:
            yield db

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[database.get_db] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize(("selection", "row_index", "question"), [(2, 2, "third"), (None, 0, "first")])
async def test_node_play_selects_one_row_and_runs_only_target_with_private_script_input(
    node_play_context, node_play_client, monkeypatch: pytest.MonkeyPatch, selection, row_index, question,
) -> None:
    ctx = node_play_context
    queued: list[uuid.UUID] = []
    script_rows: list[list[dict]] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        async with ctx["sessions"]() as db:
            assert await db.get(ProtocolRun, run_id) is not None, "enqueue must follow commit"
        queued.append(run_id)

    async def mock_provider(*, run_id, available_tools, **_kwargs):
        from asaree.mcp_servers import script_server

        async with ctx["core_sessions"]() as db:
            run = await db.get(AgentRun, run_id)
            assert run is not None
            assert run.run_metadata["node_id"] == "agent"
            assert f'"row_index":{row_index}' in run.input and f'"question":"{question}"' in run.input
            assert "gold-" not in run.input and "reference" not in run.input
            assert "Dataset context:" not in run.input
            ambient_meta = run.run_metadata["ambient_meta"]
            assert ambient_meta["row_inputs"][0]["values"] == {"question": question}
            assert "gold-" not in json.dumps(ambient_meta)
            assert "run_wired_script" in " ".join(run.agent.tool_config_data["tool_names"])
            assert any(tool["tool_name"] == "run_wired_script" for tool in available_tools)
        metadata = {f"motoro.ambient.{key}": value for key, value in ambient_meta.items()}
        metadata["motoro.workspace_id"] = f"_protocol_runs/{queued[-1]}"
        result = json.loads(script_server.run_wired_script(ctx=SimpleNamespace(
            request_context=SimpleNamespace(meta=SimpleNamespace(model_extra=metadata))
        )))
        assert result["exit_code"] == 0, result
        script_rows.append(json.loads(result["stdout"]))
        async with ctx["core_sessions"].begin() as db:
            run = await db.get(AgentRun, run_id)
            run.output = "predicted"
            run.status = RunStatus.COMPLETED

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    monkeypatch.setattr(execution, "hydrate_registry", lambda: _done())
    monkeypatch.setattr(execution, "execute_run", mock_provider)
    # Supply descriptors locally; never connect a real MCP fleet.
    registry = SimpleNamespace(get_all_tools=lambda **_kwargs: [{
        "name": f"{execution.SCRIPT_SERVER_NAME}.run_wired_script", "tool_name": "run_wired_script",
        "server": execution.SCRIPT_SERVER_NAME, "description": "Run the wired Script", "input_schema": {},
    }])
    monkeypatch.setattr(run_tools, "get_registry", lambda: registry)
    monkeypatch.setattr(execution, "get_registry", lambda: registry)
    async with ctx["sessions"]() as db:
        protocol = await db.get(Protocol, ctx["protocol"].id)
        assert protocol is not None
        # Full Test Run requires one sequential chain; node Play separately
        # permits an unwired Agent without executing it.
        unwired_ids = {"unbound", "unbound-model"}
        protocol.graph = {
            "nodes": [node for node in ctx["graph"]["nodes"] if node["id"] not in unwired_ids],
            "edges": [edge for edge in ctx["graph"]["edges"] if edge["target"] not in unwired_ids],
        }
        revision = await publish_protocol(db, protocol, owner_id=ctx["user"].id)
        protocol.published_revision_id = revision.id
        await db.commit()
        latest = await protocol_api.create_test_run_endpoint(ctx["protocol"].id, ctx["user"], db)
        replicate = await upsert_replicate(
            db, experiment_id=ctx["experiment_id"], replicate_label="production-slot",
            fields={"factor_values": {}},
        )
        dataset = await db.get(RegisteredDataset, ctx["dataset_id"])
        slot = await ensure_row_result(
            db, experiment_id=ctx["experiment_id"], design_revision_id=replicate.design_revision_id,
            protocol_revision_id=revision.id, replicate_result_id=replicate.id,
            dataset_id=dataset.id, raw_sha256=dataset.raw_sha256, row_index=0,
        )
        slot_id = slot.id
        slot_before = {column.name: getattr(slot, column.name) for column in slot.__table__.columns}
        protocol.graph = ctx["graph"]
        revision = await publish_protocol(db, protocol, owner_id=ctx["user"].id)
        protocol.published_revision_id = revision.id
        await db.commit()
    request_kwargs = {} if selection is None else {"json": {"row_index": selection}}
    response = await node_play_client.post(f"/protocols/{ctx['protocol'].id}/nodes/agent/run", **request_kwargs)
    assert response.status_code == 201, response.text
    selected = protocol_api.ProtocolRunResponse.model_validate(response.json())
    assert selected.dataset_row == {
        "dataset_id": str(ctx["dataset_id"]),
        "raw_sha256": hashlib.sha256((ctx["tmp_path"] / "node-play.csv").read_bytes()).hexdigest(),
        "row_index": row_index,
        "columns": ["question"],
        "values": {"question": question},
    }
    assert selected.row_result_id is None and selected.target_node_id == "agent"
    assert selected.replicate_result_id is None and selected.replicate_label is None
    assert queued == [latest.id, selected.id]
    await execution.run_protocol(selected.id)
    async with ctx["sessions"]() as db:
        run = await db.get(ProtocolRun, selected.id)
        experiment = await db.get(ResearchExperiment, ctx["experiment_id"])
        assert run is not None and run.status == "completed"
        assert run.dataset_row == selected.dataset_row and run.row_result_id is None
        assert run.node_runs.keys() == {"agent"}
        assert experiment is not None and experiment.latest_test_run_id == latest.id
        assert (
            await db.scalar(
                select(func.count())
                .select_from(FactorialRowResult)
                .where(FactorialRowResult.dataset_id == ctx["dataset_id"])
            )
        ) == 1
        slot = await db.get(FactorialRowResult, slot_id)
        assert {column.name: getattr(slot, column.name) for column in slot.__table__.columns} == slot_before
        assert run.attempt_result["measurement"]["observations"][0]["value"] == "predicted"
    assert script_rows == [[{"question": question}]]


async def _done() -> None:
    return None


@pytest.mark.asyncio
async def test_node_play_defaults_to_zero_and_rejects_invalid_or_ineligible_selection(
    node_play_context, node_play_client, monkeypatch,
):
    ctx = node_play_context
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    async with ctx["sessions"]() as db:
        default = await protocol_api.run_single_node_endpoint(ctx["protocol"].id, "agent", ctx["user"], db)
        assert default.dataset_row["row_index"] == 0
        for selection in (3, -1, True, 1.5, "2"):
            invalid = await node_play_client.post(
                f"/protocols/{ctx['protocol'].id}/nodes/agent/run", json={"row_index": selection},
            )
            assert invalid.status_code == 422, invalid.text
        before = await db.scalar(
            select(func.count()).select_from(ProtocolRun).where(ProtocolRun.protocol_id == ctx["protocol"].id)
        )
        upstream = await node_play_client.post(f"/protocols/{ctx['protocol'].id}/nodes/upstream/run")
        assert upstream.status_code == 422 and "upstream input" in upstream.json()["detail"]
        assert (
            await db.scalar(
                select(func.count()).select_from(ProtocolRun).where(ProtocolRun.protocol_id == ctx["protocol"].id)
            )
        ) == before == 1
        assert queued == [default.id]


@pytest.mark.asyncio
async def test_row_selection_without_target_driver_or_in_whole_mode_is_rejected(
    node_play_context, node_play_client, monkeypatch,
):
    ctx = node_play_context

    async def enqueue(_run_id: uuid.UUID) -> None:
        pytest.fail("invalid node Play must not enqueue")

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    no_binding = await node_play_client.post(
        f"/protocols/{ctx['protocol'].id}/nodes/unbound/run", json={"row_index": 0},
    )
    assert no_binding.status_code == 422 and no_binding.json()["detail"] == "no_row_driver"
    async with ctx["sessions"]() as db:
        protocol = await db.get(Protocol, ctx["protocol"].id)
        assert protocol is not None
        protocol.graph = {"nodes": [
            {"id": "agent", "type": "agent", "data": {}},
            {"id": "model", "type": "model_openai", "data": {"config": {}}},
        ], "edges": [{"id": "model-agent", "source": "model", "target": "agent", "targetHandle": "model"}]}
        revision = await publish_protocol(db, protocol, owner_id=ctx["user"].id)
        protocol.published_revision_id = revision.id
        await db.commit()
        whole = await node_play_client.post(
            f"/protocols/{ctx['protocol'].id}/nodes/agent/run", json={"row_index": 0},
        )
        assert whole.status_code == 422 and whole.json()["detail"] == "no_row_driver"
        assert await db.scalar(
            select(func.count()).select_from(ProtocolRun).where(ProtocolRun.protocol_id == protocol.id)
        ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_source", ["columns", "changed", "missing", "other_owner"])
async def test_node_play_validates_source_before_creating_run(
    node_play_context, node_play_client, monkeypatch, invalid_source,
):
    ctx = node_play_context

    async def enqueue(_run_id):
        pytest.fail("invalid source must not enqueue")

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    async with ctx["sessions"].begin() as db:
        if invalid_source == "columns":
            # Publication validates columns too. Change the registered source
            # afterward so node Play must revalidate the published binding.
            raw = b"replacement,reference\nfirst,gold-a\nsecond,gold-b\nthird,gold-c\n"
            (ctx["tmp_path"] / "node-play.csv").write_bytes(raw)
            dataset = await db.get(RegisteredDataset, ctx["dataset_id"])
            dataset.raw_sha256 = hashlib.sha256(raw).hexdigest()
        elif invalid_source == "other_owner":
            other = await get_user_by_email(db, "other@test.com")
            assert other is not None, "run scripts/seed_row_test_users.py before row database tests"
            dataset = await db.get(RegisteredDataset, ctx["dataset_id"])
            dataset.owner_id = other.id
        elif invalid_source == "changed":
            (ctx["tmp_path"] / "node-play.csv").write_text("question,reference\nchanged,private\n")
        else:
            (ctx["tmp_path"] / "node-play.csv").unlink()
    response = await node_play_client.post(
        f"/protocols/{ctx['protocol'].id}/nodes/agent/run", json={"row_index": 0},
    )
    assert response.status_code == 422, response.text
    if invalid_source == "columns":
        assert "dataset_row.invalid_columns" in response.json()["detail"]
    async with ctx["sessions"]() as db:
        assert await db.scalar(
            select(func.count()).select_from(ProtocolRun).where(ProtocolRun.protocol_id == ctx["protocol"].id)
        ) == 0
