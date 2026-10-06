"""PostgreSQL integration coverage for explicitly retrying row slots."""

from __future__ import annotations

import asyncio
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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import asaree.api.protocols as protocol_api
from asaree.api.protocols import CellRunBatchRequest, create_cell_runs_endpoint
from asaree.models.dataset import RegisteredDataset
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_run import ProtocolRun
from asaree.services.experiments import create_experiment
from asaree.services.factorial_cells import upsert_replicate
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocols import create_protocol
from asaree.services.users import get_user_by_email


@pytest_asyncio.fixture
async def retry_setup(tmp_path: Path) -> AsyncIterator[dict]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as db:
            user = await get_user_by_email(db, "test@test.com")
            assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
            raw = b"question,answer\n" + b"prompt,response\n" * 30
            raw_path = tmp_path / "retry.csv"
            raw_path.write_bytes(raw)
            dataset_id = uuid.uuid4()
            dataset = RegisteredDataset(
                id=dataset_id,
                name=f"retry-{dataset_id}",
                owner_id=user.id,
                raw_path=str(raw_path),
                raw_sha256=hashlib.sha256(raw).hexdigest(),
            )
            db.add(dataset)
            experiment = await create_experiment(
                db,
                name=f"row-retry-{uuid.uuid4()}",
                owner_id=user.id,
                design_spec={"factors": [], "metrics": []},
            )
            parent = await upsert_replicate(
                db,
                experiment_id=experiment.id,
                replicate_label="retry-parent",
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
                    {"id": "model-agent", "source": "model", "target": "agent", "targetHandle": "model"},
                ],
            }
            protocol = await create_protocol(
                db,
                name=f"row-retry-{uuid.uuid4()}",
                owner_id=user.id,
                experiment_id=experiment.id,
                graph=graph,
            )
            revision = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = revision.id
            identifiers = user.id, protocol.id, revision.id, parent.id
        async with sessions() as db:
            user = await get_user_by_email(db, "test@test.com")
            assert user is not None and user.id == identifiers[0]
            protocol = await db.get(Protocol, identifiers[1])
            parent = await db.get(FactorialReplicateResult, identifiers[3])
            assert protocol is not None and parent is not None
            yield {
                "engine": engine,
                "sessions": sessions,
                "db": db,
                "user": user,
                "protocol": protocol,
                "revision_id": identifiers[2],
                "revision_graph": deepcopy(protocol.graph),
                "parent": parent,
                "dataset": dataset,
                "raw_path": raw_path,
            }
    finally:
        await engine.dispose()


