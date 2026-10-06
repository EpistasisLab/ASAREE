"""PostgreSQL coverage for row-selected canvas Test Runs."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import asaree.api.protocols as protocol_api
import asaree.services.protocol_execution as execution
from asaree.models.dataset import RegisteredDataset
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_run import ProtocolRun
from asaree.models.user import User
from asaree.services.experiments import create_experiment, delete_experiment
from asaree.services.factorial_cells import upsert_replicate
from asaree.services.factorial_row_results import ensure_row_result
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocol_runs import create_test_run
from asaree.services.protocols import create_protocol, delete_protocol
from asaree.services.users import get_user_by_email


@pytest_asyncio.fixture
async def test_run_context(tmp_path: Path) -> AsyncIterator[dict]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    protocol_id = experiment_id = dataset_id = None
    try:
        async with sessions.begin() as db:
            user = await get_user_by_email(db, "test@test.com")
            assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
            raw = b"prompt,reference\nfirst,gold-a\nsecond,gold-b\nthird,gold-c\n"
            raw_path = tmp_path / "test-run.csv"
            raw_path.write_bytes(raw)
            dataset = RegisteredDataset(
                id=uuid.uuid4(), name=f"test-run-{uuid.uuid4()}", owner_id=user.id,
                raw_path=str(raw_path), raw_sha256=hashlib.sha256(raw).hexdigest(),
            )
            db.add(dataset)
            experiment = await create_experiment(
                db, name=f"test-run-{uuid.uuid4()}", owner_id=user.id,
                design_spec={"factors": [], "metrics": []},
            )
            graph = {
                "nodes": [
                    {"id": "source", "type": "dataset", "data": {"config": {
                        "dataset_id": str(dataset.id), "dataset_name": dataset.name,
                    }}},
                    {"id": "first", "type": "agent", "data": {"config": {"name": "first"}}},
                    {"id": "second", "type": "agent", "data": {"config": {"name": "second"}}},
                    {"id": "model-first", "type": "model_openai", "data": {"config": {}}},
                    {"id": "model-second", "type": "model_openai", "data": {"config": {}}},
                ],
                "edges": [
                    {"id": "driver-first", "source": "source", "target": "first", "targetHandle": "dataset",
                     "data": {"dataset_input": {"mode": "per_row", "columns": ["prompt", "reference"]}}},
                    {"id": "first-second", "source": "first", "target": "second"},
                    {"id": "model-first-edge", "source": "model-first", "target": "first", "targetHandle": "model"},
                    {"id": "model-second-edge", "source": "model-second", "target": "second", "targetHandle": "model"},
                ],
            }
            protocol = await create_protocol(
                db, name=f"test-run-{uuid.uuid4()}", owner_id=user.id,
                experiment_id=experiment.id, graph=graph,
            )
            revision = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = revision.id
            protocol_id, experiment_id, dataset_id = protocol.id, experiment.id, dataset.id
            context_ids = (user.id, protocol.id, revision.id, experiment.id, dataset.id)
        async with sessions() as db:
            user_id, protocol_id, revision_id, experiment_id, dataset_id = context_ids
            user = await db.get(User, user_id)
            protocol = await db.get(Protocol, protocol_id)
            assert user is not None and protocol is not None
            yield {
                "sessions": sessions,
                "user": user,
                "protocol": protocol,
                "revision_id": revision_id,
                "experiment_id": experiment_id,
                "dataset_id": dataset_id,
            }
    finally:
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


@pytest.fixture
def valid_test_run_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(protocol_api, "validate_coordination_strategy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "validate_stage_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "validate_prompt_references", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "topological_order", lambda *_args, **_kwargs: ["first", "second"])


@pytest.mark.asyncio
async def test_row_test_run_defaults_to_zero_and_selected_index_two(
    test_run_context, valid_test_run_endpoint, monkeypatch
):
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    ctx = test_run_context
    async with ctx["sessions"]() as db:
        zero = await protocol_api.create_test_run_endpoint(ctx["protocol"].id, ctx["user"], db)
        selected = await protocol_api.create_test_run_endpoint(
            ctx["protocol"].id, ctx["user"], db, protocol_api.TestRunRequest(row_index=2)
        )
        assert zero.dataset_row["row_index"] == 0
        assert selected.dataset_row["row_index"] == 2
        assert selected.dataset_row["values"] == {"prompt": "third", "reference": "gold-c"}
        assert zero.protocol_revision_id == selected.protocol_revision_id == ctx["revision_id"]
        assert len(queued) == 2
        assert await db.get(ProtocolRun, zero.id) is None
        assert await db.scalar(
            select(func.count())
            .select_from(FactorialRowResult)
            .join(FactorialReplicateResult)
            .where(FactorialReplicateResult.experiment_id == ctx["experiment_id"])
        ) == 0


@pytest.mark.asyncio
async def test_invalid_selection_preserves_latest_and_production_row_slots(
    test_run_context, valid_test_run_endpoint, monkeypatch
):
    async def enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    ctx = test_run_context
    async with ctx["sessions"]() as db:
        replicate = await upsert_replicate(
            db, experiment_id=ctx["experiment_id"], replicate_label="production-slot",
            fields={"factor_values": {}},
        )
        dataset = await db.get(RegisteredDataset, ctx["dataset_id"])
        assert dataset is not None
        slot = await ensure_row_result(
            db,
            experiment_id=ctx["experiment_id"],
            design_revision_id=replicate.design_revision_id,
            protocol_revision_id=ctx["revision_id"],
            replicate_result_id=replicate.id,
            dataset_id=dataset.id,
            raw_sha256=dataset.raw_sha256,
            row_index=0,
        )
        latest = await protocol_api.create_test_run_endpoint(ctx["protocol"].id, ctx["user"], db)
        await protocol_api.fail_protocol_run(db, latest.id, error="mocked worker failure")
        replacement = await protocol_api.create_test_run_endpoint(ctx["protocol"].id, ctx["user"], db)
        run_count = await db.scalar(select(func.count()).select_from(ProtocolRun))
        with pytest.raises(HTTPException) as invalid:
            await protocol_api.create_test_run_endpoint(
                ctx["protocol"].id, ctx["user"], db, protocol_api.TestRunRequest(row_index=3)
            )
        assert invalid.value.status_code == 422
        assert await protocol_api.get_latest_test_run_endpoint(ctx["protocol"].id, ctx["user"], db) == replacement
        assert await db.scalar(select(func.count()).select_from(ProtocolRun)) == run_count
        assert await db.get(FactorialReplicateResult, replicate.id) is not None
        assert await db.get(FactorialRowResult, slot.id) is not None


@pytest.mark.asyncio
async def test_latest_polling_returns_persisted_row_and_revision_after_draft_changes(
    test_run_context, valid_test_run_endpoint, monkeypatch
):
    async def enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    ctx = test_run_context
    async with ctx["sessions"]() as db:
        created = await protocol_api.create_test_run_endpoint(
            ctx["protocol"].id, ctx["user"], db, protocol_api.TestRunRequest(row_index=2)
        )
        run_id = created.id
        protocol = await db.get(Protocol, ctx["protocol"].id)
        assert protocol is not None
        protocol.graph = {"nodes": [], "edges": []}
        await db.commit()
    async with ctx["sessions"]() as db:
        latest = await protocol_api.get_latest_test_run_endpoint(ctx["protocol"].id, ctx["user"], db)
        assert latest.id == run_id
        assert latest.protocol_revision_id == ctx["revision_id"]
        assert latest.dataset_row["row_index"] == 2
        assert latest.dataset_row["raw_sha256"]


@pytest.mark.asyncio
async def test_two_agent_test_run_executes_full_published_protocol_without_factorial_writes(
    test_run_context, valid_test_run_endpoint, monkeypatch
):
    calls: list[str] = []
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    async def mocked_provider_execution(**kwargs) -> None:
        calls.append(str(kwargs["run_id"]))

    async def no_hydration() -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    monkeypatch.setattr(execution, "execute_run", mocked_provider_execution)
    monkeypatch.setattr(execution, "hydrate_registry", no_hydration)
    monkeypatch.setattr(execution, "get_registry", lambda: None)
    ctx = test_run_context
    async with ctx["sessions"]() as db:
        created = await protocol_api.create_test_run_endpoint(
            ctx["protocol"].id, ctx["user"], db, protocol_api.TestRunRequest(row_index=1)
        )
        run_id = created.id
    assert queued == [run_id]
    await execution.run_protocol(run_id)
    async with ctx["sessions"]() as db:
        run = await db.get(ProtocolRun, run_id)
        assert run is not None and run.status == "completed" and run.is_test_run
        assert run.protocol_revision_id == ctx["revision_id"]
        assert run.dataset_row["row_index"] == 1
        assert run.row_result_id is None
        assert run.replicate_result_id is None
        assert set(run.node_runs) >= {"first", "second"}
        assert len(calls) == 2
        assert await db.scalar(
            select(func.count()).select_from(FactorialReplicateResult).where(
                FactorialReplicateResult.experiment_id == ctx["experiment_id"]
            )
        ) == 0
        assert await db.scalar(
            select(func.count())
            .select_from(FactorialRowResult)
            .join(FactorialReplicateResult)
            .where(FactorialReplicateResult.experiment_id == ctx["experiment_id"])
        ) == 0


@pytest.mark.asyncio
async def test_whole_mode_test_run_remains_compatible(test_run_context, valid_test_run_endpoint, monkeypatch):
    async def enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    ctx = test_run_context
    async with ctx["sessions"]() as db:
        protocol = await db.get(Protocol, ctx["protocol"].id)
        assert protocol is not None
        protocol.graph = {
            "nodes": [
                {"id": "agent", "type": "agent", "data": {}},
                {"id": "model", "type": "model_openai", "data": {"config": {}}},
            ],
            "edges": [{"id": "model-agent", "source": "model", "target": "agent", "targetHandle": "model"}],
        }
        revision = await publish_protocol(db, protocol, owner_id=ctx["user"].id)
        protocol.published_revision_id = revision.id
        with pytest.raises(HTTPException) as no_driver:
            await protocol_api.create_test_run_endpoint(
                protocol.id, ctx["user"], db, protocol_api.TestRunRequest(row_index=0)
            )
        assert no_driver.value.status_code == 422 and no_driver.value.detail == "no_row_driver"
        run = await protocol_api.create_test_run_endpoint(protocol.id, ctx["user"], db)
        assert run.dataset_row is None
        assert run.protocol_revision_id == revision.id


@pytest.mark.asyncio
async def test_create_test_run_persists_snapshot_without_row_slot(test_run_context):
    ctx = test_run_context
    async with ctx["sessions"]() as db:
        snapshot = {
            "dataset_id": str(ctx["dataset_id"]),
            "raw_sha256": "a" * 64,
            "row_index": 2,
            "columns": ["prompt"],
            "values": {"prompt": "third"},
        }
        run = await create_test_run(
            db, protocol_id=ctx["protocol"].id, owner_id=ctx["user"].id,
            protocol_revision_id=ctx["revision_id"], dataset_row=snapshot,
        )
        assert run.dataset_row == snapshot
        assert run.is_test_run
        assert run.row_result_id is None and run.replicate_result_id is None
