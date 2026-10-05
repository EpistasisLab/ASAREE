"""PostgreSQL integration coverage for production single-row execution."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import asaree.api.protocols as protocol_api
from asaree.api.protocols import CreateProtocolRunRequest, create_protocol_run_endpoint
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
async def row_single(tmp_path: Path) -> AsyncIterator[tuple[AsyncSession, dict]]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as db:
            user = await get_user_by_email(db, "test@test.com")
            assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
            raw = b"question,answer\nq0,a0\nq1,a1\nq2,a2\n"
            raw_path = tmp_path / "single.csv"
            raw_path.write_bytes(raw)
            dataset = RegisteredDataset(
                id=uuid.uuid4(), name=f"single-{uuid.uuid4()}", owner_id=user.id,
                raw_path=str(raw_path), raw_sha256=hashlib.sha256(raw).hexdigest(),
            )
            db.add(dataset)
            experiment = await create_experiment(
                db, name=f"row-single-{uuid.uuid4()}", owner_id=user.id,
                design_spec={"factors": [], "metrics": []},
            )
            parent = await upsert_replicate(
                db, experiment_id=experiment.id, replicate_label="single-parent",
                fields={"factor_values": {}},
            )
            graph = {
                "nodes": [
                    {"id": "dataset", "type": "dataset", "data": {"config": {
                        "dataset_id": str(dataset.id), "dataset_name": dataset.name,
                    }}},
                    {"id": "agent", "type": "agent", "data": {}},
                    {"id": "model", "type": "model_openai", "data": {"config": {}}},
                ],
                "edges": [
                    {"id": "dataset-agent", "source": "dataset", "target": "agent",
                     "targetHandle": "dataset", "data": {"dataset_input": {
                         "mode": "per_row", "columns": ["question"],
                     }}},
                    {"id": "model-agent", "source": "model", "target": "agent", "targetHandle": "model"},
                ],
            }
            protocol = await create_protocol(
                db, name=f"row-single-{uuid.uuid4()}", owner_id=user.id,
                experiment_id=experiment.id, graph=graph,
            )
            revision = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = revision.id
            await db.flush()
            user_id, protocol_id, revision_id, parent_id = user.id, protocol.id, revision.id, parent.id
        async with sessions() as db:
            from asaree.models.protocol import Protocol
            from asaree.models.protocol_revision import ProtocolRevision
            from asaree.models.user import User

            user = await db.get(User, user_id)
            protocol = await db.get(Protocol, protocol_id)
            revision = await db.get(ProtocolRevision, revision_id)
            assert user is not None and protocol is not None and revision is not None
            yield db, {
                "user": user,
                "protocol": protocol,
                "revision": revision,
                "parent_id": parent_id,
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_single_run_selects_row_and_default_row_without_factorial_writes(row_single, monkeypatch):
    db, ctx = row_single
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    selected = await create_protocol_run_endpoint(
        ctx["protocol"].id, ctx["user"], db,
        CreateProtocolRunRequest(replicate_label="single-parent", row_index=2),
    )
    default = await create_protocol_run_endpoint(ctx["protocol"].id, ctx["user"], db)
    assert selected.dataset_row["row_index"] == 2
    assert default.dataset_row["row_index"] == 0
    assert selected.row_result_id != default.row_result_id
    assert selected.replicate_result_id is not None
    assert selected.replicate_label == "single-parent"
    assert selected.factor_values == {}
    assert default.replicate_result_id is None
    assert default.replicate_label is None
    assert default.factor_values is None
    for run in (selected, default):
        assert run.protocol_revision_id == ctx["revision"].id
        assert run.design_revision_id is not None
    assert len(queued) == 2
    assert await db.scalar(select(func.count()).select_from(ProtocolRun).where(
        ProtocolRun.protocol_id == ctx["protocol"].id,
    )) == 2
    parent = await db.get(FactorialReplicateResult, ctx["parent_id"])
    assert parent is not None and parent.run_id is None and parent.metric_values is None
    assert await db.scalar(select(func.count()).select_from(FactorialRowResult).where(
        FactorialRowResult.replicate_result_id == ctx["parent_id"],
    )) == 2


@pytest.mark.asyncio
async def test_attempted_slot_and_out_of_range_row_are_rejected_before_enqueue(row_single, monkeypatch):
    from fastapi import HTTPException

    db, ctx = row_single
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    request = CreateProtocolRunRequest(replicate_label="single-parent", row_index=1)
    await create_protocol_run_endpoint(ctx["protocol"].id, ctx["user"], db, request)
    with pytest.raises(HTTPException) as attempted:
        await create_protocol_run_endpoint(ctx["protocol"].id, ctx["user"], db, request)
    assert attempted.value.status_code == 422
    assert attempted.value.detail == "row_already_attempted"
    before = await db.scalar(select(func.count()).select_from(ProtocolRun).where(
        ProtocolRun.protocol_id == ctx["protocol"].id,
    ))
    with pytest.raises(HTTPException):
        await create_protocol_run_endpoint(
            ctx["protocol"].id, ctx["user"], db,
            CreateProtocolRunRequest(replicate_label="single-parent", row_index=3),
        )
    assert await db.scalar(select(func.count()).select_from(ProtocolRun).where(
        ProtocolRun.protocol_id == ctx["protocol"].id,
    )) == before == 1
    assert len(queued) == 1


@pytest.mark.asyncio
async def test_row_index_without_driver_is_rejected_before_run_creation(row_single, monkeypatch):
    from fastapi import HTTPException

    db, ctx = row_single
    queued: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> None:
        queued.append(run_id)

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", enqueue)
    protocol = ctx["protocol"]
    protocol.graph = {
        "nodes": [
            {"id": "agent", "type": "agent", "data": {}},
            {"id": "model", "type": "model_openai", "data": {"config": {}}},
        ],
        "edges": [{"id": "model-agent", "source": "model", "target": "agent", "targetHandle": "model"}],
    }
    revision = await publish_protocol(db, protocol, owner_id=ctx["user"].id)
    protocol.published_revision_id = revision.id
    before = await db.scalar(select(func.count()).select_from(ProtocolRun).where(
        ProtocolRun.protocol_id == protocol.id,
    ))
    with pytest.raises(HTTPException) as no_driver:
        await create_protocol_run_endpoint(
            protocol.id, ctx["user"], db, CreateProtocolRunRequest(row_index=0),
        )
    assert no_driver.value.status_code == 422
    assert no_driver.value.detail == "no_row_driver"
    assert await db.scalar(select(func.count()).select_from(ProtocolRun).where(
        ProtocolRun.protocol_id == protocol.id,
    )) == before == 0
    assert queued == []


def test_row_index_is_strict_and_nonnegative():
    for value in (-1, True, "2"):
        with pytest.raises(ValidationError):
            CreateProtocolRunRequest(row_index=value)
