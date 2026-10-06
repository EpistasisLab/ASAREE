"""PostgreSQL integration coverage for a complete row-scoped protocol attempt."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from motoro.runner import get_run
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import asaree.services.protocol_execution as execution
from asaree.models.dataset import RegisteredDataset
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol_run import ProtocolRun
from asaree.services.experiments import create_experiment
from asaree.services.factorial_cells import upsert_replicate
from asaree.services.factorial_row_results import claim_row_attempt, ensure_row_result
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocols import create_protocol
from asaree.services.users import get_user_by_email


@pytest_asyncio.fixture
async def row_protocol(tmp_path: Path):
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as db:
            user = await get_user_by_email(db, "test@test.com")
            assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
            source_path = tmp_path / "protocol-source.csv"
            content = b"prompt,reference\nfirst,gold-a\nsecond,gold-b\n"
            source_path.write_bytes(content)
            dataset = RegisteredDataset(
                id=uuid.uuid4(), name=f"row-executor-{uuid.uuid4()}", owner_id=user.id,
                raw_path=str(source_path), raw_sha256=hashlib.sha256(content).hexdigest(),
                target_column="reference",
            )
            db.add(dataset)
            experiment = await create_experiment(
                db, name=f"row-executor-{uuid.uuid4()}", owner_id=user.id,
                design_spec={"factors": [], "metrics": []},
            )
            replicate = await upsert_replicate(
                db, experiment_id=experiment.id, replicate_label="row-executor-cell",
                fields={"factor_values": {}},
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
                    {"id": "model-first-edge", "source": "model-first", "target": "first", "targetHandle": "model"},
                    {"id": "first-second", "source": "first", "target": "second"},
                    {"id": "model-second-edge", "source": "model-second", "target": "second", "targetHandle": "model"},
                ],
            }
            protocol = await create_protocol(
                db, name=f"row-executor-{uuid.uuid4()}", owner_id=user.id,
                experiment_id=experiment.id, graph=graph,
            )
            revision = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = revision.id
            row = execution.project_row(
                execution.read_row_source(
                    dataset_id=str(dataset.id), raw_path=str(source_path), raw_sha256=dataset.raw_sha256,
                ), row_index=0, columns=["prompt", "reference"],
            )
            # Production row creation validates and claims the stable row slot.
            slot = await ensure_row_result(
                db, experiment_id=experiment.id, design_revision_id=replicate.design_revision_id,
                protocol_revision_id=revision.id, replicate_result_id=replicate.id,
                dataset_id=dataset.id, raw_sha256=dataset.raw_sha256, row_index=0,
            )
            run = await claim_row_attempt(
                db, row_result_id=slot.id, expected_run_id=None,
                create_kwargs={"protocol_id": protocol.id, "owner_id": user.id,
                               "replicate_label": replicate.replicate_label, "factor_values": {},
                               "replicate_result_id": replicate.id, "design_revision_id": replicate.design_revision_id,
                               "protocol_revision_id": revision.id, "dataset_row": row},
            )
            assert run is not None
            ids = (run.id,)
        yield sessions, ids[0]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_complete_protocol_uses_fresh_row_context_without_parent_writeback(row_protocol, monkeypatch):
    sessions, run_id = row_protocol
    calls = 0

    async def execute_run_mock(**_kwargs):
        nonlocal calls
        calls += 1

    async def no_hydration():
        return None

    monkeypatch.setattr(execution, "execute_run", execute_run_mock)
    monkeypatch.setattr(execution, "hydrate_registry", no_hydration)
    monkeypatch.setattr(execution, "get_registry", lambda: None)

    await execution.run_protocol(run_id)

    async with sessions() as db:
        run = await db.get(ProtocolRun, run_id)
        assert run is not None and run.status == "completed"
        assert set(run.node_runs) >= {"first", "second"}
        assert calls == 2
        replicate = await db.get(FactorialReplicateResult, run.replicate_result_id)
        slot = await db.get(FactorialRowResult, run.row_result_id)
        assert replicate is not None and replicate.workspace_id is None and replicate.metric_values is None
        assert slot is not None and slot.workspace_id == f"_protocol_runs/{run_id}"
        motoro_runs = [await get_run(uuid.UUID(run.node_runs[node]["run_id"])) for node in ("first", "second")]
        assert all(item is not None for item in motoro_runs)
        contexts = [item.run_metadata["ambient_meta"] for item in motoro_runs]
        assert all(item["dataset_mode"] == "per_row" for item in contexts)
        assert contexts[0]["row_inputs"][0]["values"] == {"prompt": "first", "reference": "gold-a"}
        assert contexts[1]["row_inputs"] == []


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_create_another_row_execution(row_protocol, monkeypatch):
    _sessions, run_id = row_protocol
    calls = 0

    async def execute_run_mock(**_kwargs):
        nonlocal calls
        calls += 1

    async def no_hydration():
        return None

    monkeypatch.setattr(execution, "execute_run", execute_run_mock)
    monkeypatch.setattr(execution, "hydrate_registry", no_hydration)
    monkeypatch.setattr(execution, "get_registry", lambda: None)

    await execution.run_protocol(run_id)
    first_delivery_calls = calls
    await execution.run_protocol(run_id)

    assert first_delivery_calls == 2
    assert calls == first_delivery_calls


@pytest.mark.asyncio
async def test_changed_original_source_fails_before_provider_execution(row_protocol, monkeypatch):
    sessions, run_id = row_protocol
    calls = 0

    async def execute_run_mock(**_kwargs):
        nonlocal calls
        calls += 1

    async def no_hydration():
        return None

    monkeypatch.setattr(execution, "execute_run", execute_run_mock)
    monkeypatch.setattr(execution, "hydrate_registry", no_hydration)
    monkeypatch.setattr(execution, "get_registry", lambda: None)

    async with sessions.begin() as db:
        run = await db.get(ProtocolRun, run_id)
        assert run is not None
        registration = await db.get(RegisteredDataset, run.dataset_row["dataset_id"])
        assert registration is not None
        changed_path = Path(registration.raw_path).with_name("changed-source.csv")
        changed_bytes = b"prompt,reference\nchanged,gold-a\nsecond,gold-b\n"
        changed_path.write_bytes(changed_bytes)
        registration.raw_path = str(changed_path)
        registration.raw_sha256 = hashlib.sha256(changed_bytes).hexdigest()

    await execution.run_protocol(run_id)

    async with sessions() as db:
        run = await db.get(ProtocolRun, run_id)
        assert run is not None and run.status == "failed"
        assert "Row source validation failed" in (run.error or "")
    assert calls == 0
