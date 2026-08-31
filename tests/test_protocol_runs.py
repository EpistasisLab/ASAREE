"""Tests for services.protocol_runs, against the real dev-stack Postgres --
same throwaway-user fixture pattern as tests/test_protocols.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest_asyncio

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for experiments' FK
import asaree.models.experiment  # noqa: F401 -- registers research_experiments for protocols' FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.experiments import create_experiment, delete_experiment
from asaree.services.factorial_cells import upsert_cell
from asaree.services.protocol_runs import (
    create_protocol_run,
    fail_protocol_run,
    get_protocol_run,
    list_experiment_trials,
    list_protocol_runs,
    list_stale_protocol_runs,
    request_protocol_run_cancellation,
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


async def test_request_protocol_run_cancellation_flags_a_running_run(
    owner_id: uuid.UUID, protocol_id: uuid.UUID
) -> None:
    async with get_session() as db:
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id
        run.status = "running"
        await db.flush()

    async with get_session() as db:
        flagged = await request_protocol_run_cancellation(db, run_id)
        assert flagged is not None
        assert flagged.cancel_requested_at is not None
        # Only the flag is set -- run_protocol's own node loop is what
        # transitions status, not this call.
        assert flagged.status == "running"


async def test_request_protocol_run_cancellation_is_a_noop_once_terminal(
    owner_id: uuid.UUID, protocol_id: uuid.UUID
) -> None:
    async with get_session() as db:
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    async with get_session() as db:
        await fail_protocol_run(db, run_id, error="boom")

    async with get_session() as db:
        result = await request_protocol_run_cancellation(db, run_id)
        assert result is not None
        assert result.cancel_requested_at is None
        assert result.status == "failed"


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


async def test_list_stale_protocol_runs_applies_a_separate_cutoff_per_status(
    owner_id: uuid.UUID, protocol_id: uuid.UUID
) -> None:
    """The reason "pending" needs its own, much longer cutoff: a run waiting
    its turn behind the worker's max_jobs looks exactly like one whose task
    was cancelled before it could write a status. Failing the former would be
    worse than the stranded rows this reaps."""
    now = datetime.now(UTC)

    async with get_session() as db:
        # running, last heartbeat 10 minutes ago -- past the running cutoff.
        dead_running = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        dead_running.status = "running"
        dead_running.last_heartbeat_at = now - timedelta(minutes=10)

        # running, heartbeat seconds ago -- alive.
        live_running = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        live_running.status = "running"
        live_running.last_heartbeat_at = now - timedelta(seconds=5)

        # pending for 10 minutes: past the *running* cutoff but nowhere near
        # the pending one, so it must survive -- this is the queued-and-waiting
        # case, and it shares "no heartbeat" with the dead one below.
        queued = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        queued.created_at = now - timedelta(minutes=10)

        # pending for 2 days -- past the pending cutoff too.
        stranded = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        stranded.created_at = now - timedelta(days=2)

        # Terminal rows are never candidates however old.
        done = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        done.status = "completed"
        done.created_at = now - timedelta(days=2)

        await db.flush()
        ids = {
            "dead_running": dead_running.id,
            "live_running": live_running.id,
            "queued": queued.id,
            "stranded": stranded.id,
            "done": done.id,
        }

    async with get_session() as db:
        stale = await list_stale_protocol_runs(
            db,
            running_cutoff=now - timedelta(minutes=5),
            pending_cutoff=now - timedelta(hours=12),
        )
        # Other tests share this database, so assert on membership rather than
        # on the size of the result set.
        stale_ids = {r.id for r in stale}

    assert ids["dead_running"] in stale_ids
    assert ids["stranded"] in stale_ids
    assert ids["live_running"] not in stale_ids
    assert ids["queued"] not in stale_ids
    assert ids["done"] not in stale_ids


async def test_list_stale_protocol_runs_falls_back_to_created_at(owner_id: uuid.UUID, protocol_id: uuid.UUID) -> None:
    """A run that died before its first status write has no heartbeat at all;
    without the coalesce it would never be a candidate."""
    now = datetime.now(UTC)
    async with get_session() as db:
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run.status = "running"
        run.last_heartbeat_at = None
        run.created_at = now - timedelta(minutes=10)
        await db.flush()
        run_id = run.id

    async with get_session() as db:
        stale = await list_stale_protocol_runs(
            db, running_cutoff=now - timedelta(minutes=5), pending_cutoff=now - timedelta(hours=12)
        )
        assert run_id in {r.id for r in stale}


async def test_list_experiment_trials_reflects_not_started_running_and_completed(
    owner_id: uuid.UUID, protocol_id: uuid.UUID
) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"trial-test-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id

        # Never run at all -- still a trial, status "not_started".
        await upsert_cell(db, experiment_id=experiment_id, cell_label="cell-queued", fields={"factor_values": {"x": 1}})

        # Has a live ProtocolRun, still going -- status "running".
        running_run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        running_run.status = "running"
        await db.flush()
        await upsert_cell(
            db,
            experiment_id=experiment_id,
            cell_label="cell-running",
            fields={"factor_values": {"x": 2}, "run_id": running_run.id},
        )

        # Scored directly (no ProtocolRun at all) -- status "completed".
        await upsert_cell(
            db,
            experiment_id=experiment_id,
            cell_label="cell-scored-externally",
            fields={"factor_values": {"x": 3}, "metric_values": {"accuracy": 0.9}},
        )

    async with get_session() as db:
        trials = await list_experiment_trials(db, experiment_id=experiment_id)
        by_label = {t.cell_label: t for t in trials}
        assert by_label["cell-queued"].status == "not_started"
        assert by_label["cell-running"].status == "running"
        assert by_label["cell-scored-externally"].status == "completed"
        assert by_label["cell-scored-externally"].run_id is None

    async with get_session() as db:
        await delete_experiment(db, experiment_id)
