"""Tests for services.protocols, against the real dev-stack Postgres --
JSONB has no sqlite equivalent, and this codebase's own conventions never
mock the database (see Motoro's own test suite for the same choice).

First test file in this repo, so it also owns the one bit of test
infrastructure needed: a throwaway ``User`` row per test, cleaned up
afterward, via ``asaree.models.database.get_session`` (the same
commit/rollback context manager non-FastAPI callers use).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for research_experiments' FK
import asaree.models.experiment  # noqa: F401 -- registers research_experiments for the FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.experiments import create_experiment
from asaree.services.protocols import (
    create_protocol,
    delete_protocol,
    generated_protocol_name,
    get_protocol,
    get_protocol_by_name,
    list_protocols,
    sync_protocol_names_to_experiment,
    update_protocol,
)


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    """The app's DB engine is a process-wide singleton bound to whatever
    event loop first used it; pytest-asyncio gives each test its own loop by
    default, so a stale pooled connection from a prior test's loop breaks
    the next one. Dispose after every test to force a fresh engine/pool
    bound to the next test's loop."""
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"protocol-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Protocol Test User",
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


async def test_create_get_update_delete(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        protocol = await create_protocol(db, name="pipeline-a", owner_id=owner_id)
        assert protocol.graph == {"nodes": [], "edges": []}
        assert protocol.experiment_id is None
        protocol_id = protocol.id

    async with get_session() as db:
        fetched = await get_protocol(db, protocol_id)
        assert fetched is not None
        assert fetched.name == "pipeline-a"

    async with get_session() as db:
        by_name = await get_protocol_by_name(db, "pipeline-a", owner_id=owner_id)
        assert by_name is not None
        assert by_name.id == protocol_id

    new_graph = {"nodes": [{"id": "n1", "type": "agent", "position": {"x": 0, "y": 0}, "data": {}}], "edges": []}
    async with get_session() as db:
        updated = await update_protocol(db, protocol_id, fields={"graph": new_graph, "description": "desc"})
        assert updated is not None
        assert updated.graph == new_graph
        assert updated.description == "desc"

    async with get_session() as db:
        assert await delete_protocol(db, protocol_id) is True
        assert await get_protocol(db, protocol_id) is None
        assert await delete_protocol(db, protocol_id) is False


async def test_update_unknown_field_rejected(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        protocol = await create_protocol(db, name="pipeline-b", owner_id=owner_id)
        with pytest.raises(ValueError, match="not settable"):
            await update_protocol(db, protocol.id, fields={"owner_id": uuid.uuid4()})
        await delete_protocol(db, protocol.id)


async def test_rename_sync_follows_the_experiment_name(owner_id: uuid.UUID) -> None:
    """An auto-named protocol tracks its experiment's name; a hand-named one
    (and a protocol on another experiment) is left alone."""
    async with get_session() as db:
        experiment = await create_experiment(db, name="old name", owner_id=owner_id)
        auto = await create_protocol(
            db,
            name=generated_protocol_name("old name", experiment.id),
            owner_id=owner_id,
            experiment_id=experiment.id,
        )
        custom = await create_protocol(
            db, name="my tuning sweep", owner_id=owner_id, experiment_id=experiment.id
        )
        elsewhere = await create_protocol(db, name="unattached", owner_id=owner_id)

    async with get_session() as db:
        renamed = await sync_protocol_names_to_experiment(
            db, experiment_id=experiment.id, experiment_name="new name", owner_id=owner_id
        )
        assert [p.id for p in renamed] == [auto.id]

    async with get_session() as db:
        assert (await get_protocol(db, auto.id)).name == generated_protocol_name("new name", experiment.id)
        assert (await get_protocol(db, custom.id)).name == "my tuning sweep"
        assert (await get_protocol(db, elsewhere.id)).name == "unattached"
        # Idempotent: a second pass with the same name renames nothing.
        assert await sync_protocol_names_to_experiment(
            db, experiment_id=experiment.id, experiment_name="new name", owner_id=owner_id
        ) == []

    async with get_session() as db:
        for pid in (auto.id, custom.id, elsewhere.id):
            await delete_protocol(db, pid)
        exp = await db.get(type(experiment), experiment.id)
        if exp is not None:
            await db.delete(exp)


async def test_rename_sync_skips_a_name_another_protocol_holds(owner_id: uuid.UUID) -> None:
    """Two auto-named protocols on one experiment both want the same string;
    the second must be skipped, not crash on uq_protocols_owner_name."""
    async with get_session() as db:
        experiment = await create_experiment(db, name="before", owner_id=owner_id)
        target = generated_protocol_name("after", experiment.id)
        squatter = await create_protocol(db, name=target, owner_id=owner_id, experiment_id=experiment.id)
        other = await create_protocol(
            db,
            name=generated_protocol_name("before", experiment.id),
            owner_id=owner_id,
            experiment_id=experiment.id,
        )

    async with get_session() as db:
        renamed = await sync_protocol_names_to_experiment(
            db, experiment_id=experiment.id, experiment_name="after", owner_id=owner_id
        )
        assert renamed == []

    async with get_session() as db:
        assert (await get_protocol(db, other.id)).name == generated_protocol_name("before", experiment.id)
        for pid in (squatter.id, other.id):
            await delete_protocol(db, pid)
        exp = await db.get(type(experiment), experiment.id)
        if exp is not None:
            await db.delete(exp)


async def test_list_filtered_by_experiment(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name="exp-for-protocols", owner_id=owner_id)
        p_attached = await create_protocol(
            db, name="attached", owner_id=owner_id, experiment_id=experiment.id
        )
        p_standalone = await create_protocol(db, name="standalone", owner_id=owner_id)

    async with get_session() as db:
        all_protocols = await list_protocols(db, owner_id=owner_id)
        assert {p.id for p in all_protocols} == {p_attached.id, p_standalone.id}

        scoped = await list_protocols(db, owner_id=owner_id, experiment_id=experiment.id)
        assert [p.id for p in scoped] == [p_attached.id]

    async with get_session() as db:
        await delete_protocol(db, p_attached.id)
        await delete_protocol(db, p_standalone.id)
        exp = await db.get(type(experiment), experiment.id)
        if exp is not None:
            await db.delete(exp)
