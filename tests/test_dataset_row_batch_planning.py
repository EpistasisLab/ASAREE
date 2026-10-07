"""PostgreSQL integration coverage for row-mode production batches."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import asaree.api.protocols as protocol_api
from asaree.api.protocols import CellRunBatchRequest, create_cell_runs_endpoint
from asaree.models.dataset import RegisteredDataset
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol_run import ProtocolRun
from asaree.services.experiments import create_experiment
from asaree.services.factorial_cells import upsert_replicate
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocols import create_protocol
from asaree.services.users import get_user_by_email


@pytest_asyncio.fixture
async def row_batch(tmp_path: Path, request: pytest.FixtureRequest) -> AsyncIterator[tuple[AsyncSession, dict]]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    async with engine.begin() as connection:
        sessions = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with sessions() as db:
            user = await get_user_by_email(db, "test@test.com")
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
            db.add(dataset)
            experiment = await create_experiment(
                db,
                name=f"row-batch-{uuid.uuid4()}",
                owner_id=user.id,
                design_spec={"factors": [], "metrics": []},
            )
            await upsert_replicate(
                db,
                experiment_id=experiment.id,
                replicate_label="batch-parent",
                fields={"factor_values": {}},
            )
            if getattr(request, "param", None) == "two_parents":
                await upsert_replicate(
                    db,
                    experiment_id=experiment.id,
                    replicate_label="second-parent",
                    fields={"factor_values": {}},
                )
            graph = {
                "nodes": [
                    {
                        "id": "dataset",
                        "type": "dataset",
                        "data": {
                            "config": {"dataset_id": str(dataset_id), "dataset_name": dataset.name}
                        },
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
                db,
                name=f"row-protocol-{uuid.uuid4()}",
                owner_id=user.id,
                experiment_id=experiment.id,
                graph=graph,
            )
            revision = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = revision.id
            await db.flush()
            yield db, {"user": user, "protocol": protocol, "revision": revision}
    await engine.dispose()


@pytest.mark.asyncio
async def test_batch_persists_each_original_row_once(row_batch, monkeypatch: pytest.MonkeyPatch) -> None:
    db, ctx = row_batch
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    first = await create_cell_runs_endpoint(
        ctx["protocol"].id, ctx["user"], db, CellRunBatchRequest()
    )
    assert len(first.protocol_run_ids) == 30
    assert len(first.row_result_ids) == 30
    assert first.replicate_labels == ["batch-parent"] * 30
    assert first.consumption_mode == "per_row"
    assert first.skipped == 0
    failed_run = await db.get(ProtocolRun, first.protocol_run_ids[0])
    completed_run = await db.get(ProtocolRun, first.protocol_run_ids[1])
    assert failed_run is not None and completed_run is not None
    failed_run.status = "failed"
    completed_run.status = "completed"
    parent = await db.scalar(
        select(FactorialReplicateResult).where(FactorialReplicateResult.replicate_label == "batch-parent")
    )
    assert parent is not None and parent.run_id is None and parent.metric_values is None

    second = await create_cell_runs_endpoint(
        ctx["protocol"].id, ctx["user"], db, CellRunBatchRequest()
    )
    assert second.protocol_run_ids == []
    assert second.row_result_ids == []
    assert second.skipped == 30
    assert len(queued) == 30
    assert await db.scalar(
        select(func.count()).select_from(ProtocolRun).where(
            ProtocolRun.protocol_id == ctx["protocol"].id,
            ProtocolRun.is_test_run.is_(False),
        )
    ) == 30


@pytest.mark.asyncio
async def test_whole_dataset_batch_response_keeps_legacy_mode(row_batch, monkeypatch: pytest.MonkeyPatch) -> None:
    db, ctx = row_batch
    async def enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    protocol = ctx["protocol"]
    protocol.graph = {
        "nodes": [
            {"id": "agent", "type": "agent", "data": {}},
            {"id": "model", "type": "model_openai", "data": {"config": {}}},
        ],
        "edges": [
            {
                "id": "model-agent",
                "source": "model",
                "target": "agent",
                "targetHandle": "model",
            }
        ],
    }
    revision = await publish_protocol(db, protocol, owner_id=ctx["user"].id)
    protocol.published_revision_id = revision.id
    # This regression checks response mode only; the row parent is still valid
    # for the existing whole-dataset planner path.
    result = await create_cell_runs_endpoint(protocol.id, ctx["user"], db)
    assert result.consumption_mode == "whole_dataset"
    assert result.row_result_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize("row_batch", ["two_parents"], indirect=True)
async def test_parent_selection_expands_every_source_row(row_batch, monkeypatch: pytest.MonkeyPatch) -> None:
    db, ctx = row_batch
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    dataset_id = uuid.UUID(
        ctx["revision"].graph["nodes"][0]["data"]["config"]["dataset_id"]
    )
    dataset = await db.get(RegisteredDataset, dataset_id)
    assert dataset is not None
    raw = b"question,answer\nq1,a1\nq2,a2\nq3,a3\n"
    Path(dataset.raw_path).write_bytes(raw)
    dataset.raw_sha256 = hashlib.sha256(raw).hexdigest()
    result = await create_cell_runs_endpoint(
        ctx["protocol"].id,
        ctx["user"],
        db,
        CellRunBatchRequest(replicate_labels=["batch-parent", "second-parent"]),
    )
    assert len(result.protocol_run_ids) == 6
    assert len(result.row_result_ids) == 6
    # Parents with identical cell labels and replicate numbers are ordered by
    # their persistent UUID, not their display labels or insertion order.
    assert sorted(result.replicate_labels) == ["batch-parent"] * 3 + ["second-parent"] * 3
    assert len(set(result.replicate_labels[:3])) == 1
    assert len(set(result.replicate_labels[3:])) == 1
    assert result.skipped == 0
    assert len(queued) == 6


@pytest.mark.asyncio
async def test_new_publication_has_separate_row_slots(row_batch, monkeypatch: pytest.MonkeyPatch) -> None:
    db, ctx = row_batch

    async def enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    protocol = ctx["protocol"]
    first = await create_cell_runs_endpoint(protocol.id, ctx["user"], db)
    protocol.graph = deepcopy(protocol.graph)
    protocol.graph["nodes"][1]["data"]["system_prompt"] = "published again"
    next_revision = await publish_protocol(db, protocol, owner_id=ctx["user"].id)
    protocol.published_revision_id = next_revision.id

    second = await create_cell_runs_endpoint(protocol.id, ctx["user"], db)
    assert first.protocol_revision_id != second.protocol_revision_id
    assert len(second.protocol_run_ids) == 30
    assert len(set(first.row_result_ids).intersection(second.row_result_ids)) == 0


@pytest.mark.asyncio
async def test_individual_row_run_and_rerun_preserve_other_rows(row_batch, monkeypatch) -> None:
    db, ctx = row_batch
    queued = []

    async def enqueue(run_id):
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    protocol = ctx["protocol"]
    selection = CellRunBatchRequest(replicate_labels=["batch-parent"], row_indices=[2])
    first = await create_cell_runs_endpoint(protocol.id, ctx["user"], db, selection)
    assert len(first.protocol_run_ids) == len(first.row_result_ids) == 1
    original = await db.get(ProtocolRun, first.protocol_run_ids[0])
    assert original.dataset_row["row_index"] == 2
    assert original.protocol_revision_id == ctx["revision"].id
    assert await db.scalar(select(func.count()).select_from(FactorialRowResult).where(
        FactorialRowResult.protocol_revision_id == ctx["revision"].id,
    )) == 1

    active = await create_cell_runs_endpoint(protocol.id, ctx["user"], db, selection)
    assert active.protocol_run_ids == [] and active.skipped == 1
    original.status = "completed"
    await db.flush()
    rerun = await create_cell_runs_endpoint(protocol.id, ctx["user"], db, CellRunBatchRequest(
        replicate_labels=["batch-parent"], rerun_replicate_labels=["batch-parent"], row_indices=[2],
    ))
    assert len(rerun.protocol_run_ids) == 1
    assert rerun.row_result_ids == first.row_result_ids
    assert rerun.protocol_run_ids != first.protocol_run_ids
    await db.refresh(original)
    assert original.status == "completed"
    remaining = await create_cell_runs_endpoint(protocol.id, ctx["user"], db, CellRunBatchRequest())
    assert len(remaining.protocol_run_ids) == 29
    assert remaining.skipped == 1
    assert first.row_result_ids[0] not in remaining.row_result_ids
    assert len(queued) == 31


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [
    {"row_indices": [0]},
    {"replicate_labels": [], "row_indices": [0]},
    {"replicate_labels": ["batch-parent"], "row_indices": []},
    {"replicate_labels": ["batch-parent"], "row_indices": [30]},
    {"replicate_labels": ["unknown"], "row_indices": [0]},
])
async def test_invalid_row_selection_creates_no_runs(row_batch, selection) -> None:
    db, ctx = row_batch
    with pytest.raises(HTTPException) as exc:
        await create_cell_runs_endpoint(ctx["protocol"].id, ctx["user"], db, CellRunBatchRequest(**selection))
    assert exc.value.status_code == 422
    assert await db.scalar(select(func.count()).select_from(ProtocolRun).where(
        ProtocolRun.protocol_id == ctx["protocol"].id,
    )) == 0
