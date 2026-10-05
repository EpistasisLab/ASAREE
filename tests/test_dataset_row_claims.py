"""PostgreSQL integration checks for serialized dataset row attempt claims."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import asaree.api.protocols as protocol_api
from asaree.api.protocols import CellRunBatchRequest, create_cell_runs_endpoint
from asaree.models.dataset import RegisteredDataset
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol_run import ProtocolRun
from asaree.services.experiments import create_experiment
from asaree.services.factorial_cells import upsert_replicate
from asaree.services.factorial_row_results import claim_row_attempt
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocols import create_protocol
from asaree.services.users import get_user_by_email


@pytest.mark.parametrize("include_row_result_id", [False, True])
async def test_claim_retry_forwards_row_result_id_once(monkeypatch, include_row_result_id) -> None:
    slot_id, previous_run_id = uuid.uuid4(), uuid.uuid4()
    slot = FactorialRowResult(id=slot_id, run_id=previous_run_id)
    previous = ProtocolRun(id=previous_run_id, status="failed")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = slot
    db.get.return_value = previous
    create_run = AsyncMock(return_value=ProtocolRun(id=uuid.uuid4(), row_result_id=slot_id))
    monkeypatch.setattr("asaree.services.protocol_runs.create_protocol_run", create_run)
    create_kwargs = {"protocol_id": uuid.uuid4(), "owner_id": uuid.uuid4()}
    if include_row_result_id:
        create_kwargs["row_result_id"] = slot_id
    original_kwargs = create_kwargs.copy()

    run = await claim_row_attempt(
        db,
        row_result_id=slot_id,
        expected_run_id=previous_run_id,
        create_kwargs=create_kwargs,
    )

    assert run is create_run.return_value
    create_run.assert_awaited_once_with(db, **{**create_kwargs, "row_result_id": slot_id})
    assert create_kwargs == original_kwargs


@pytest_asyncio.fixture
async def row_batch(tmp_path: Path) -> AsyncIterator[tuple[AsyncSession, dict]]:
    """Create row-batch setup committed for independently locking sessions."""
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as setup:
            user = await get_user_by_email(setup, "test@test.com")
            assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
            raw = b"question,answer\n" + b"prompt,response\n" * 30
            raw_path = tmp_path / "batch.csv"
            raw_path.write_bytes(raw)
            dataset_id = uuid.uuid4()
            dataset = RegisteredDataset(
                id=dataset_id,
                name=f"batch-{dataset_id}",
                owner_id=user.id,
                raw_path=str(raw_path),
                raw_sha256=hashlib.sha256(raw).hexdigest(),
            )
            setup.add(dataset)
            experiment = await create_experiment(
                setup,
                name=f"row-batch-{uuid.uuid4()}",
                owner_id=user.id,
                design_spec={"factors": [], "metrics": []},
            )
            await upsert_replicate(
                setup,
                experiment_id=experiment.id,
                replicate_label="batch-parent",
                fields={"factor_values": {}},
            )
            graph = {
                "nodes": [
                    {
                        "id": "dataset",
                        "type": "dataset",
                        "data": {"config": {"dataset_id": str(dataset_id), "dataset_name": dataset.name}},
                    },
                    {"id": "agent", "type": "agent", "data": {}},
                    {"id": "model", "type": "model_openai", "data": {"config": {}}},
                ],
                "edges": [
                    {
                        "id": "dataset-agent",
                        "source": "dataset",
                        "target": "agent",
                        "targetHandle": "dataset",
                        "data": {"dataset_input": {"mode": "per_row", "columns": ["question"]}},
                    },
                    {
                        "id": "model-agent",
                        "source": "model",
                        "target": "agent",
                        "targetHandle": "model",
                    },
                ],
            }
            protocol = await create_protocol(
                setup,
                name=f"row-protocol-{uuid.uuid4()}",
                owner_id=user.id,
                experiment_id=experiment.id,
                graph=graph,
            )
            revision = await publish_protocol(setup, protocol, owner_id=user.id)
            protocol_id, user_id, revision_id = protocol.id, user.id, revision.id

        async with sessions() as db:
            user = await db.get(type(user), user_id)
            protocol = await db.get(type(protocol), protocol_id)
            revision = await db.get(type(revision), revision_id)
            assert user is not None and protocol is not None and revision is not None
            yield db, {"user": user, "protocol": protocol, "revision": revision}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claim_waits_for_committed_claim_and_never_duplicates(row_batch) -> None:
    db, ctx = row_batch
    async def no_enqueue(_run_id: uuid.UUID) -> None:
        return None

    original_enqueue = protocol_api.enqueue_protocol_run
    protocol_api.enqueue_protocol_run = no_enqueue
    try:
        result = await create_cell_runs_endpoint(ctx["protocol"].id, ctx["user"], db, CellRunBatchRequest())
    finally:
        protocol_api.enqueue_protocol_run = original_enqueue
    slot_id = result.row_result_ids[0]
    protocol = ctx["protocol"]
    run = await db.get(ProtocolRun, result.protocol_run_ids[0])
    assert run is not None

    # The slot row lock is the production claim boundary. The second session
    # waits at that lock and observes the first transaction's latest run id.
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    locked = asyncio.Event()
    second_started = asyncio.Event()
    async with sessions() as first, sessions() as second:
        async with first.begin():
            slot = await first.scalar(
                select(FactorialRowResult).where(FactorialRowResult.id == slot_id).with_for_update()
            )
            assert slot is not None
            locked.set()

            async def competing_claim():
                async with second.begin():
                    second_started.set()
                    return await claim_row_attempt(
                        second,
                        row_result_id=slot_id,
                        expected_run_id=None,
                        create_kwargs={
                            "protocol_id": protocol.id,
                            "owner_id": ctx["user"].id,
                            "replicate_label": run.replicate_label,
                            "factor_values": run.factor_values,
                            "replicate_result_id": run.replicate_result_id,
                            "design_revision_id": run.design_revision_id,
                            "protocol_revision_id": run.protocol_revision_id,
                            "dataset_row": run.dataset_row,
                        },
                    )

            task = asyncio.create_task(competing_claim())
            await locked.wait()
            await second_started.wait()
        assert await task is None
    async with sessions() as check:
        count = await check.scalar(
            select(func.count()).select_from(ProtocolRun).where(ProtocolRun.row_result_id == slot_id)
        )
    assert count == 1
    await engine.dispose()
