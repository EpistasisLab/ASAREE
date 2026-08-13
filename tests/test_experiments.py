"""Tests for services.experiments' update_experiment -- covers the rename
capability just added (name/description), alongside the pre-existing
dataset_id attach/detach. Same real-Postgres, throwaway-user fixture as
tests/test_protocols.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for the FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.experiments import create_experiment, get_experiment, update_experiment


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


async def test_update_unknown_field_rejected(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="Untitled Experiment", owner_id=owner_id)
        with pytest.raises(ValueError, match="not settable"):
            await update_experiment(db, experiment.id, fields={"owner_id": uuid.uuid4()})
        await db.delete(experiment)
