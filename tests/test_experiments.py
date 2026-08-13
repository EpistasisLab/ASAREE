"""Tests for services.experiments' update_experiment -- covers the rename
capability just added (name/description), alongside the pre-existing
dataset_id attach/detach. Same real-Postgres, throwaway-user fixture as
tests/test_protocols.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for the FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.experiments import create_experiment, get_experiment, list_experiments, update_experiment


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"experiment-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Experiment Test User",
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        uid = user.id
    yield uid
    async with get_session() as db:
        db_user = await db.get(User, uid)
        if db_user is not None:
            await db.delete(db_user)


async def test_rename_and_set_description(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        experiment_id = experiment.id

    async with get_session() as db:
        updated = await update_experiment(
            db, experiment_id, fields={"name": "spinal-fusion-sweep", "description": "renamed from the GUI"}
        )
        assert updated is not None
        assert updated.name == "spinal-fusion-sweep"
        assert updated.description == "renamed from the GUI"

    async with get_session() as db:
        fetched = await get_experiment(db, experiment_id)
        assert fetched is not None
        assert fetched.name == "spinal-fusion-sweep"
        await db.delete(fetched)


async def test_set_design_spec(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        experiment_id = experiment.id

    spec = {"factors": [{"name": "temperature", "levels": [0.2, 0.8]}]}
    async with get_session() as db:
        updated = await update_experiment(db, experiment_id, fields={"design_spec": spec})
        assert updated is not None
        assert updated.design_spec == spec

    async with get_session() as db:
        fetched = await get_experiment(db, experiment_id)
        assert fetched is not None
        assert fetched.design_spec == spec
        await db.delete(fetched)


async def test_update_unknown_field_rejected(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        with pytest.raises(ValueError, match="not settable"):
            await update_experiment(db, experiment.id, fields={"owner_id": uuid.uuid4()})
        await db.delete(experiment)


async def test_archive_and_unarchive(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        experiment_id = experiment.id
        assert experiment.archived_at is None

    now = datetime.now(UTC)
    async with get_session() as db:
        archived = await update_experiment(db, experiment_id, fields={"archived_at": now})
        assert archived is not None
        assert archived.archived_at is not None

    async with get_session() as db:
        unarchived = await update_experiment(db, experiment_id, fields={"archived_at": None})
        assert unarchived is not None
        assert unarchived.archived_at is None

    async with get_session() as db:
        fetched = await get_experiment(db, experiment_id)
        assert fetched is not None
        await db.delete(fetched)


async def test_list_experiments_excludes_archived_by_default(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        active = await create_experiment(db, name=f"active-{uuid.uuid4().hex}", owner_id=owner_id)
        archived = await create_experiment(db, name=f"archived-{uuid.uuid4().hex}", owner_id=owner_id)
        await update_experiment(db, archived.id, fields={"archived_at": datetime.now(UTC)})
        active_id, archived_id = active.id, archived.id

    try:
        async with get_session() as db:
            default_list = await list_experiments(db, owner_id=owner_id)
            assert {e.id for e in default_list} == {active_id}

        async with get_session() as db:
            full_list = await list_experiments(db, owner_id=owner_id, include_archived=True)
            assert {e.id for e in full_list} == {active_id, archived_id}
    finally:
        async with get_session() as db:
            for eid in (active_id, archived_id):
                e = await get_experiment(db, eid)
                if e is not None:
                    await db.delete(e)
