"""Tests for services.protocol_runs, against the real dev-stack Postgres --
same throwaway-user fixture pattern as tests/test_protocols.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

import asaree.models.experiment  # noqa: F401 -- registers research_experiments for protocols' FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.protocol_runs import (
    create_protocol_run,
    fail_protocol_run,
    get_protocol_run,
    list_protocol_runs,
    update_node_run,
)
from asaree.services.protocols import create_protocol, delete_protocol


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"protocol-run-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Protocol Run Test User",
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


@pytest_asyncio.fixture
async def protocol_id(owner_id: uuid.UUID) -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        protocol = await create_protocol(db, name="run-test-protocol", owner_id=owner_id)
        pid = protocol.id
    yield pid
    async with get_session() as db:
        await delete_protocol(db, pid)


async def test_create_get_and_node_run_progress(owner_id: uuid.UUID, protocol_id: uuid.UUID) -> None:
    async with get_session() as db:
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        assert run.status == "pending"
        assert run.node_runs == {}
        run_id = run.id

    async with get_session() as db:
        updated = await update_node_run(db, run_id, "node-1", {"status": "running"})
        assert updated is not None
        assert updated.node_runs == {"node-1": {"status": "running"}}

    async with get_session() as db:
        # A second patch to the SAME node merges rather than replaces --
        # confirms update_node_run's shallow-merge idiom.
        updated = await update_node_run(db, run_id, "node-1", {"status": "completed", "output_text": "done"})
        assert updated is not None
        assert updated.node_runs == {"node-1": {"status": "completed", "output_text": "done"}}

        # A different node gets its own independent key.
        updated = await update_node_run(db, run_id, "node-2", {"status": "running"})
        assert updated is not None
        assert updated.node_runs["node-1"]["status"] == "completed"
        assert updated.node_runs["node-2"] == {"status": "running"}

    async with get_session() as db:
        fetched = await get_protocol_run(db, run_id)
        assert fetched is not None
        assert fetched.status == "pending"


async def test_fail_protocol_run_is_race_safe_against_terminal(owner_id: uuid.UUID, protocol_id: uuid.UUID) -> None:
    async with get_session() as db:
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    async with get_session() as db:
        failed = await fail_protocol_run(db, run_id, error="boom")
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error == "boom"

    async with get_session() as db:
        # Already terminal -- a second fail_protocol_run call is a no-op,
        # not an overwrite of the original error.
        again = await fail_protocol_run(db, run_id, error="a different error")
        assert again is not None
        assert again.status == "failed"
        assert again.error == "boom"


async def test_list_protocol_runs_scoped_to_protocol(owner_id: uuid.UUID, protocol_id: uuid.UUID) -> None:
    async with get_session() as db:
        other_protocol = await create_protocol(db, name="run-test-protocol-other", owner_id=owner_id)
        other_protocol_id = other_protocol.id
        run_a = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        await create_protocol_run(db, protocol_id=other_protocol_id, owner_id=owner_id)

    async with get_session() as db:
        runs = await list_protocol_runs(db, protocol_id=protocol_id)
        assert [r.id for r in runs] == [run_a.id]
        await delete_protocol(db, other_protocol_id)
