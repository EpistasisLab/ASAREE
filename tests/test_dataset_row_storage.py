"""PostgreSQL persistence tests for stable dataset-row slots."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from asaree.api import datasets as datasets_api
from asaree.deps import get_current_user
from asaree.models.database import get_db
from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.user import User
from asaree.services.users import create_user, get_user_by_email, set_password


@pytest_asyncio.fixture
async def storage_graph() -> AsyncIterator[tuple[AsyncSession, dict[str, uuid.UUID]]]:
    url = os.environ["ASAREE_PRODUCT_DATABASE_URL"]
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids = {
        name: uuid.uuid4()
        for name in ("user", "dataset", "experiment", "design", "cell", "replicate", "protocol", "protocol_revision")
    }
    async with sessions() as db:
        user = await get_user_by_email(db, "test@test.com")
        if user is None:
            user = await create_user(db, email="test@test.com", password="Test1234", display_name="Row test")
        else:
            await set_password(db, user, new_password="Test1234")
            user.display_name = "Row test"
            user.is_active = True
        ids["user"] = user.id
        db.add_all(
            [
                RegisteredDataset(id=ids["dataset"], name=f"row-{ids['dataset']}", owner_id=ids["user"]),
                ResearchExperiment(id=ids["experiment"], name=f"row-{ids['experiment']}", owner_id=ids["user"]),
                Protocol(id=ids["protocol"], name=f"row-{ids['protocol']}", owner_id=ids["user"], graph={}),
            ]
        )
        await db.flush()
        db.add_all(
            [
                ExperimentDesignRevision(id=ids["design"], experiment_id=ids["experiment"], revision=1),
                ProtocolRevision(
                    id=ids["protocol_revision"],
                    protocol_id=ids["protocol"],
                    revision=1,
                    graph={},
                    published_at=datetime.now(UTC),
                ),
            ]
        )
        await db.flush()
        db.add(
            FactorialCell(
                id=ids["cell"],
                experiment_id=ids["experiment"],
                design_revision_id=ids["design"],
                cell_label="cell-1",
                factor_values={},
            )
        )
        await db.flush()
        replicate = FactorialReplicateResult(
            id=ids["replicate"], cell_id=ids["cell"], replicate_number=1, replicate_label="replicate-1"
        )
        db.add(replicate)
        await db.flush()
        yield db, ids
        await db.rollback()
    await engine.dispose()


def _slot(ids: dict[str, uuid.UUID], row_index: int = 0) -> FactorialRowResult:
    return FactorialRowResult(
        replicate_result_id=ids["replicate"],
        protocol_revision_id=ids["protocol_revision"],
        dataset_id=ids["dataset"],
        raw_sha256="a" * 64,
        row_index=row_index,
    )


async def test_composite_identity_and_duplicate_valued_positions(storage_graph) -> None:
    db, ids = storage_graph
    first = _slot(ids, 0)
    second = _slot(ids, 1)
    db.add_all([first, second])
    await db.flush()
    assert first.id != second.id

    db.add(_slot(ids, 0))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_negative_row_index_is_rejected(storage_graph) -> None:
    db, ids = storage_graph
    db.add(_slot(ids, -1))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_legacy_run_nullable_fields_and_row_slot_cascade(storage_graph) -> None:
    db, ids = storage_graph
    legacy = ProtocolRun(protocol_id=ids["protocol"], owner_id=ids["user"], node_runs={})
    preview = ProtocolRun(
        protocol_id=ids["protocol"],
        owner_id=ids["user"],
        node_runs={},
        dataset_row={"dataset_id": str(ids["dataset"]), "raw_sha256": "a" * 64, "row_index": 0},
    )
    slot = _slot(ids)
    db.add_all([legacy, preview, slot])
    await db.flush()
    assert legacy.row_result_id is None and legacy.dataset_row is None
    assert preview.row_result_id is None and preview.dataset_row is not None
    linked = ProtocolRun(
        protocol_id=ids["protocol"],
        owner_id=ids["user"],
        node_runs={},
        row_result_id=slot.id,
        dataset_row={"dataset_id": str(ids["dataset"]), "raw_sha256": "a" * 64, "row_index": 0},
    )
    db.add(linked)
    await db.flush()
    await db.delete(slot)
    await db.flush()
    await db.refresh(linked)
    assert linked.row_result_id is None
    assert linked.id is not None


async def test_replicate_deletion_cascades_slots_and_nulls_run_link(storage_graph) -> None:
    db, ids = storage_graph
    slot = _slot(ids)
    run = ProtocolRun(
        protocol_id=ids["protocol"], owner_id=ids["user"], node_runs={}, row_result_id=slot.id
    )
    db.add_all([slot, run])
    await db.flush()
    await db.execute(delete(FactorialReplicateResult).where(FactorialReplicateResult.id == ids["replicate"]))
    await db.flush()
    await db.refresh(run)
    assert run.row_result_id is None
    assert await db.scalar(select(FactorialRowResult.id).where(FactorialRowResult.id == slot.id)) is None



async def test_design_deletion_cascades_slots_and_nulls_run_link(storage_graph) -> None:
    db, ids = storage_graph
    slot = _slot(ids)
    run = ProtocolRun(
        protocol_id=ids["protocol"], owner_id=ids["user"], node_runs={}, row_result_id=slot.id
    )
    db.add_all([slot, run])
    await db.flush()
    await db.execute(delete(ExperimentDesignRevision).where(ExperimentDesignRevision.id == ids["design"]))
    await db.flush()
    await db.refresh(run)
    assert run.row_result_id is None
    assert run.id is not None


async def test_protocol_revision_cascade_and_dataset_restrict(storage_graph) -> None:
    db, ids = storage_graph
    slot = _slot(ids)
    db.add(slot)
    await db.flush()
    await db.execute(delete(ProtocolRevision).where(ProtocolRevision.id == ids["protocol_revision"]))
    await db.flush()
    assert await db.scalar(select(FactorialRowResult.id).where(FactorialRowResult.id == slot.id)) is None

    # Recreate a revision and slot to verify dataset RESTRICT independently.
    revision = ProtocolRevision(
        id=uuid.uuid4(), protocol_id=ids["protocol"], revision=2, graph={}, published_at=datetime.now(UTC)
    )
    db.add(revision)
    await db.flush()
    retained = FactorialRowResult(
        replicate_result_id=ids["replicate"],
        protocol_revision_id=revision.id,
        dataset_id=ids["dataset"],
        raw_sha256="b" * 64,
        row_index=0,
    )
    db.add(retained)
    await db.flush()
    app = FastAPI()
    app.include_router(datasets_api.router, prefix="/api")

    async def current_user() -> User:
        return await db.get(User, ids["user"])

    async def db_session():
        yield db

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = db_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.delete(f"/api/datasets/{ids['dataset']}")
    assert response.status_code == 409
    assert response.json()["detail"] == "Dataset is referenced by stored row results and cannot be deleted"