async def _enqueue_rows(ctx: dict, monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    result = await create_cell_runs_endpoint(ctx["protocol"].id, ctx["user"], ctx["db"])
    assert len(result.protocol_run_ids) == len(result.row_result_ids) == 30
    return result.row_result_ids


async def _latest(ctx: dict, slot_id: uuid.UUID) -> ProtocolRun:
    slot = await ctx["db"].get(FactorialRowResult, slot_id)
    assert slot is not None and slot.run_id is not None
    run = await ctx["db"].get(ProtocolRun, slot.run_id)
    assert run is not None
    return run


@pytest.mark.asyncio
async def test_failed_and_cancelled_slots_retry_as_new_attempts(retry_setup, monkeypatch) -> None:
    ctx = retry_setup
    slot_ids = await _enqueue_rows(ctx, monkeypatch)
    previous = [await _latest(ctx, slot_id) for slot_id in slot_ids[:2]]
    previous[0].status = "failed"
    previous[1].status = "cancelled"
    previous[0].attempt_result = {"immutable": "failed facts"}
    previous[1].attempt_result = {"immutable": "cancelled facts"}
    parent_before = await ctx["db"].get(FactorialReplicateResult, ctx["parent"].id)
    parent_run_id = parent_before.run_id if parent_before else None

    result = await create_cell_runs_endpoint(
        ctx["protocol"].id,
        ctx["user"],
        ctx["db"],
        CellRunBatchRequest(retry_row_result_ids=slot_ids[:2]),
    )
    assert len(result.protocol_run_ids) == 2
    assert result.row_result_ids == slot_ids[:2]
    assert result.replicate_labels == ["retry-parent", "retry-parent"]
    assert result.skipped == 0
    attempts = [await ctx["db"].get(ProtocolRun, run_id) for run_id in result.protocol_run_ids]
    assert all(run is not None for run in attempts)
    for old, new in zip(previous, attempts, strict=True):
        assert new is not None
        assert new.row_result_id == old.row_result_id
        assert new.replicate_result_id == old.replicate_result_id
        assert new.replicate_label == old.replicate_label
        assert new.replicate_label == "retry-parent"
        assert new.factor_values == old.factor_values == {}
        assert new.design_revision_id == old.design_revision_id
        assert new.protocol_revision_id == old.protocol_revision_id
        assert new.dataset_row == old.dataset_row
        assert old.attempt_result in ({"immutable": "failed facts"}, {"immutable": "cancelled facts"})
    assert (await ctx["db"].get(FactorialReplicateResult, ctx["parent"].id)).run_id == parent_run_id
    assert await ctx["db"].scalar(
        select(func.count()).select_from(ProtocolRun).where(ProtocolRun.row_result_id.in_(slot_ids[:2]))
    ) == 4


@pytest.mark.asyncio
async def test_retry_one_of_thirty_adds_one_attempt_without_parent_mutation(retry_setup, monkeypatch) -> None:
    ctx = retry_setup
    slot_ids = await _enqueue_rows(ctx, monkeypatch)
    previous = await _latest(ctx, slot_ids[0])
    previous.status = "failed"
    previous.attempt_result = {"error": "original"}
    row_before = await ctx["db"].get(FactorialRowResult, slot_ids[0])
    row_before.workspace_id = "previous-attempt-workspace"
    parent = await ctx["db"].get(FactorialReplicateResult, ctx["parent"].id)
    parent_snapshot = (parent.run_id, parent.metric_values, parent.artifacts, parent.replicate_number)

    result = await create_cell_runs_endpoint(
        ctx["protocol"].id,
        ctx["user"],
        ctx["db"],
        CellRunBatchRequest(retry_row_result_ids=[slot_ids[0]]),
    )
    assert len(result.protocol_run_ids) == 1
    assert result.row_result_ids == [slot_ids[0]]
    assert await ctx["db"].scalar(
        select(func.count()).select_from(ProtocolRun).where(
            ProtocolRun.protocol_id == ctx["protocol"].id,
            ProtocolRun.is_test_run.is_(False),
        )
    ) == 31
    parent = await ctx["db"].get(FactorialReplicateResult, ctx["parent"].id)
    assert (parent.run_id, parent.metric_values, parent.artifacts, parent.replicate_number) == parent_snapshot
    row = await ctx["db"].get(FactorialRowResult, slot_ids[0])
    assert row is not None and row.run_id == result.protocol_run_ids[0]
    assert row.workspace_id is None
    assert previous.attempt_result == {"error": "original"}


@pytest.mark.asyncio
async def test_success_unavailable_active_and_duplicate_targets_reject(retry_setup, monkeypatch) -> None:
    ctx = retry_setup
    slot_ids = await _enqueue_rows(ctx, monkeypatch)
    statuses = ["completed", "completed", "pending", "running", "finalizing"]
    for slot_id, status in zip(slot_ids[:5], statuses, strict=True):
        run = await _latest(ctx, slot_id)
        run.status = status
    unavailable = await _latest(ctx, slot_ids[1])
    unavailable.attempt_result = {"measurement": {"observations": [{"status": "unavailable"}]}}
    for selected in ([slot_ids[0]], [slot_ids[1]], [slot_ids[2]], [slot_ids[3]], [slot_ids[4]]):
        with pytest.raises(HTTPException) as exc:
            await create_cell_runs_endpoint(
                ctx["protocol"].id,
                ctx["user"],
                ctx["db"],
                CellRunBatchRequest(retry_row_result_ids=selected),
            )
        assert exc.value.status_code == 422
        assert exc.value.detail == "invalid_retry_target"
    with pytest.raises(HTTPException) as exc:
        await create_cell_runs_endpoint(
            ctx["protocol"].id,
            ctx["user"],
            ctx["db"],
            CellRunBatchRequest(retry_row_result_ids=[slot_ids[0], slot_ids[0]]),
        )
    assert exc.value.status_code == 422 and exc.value.detail == "invalid_retry_selection"

    for body in (
        CellRunBatchRequest(retry_row_result_ids=[]),
        CellRunBatchRequest(retry_row_result_ids=[slot_ids[5]], replicate_labels=[]),
        CellRunBatchRequest(retry_row_result_ids=[slot_ids[5]], rerun_replicate_labels=["retry-parent"]),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_cell_runs_endpoint(ctx["protocol"].id, ctx["user"], ctx["db"], body)
        assert exc.value.status_code == 422 and exc.value.detail == "invalid_retry_selection"


@pytest.mark.asyncio
async def test_mixed_valid_and_invalid_targets_are_atomic(retry_setup, monkeypatch) -> None:
    ctx = retry_setup
    slot_ids = await _enqueue_rows(ctx, monkeypatch)
    first, second = [await _latest(ctx, slot_id) for slot_id in slot_ids[:2]]
    first.status = "failed"
    second.status = "completed"
    before = await ctx["db"].scalar(select(func.count()).select_from(ProtocolRun))
    with pytest.raises(HTTPException) as exc:
        await create_cell_runs_endpoint(
            ctx["protocol"].id,
            ctx["user"],
            ctx["db"],
            CellRunBatchRequest(retry_row_result_ids=slot_ids[:2]),
        )
    assert exc.value.status_code == 422 and exc.value.detail == "invalid_retry_target"
    assert await ctx["db"].scalar(select(func.count()).select_from(ProtocolRun)) == before
    assert (await ctx["db"].get(FactorialRowResult, slot_ids[0])).run_id == first.id


@pytest.mark.asyncio
async def test_foreign_historical_targets_and_changed_source_reject(retry_setup, monkeypatch) -> None:
    ctx = retry_setup
    slot_ids = await _enqueue_rows(ctx, monkeypatch)
    previous = await _latest(ctx, slot_ids[0])
    previous.status = "failed"

    other = await get_user_by_email(ctx["db"], "other@test.com")
    assert other is not None, "run scripts/seed_row_test_users.py before row database tests"
    foreign_experiment = await create_experiment(
        ctx["db"],
        name=f"foreign-row-retry-{uuid.uuid4()}",
        owner_id=other.id,
        design_spec={"factors": [], "metrics": []},
    )
    await upsert_replicate(
        ctx["db"],
        experiment_id=foreign_experiment.id,
        replicate_label="foreign-parent",
        fields={"factor_values": {}},
    )
    foreign_dataset_id = uuid.uuid4()
    ctx["db"].add(
        RegisteredDataset(
            id=foreign_dataset_id,
            name=f"foreign-{foreign_dataset_id}",
            owner_id=other.id,
            raw_path=str(ctx["raw_path"]),
            raw_sha256=ctx["dataset"].raw_sha256,
        )
    )
    foreign_graph = deepcopy(ctx["revision_graph"])
    foreign_graph["nodes"][0]["data"]["config"]["dataset_id"] = str(foreign_dataset_id)
    foreign_graph["nodes"][0]["data"]["config"]["dataset_name"] = f"foreign-{foreign_dataset_id}"
    foreign_protocol = await create_protocol(
        ctx["db"],
        name=f"foreign-row-retry-{uuid.uuid4()}",
        owner_id=other.id,
        experiment_id=foreign_experiment.id,
        graph=foreign_graph,
    )
    foreign_revision = await publish_protocol(ctx["db"], foreign_protocol, owner_id=other.id)
    foreign_protocol.published_revision_id = foreign_revision.id
    async def no_enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", no_enqueue)
    foreign_batch = await create_cell_runs_endpoint(foreign_protocol.id, other, ctx["db"])
    foreign_slot_id = foreign_batch.row_result_ids[0]

    with pytest.raises(HTTPException) as exc:
        await create_cell_runs_endpoint(
            ctx["protocol"].id,
            ctx["user"],
            ctx["db"],
            CellRunBatchRequest(retry_row_result_ids=[foreign_slot_id]),
        )
    assert exc.value.status_code == 422 and exc.value.detail == "invalid_retry_target"

    protocol = ctx["protocol"]
    protocol.graph = deepcopy(protocol.graph)
    protocol.graph["nodes"][1]["data"]["system_prompt"] = "new published graph"
    revision = await publish_protocol(ctx["db"], protocol, owner_id=ctx["user"].id)
    protocol.published_revision_id = revision.id
    with pytest.raises(HTTPException) as exc:
        await create_cell_runs_endpoint(
            protocol.id,
            ctx["user"],
            ctx["db"],
            CellRunBatchRequest(retry_row_result_ids=[slot_ids[0]]),
        )
    assert exc.value.status_code == 422 and exc.value.detail == "invalid_retry_target"

    # Republish original graph so the slot is current, then verify its pinned hash.
    protocol.graph = deepcopy(ctx["revision_graph"])
    current_again = await publish_protocol(ctx["db"], protocol, owner_id=ctx["user"].id)
    protocol.published_revision_id = current_again.id
    with pytest.raises(HTTPException) as exc:
        await create_cell_runs_endpoint(
            protocol.id,
            ctx["user"],
            ctx["db"],
            CellRunBatchRequest(retry_row_result_ids=[slot_ids[0]]),
        )
    assert exc.value.status_code == 422 and exc.value.detail == "invalid_retry_target"


@pytest.mark.asyncio
async def test_changed_bytes_report_source_hash_mismatch(retry_setup, monkeypatch) -> None:
    ctx = retry_setup
    slot_ids = await _enqueue_rows(ctx, monkeypatch)
    (await _latest(ctx, slot_ids[0])).status = "failed"
    ctx["raw_path"].write_bytes(b"question,answer\nchanged,response\n")
    with pytest.raises(HTTPException) as exc:
        await create_cell_runs_endpoint(
            ctx["protocol"].id,
            ctx["user"],
            ctx["db"],
            CellRunBatchRequest(retry_row_result_ids=[slot_ids[0]]),
        )
    assert exc.value.status_code == 422 and "source_hash_mismatch" in exc.value.detail


@pytest.mark.asyncio
async def test_simultaneous_retry_claims_at_most_one_attempt(retry_setup, monkeypatch) -> None:
    ctx = retry_setup
    slot_ids = await _enqueue_rows(ctx, monkeypatch)
    (await _latest(ctx, slot_ids[0])).status = "cancelled"
    await ctx["db"].commit()
    sessions = ctx["sessions"]

    async def retry_once():
        async with sessions() as db:
            user = await get_user_by_email(db, "test@test.com")
            protocol = await db.get(Protocol, ctx["protocol"].id)
            assert user is not None and protocol is not None
            try:
                result = await create_cell_runs_endpoint(
                    protocol.id,
                    user,
                    db,
                    CellRunBatchRequest(retry_row_result_ids=[slot_ids[0]]),
                )
                return result.protocol_run_ids
            except HTTPException as exc:
                return exc.detail

    outcomes = await asyncio.gather(retry_once(), retry_once())
    assert sum(isinstance(outcome, list) and len(outcome) == 1 for outcome in outcomes) == 1
    assert sum(outcome == "invalid_retry_target" for outcome in outcomes) == 1
    async with sessions() as check:
        assert await check.scalar(
            select(func.count()).select_from(ProtocolRun).where(ProtocolRun.row_result_id == slot_ids[0])
        ) == 2
