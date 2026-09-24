"""Tests for services.protocol_runs, against the real dev-stack Postgres --
same throwaway-user fixture pattern as tests/test_protocols.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException

import asaree.api.protocols as protocol_api
import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for experiments' FK
import asaree.models.experiment  # noqa: F401 -- registers research_experiments for protocols' FK
from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.experiment_run_results import summarize_experiment_run_results
from asaree.services.experiments import create_experiment, delete_experiment
from asaree.services.factorial_cells import get_replicate, upsert_replicate
from asaree.services.measurement_engine import (
    MeasurementEvaluation,
    MetricObservation,
    ProducerProvenance,
)
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocol_runs import (
    create_protocol_run,
    create_test_run,
    fail_protocol_run,
    get_protocol_run,
    list_experiment_trials,
    list_protocol_runs,
    list_stale_protocol_runs,
    record_measurement_evaluation,
    request_protocol_run_cancellation,
    set_status,
    update_node_run,
)
from asaree.services.protocols import create_protocol, delete_protocol, update_protocol
from asaree.services.runtime_metrics import finalize_attempt_measurement
from asaree.services.test_run_results import ResourceUsage


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


def test_test_run_response_accepts_projected_resource_usage() -> None:
    """The API boundary accepts the resource value objects returned by its projector."""
    now = datetime.now(UTC)
    usage = ResourceUsage(duration_seconds=None, cost_usd=None)

    response = protocol_api.TestRunResponse(
        id=uuid.uuid4(),
        protocol_id=uuid.uuid4(),
        status="pending",
        error=None,
        protocol_revision_id=None,
        created_at=now,
        updated_at=now,
        observations=[],
        artifacts=[],
        conversation=None,
        tested_published_revision=None,
        freshness={"out_of_date": False, "reasons": []},
        resources={"task": usage, "evaluation": usage, "total": usage},
        execution_summary={
            "node_runs": {},
            "started_at": None,
            "completed_at": None,
            "cancel_requested_at": None,
        },
    )

    assert response.resources.task.duration_seconds is None


async def test_test_run_replaces_only_the_experiment_current_attempt(owner_id: uuid.UUID) -> None:
    """A canvas validation attempt has no factorial projection or history."""
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"test-run-{uuid.uuid4().hex}", owner_id=owner_id)
        protocol = await create_protocol(db, name="test-run-protocol", owner_id=owner_id, experiment_id=experiment.id)
        revision = await publish_protocol(db, protocol)
        first = await create_test_run(db, protocol_id=protocol.id, owner_id=owner_id, protocol_revision_id=revision.id)
        assert first.is_test_run
        assert first.replicate_result_id is None
        first_id = first.id

    async with get_session() as db:
        protocol = await db.get(type(protocol), protocol.id)
        assert protocol is not None
        revision = await publish_protocol(db, protocol)
        second = await create_test_run(db, protocol_id=protocol.id, owner_id=owner_id, protocol_revision_id=revision.id)
        experiment = await db.get(type(experiment), experiment.id)
        assert experiment is not None
        assert experiment.latest_test_run_id == second.id
        assert await get_protocol_run(db, first_id) is None

        await delete_protocol(db, protocol.id)
        await delete_experiment(db, experiment.id)


async def test_test_run_records_separate_task_and_evaluation_boundaries(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"test-run-timing-{uuid.uuid4().hex}", owner_id=owner_id)
        protocol = await create_protocol(
            db, name="test-run-timing-protocol", owner_id=owner_id, experiment_id=experiment.id
        )
        revision = await publish_protocol(db, protocol)
        run = await create_test_run(db, protocol_id=protocol.id, owner_id=owner_id, protocol_revision_id=revision.id)
        await set_status(db, run.id, status="running")
        await set_status(db, run.id, status="finalizing")
        await set_status(db, run.id, status="completed")
        before_evaluation = await get_protocol_run(db, run.id)
        assert before_evaluation is not None
        assert before_evaluation.attempt_result is not None
        assert before_evaluation.attempt_result["task_completed_at"]
        assert before_evaluation.attempt_result["evaluation_started_at"]

        await record_measurement_evaluation(
            db,
            run.id,
            MeasurementEvaluation(
                replicate_id=str(run.id),
                attempt_id=str(run.id),
                observations=(),
                artifacts=(),
            ),
        )
        completed = await get_protocol_run(db, run.id)
        assert completed is not None
        assert completed.attempt_result is not None
        assert completed.attempt_result["evaluation_completed_at"]

        await delete_protocol(db, protocol.id)
        await delete_experiment(db, experiment.id)


async def test_test_run_public_api_replaces_and_hides_it_from_other_users(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public Test Run API follows the canvas's existing owner access rule."""

    async def _enqueue(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(protocol_api, "validate_coordination_strategy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "validate_stage_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "validate_prompt_references", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "topological_order", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", _enqueue)

    async with get_session() as db:
        owner = await db.get(User, owner_id)
        assert owner is not None
        experiment = await create_experiment(db, name=f"test-run-api-{uuid.uuid4().hex}", owner_id=owner_id)
        protocol = await create_protocol(
            db, name="test-run-api-protocol", owner_id=owner_id, experiment_id=experiment.id
        )
        await publish_protocol(db, protocol)
        first = await protocol_api.create_test_run_endpoint(protocol.id, owner, db)
        assert first.status == "pending"
        assert (await protocol_api.get_latest_test_run_endpoint(protocol.id, owner, db)).id == first.id

        second = await protocol_api.create_test_run_endpoint(protocol.id, owner, db)
        assert second.id != first.id
        assert await get_protocol_run(db, first.id) is None

        # Any terminal outcome remains the current, reopenable Test Run rather
        # than reviving an older successful validation attempt.
        for status in ("completed", "failed", "cancelled"):
            attempt = await protocol_api.create_test_run_endpoint(protocol.id, owner, db)
            await set_status(db, attempt.id, status=status, error="boom" if status == "failed" else None)
            latest = await protocol_api.get_latest_test_run_endpoint(protocol.id, owner, db)
            assert latest.id == attempt.id
            assert latest.status == status

        other = User(
            email=f"other-test-run-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Other Test Run User",
        )
        db.add(other)
        await db.flush()
        with pytest.raises(HTTPException, match="No such protocol"):
            await protocol_api.get_latest_test_run_endpoint(protocol.id, other, db)

        await delete_protocol(db, protocol.id)
        await delete_experiment(db, experiment.id)
        await db.delete(other)
        await db.flush()


async def test_test_run_public_api_exposes_live_evidence_resources_and_freshness(
    owner_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace reads everything through the public latest-result seam."""

    async def _enqueue(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(protocol_api, "validate_coordination_strategy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "validate_stage_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "validate_prompt_references", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(protocol_api, "topological_order", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", _enqueue)

    plan = {
        "metrics": [
            {
                "id": "cost",
                "name": "Cost",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": True,
            }
        ],
        "producers": [
            {"id": "runtime", "producer_id": "asaree.runtime", "kind": "runtime", "outputs": {"cost_usd": "cost"}}
        ],
        "inputs": [{"producer_binding_id": "runtime", "input_key": "facts", "source_key": "attempt.runtime"}],
    }
    async with get_session() as db:
        owner = await db.get(User, owner_id)
        assert owner is not None
        experiment = await create_experiment(
            db, name=f"test-run-evidence-{uuid.uuid4().hex}", owner_id=owner_id, measurement_plan=plan
        )
        protocol = await create_protocol(
            db,
            name="test-run-evidence-protocol",
            owner_id=owner_id,
            experiment_id=experiment.id,
            graph={"nodes": [{"id": "agent-1", "type": "agent", "data": {"label": "Analyst"}}], "edges": []},
        )
        revision = await publish_protocol(db, protocol)
        response = await protocol_api.create_test_run_endpoint(protocol.id, owner, db)
        run = await get_protocol_run(db, response.id)
        assert run is not None
        run.status = "completed"
        run.started_at = datetime(2026, 1, 1, tzinfo=UTC)
        run.completed_at = datetime(2026, 1, 1, 0, 0, 8, tzinfo=UTC)
        run.node_runs = {"agent-1": {"status": "completed", "output_text": "done"}}
        run.conversation = {"state": "completed", "entry_agent_id": "agent-1", "messages": []}
        run.attempt_result = {
            **(run.attempt_result or {}),
            "measurement": {
                "observations": [
                    {
                        "metric_id": "cost",
                        "metric_name": "Cost",
                        "value_type": "number",
                        "status": "measured",
                        "value": 1.25,
                        "error": None,
                        "attempt_id": str(run.id),
                        "producer": {
                            "binding_id": "runtime",
                            "producer_id": "asaree.runtime",
                            "kind": "runtime",
                            "version": "1",
                        },
                        "input_provenance": {},
                    }
                ],
                "artifacts": [],
            },
            "task_completed_at": "2026-01-01T00:00:08+00:00",
            "evaluation_started_at": "2026-01-01T00:00:08+00:00",
            "evaluation_completed_at": "2026-01-01T00:00:10+00:00",
            "evaluation_summary": {"cost_usd": None},
        }
        await db.flush()

        latest = await protocol_api.get_latest_test_run_endpoint(protocol.id, owner, db)
        assert latest.conversation == run.conversation
        assert latest.tested_published_revision is not None
        assert latest.tested_published_revision.model_dump() == {
            "id": revision.id,
            "number": 1,
            "published_at": revision.published_at,
        }
        assert latest.freshness.model_dump() == {"out_of_date": False, "reasons": []}
        assert latest.resources.model_dump() == {
            "task": {"duration_seconds": 8.0, "cost_usd": 1.25},
            "evaluation": {"duration_seconds": 2.0, "cost_usd": None},
            "total": {"duration_seconds": 10.0, "cost_usd": None},
        }

        await update_protocol(db, protocol.id, fields={"graph": {"nodes": [], "edges": []}})
        experiment.measurement_plan = None
        await db.flush()
        stale = await protocol_api.get_latest_test_run_endpoint(protocol.id, owner, db)
        assert stale.freshness.out_of_date is True
        assert stale.freshness.reasons == ["canvas", "measurement_plan"]

        await delete_protocol(db, protocol.id)
        await delete_experiment(db, experiment.id)


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


async def test_request_protocol_run_cancellation_immediately_cancels_a_pending_run(
    owner_id: uuid.UUID, protocol_id: uuid.UUID
) -> None:
    """A queued run has no executor available to observe a cancel flag."""
    async with get_session() as db:
        run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        run_id = run.id

    async with get_session() as db:
        cancelled = await request_protocol_run_cancellation(db, run_id)
        assert cancelled is not None
        assert cancelled.cancel_requested_at is not None
        assert cancelled.status == "cancelled"
        assert cancelled.completed_at is not None


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

        # finalizing uses the running cutoff too: graph execution is over, but
        # built-in measurement finalization can still strand when its worker dies.
        dead_finalizing = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        dead_finalizing.status = "finalizing"
        dead_finalizing.last_heartbeat_at = now - timedelta(minutes=10)

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
            "dead_finalizing": dead_finalizing.id,
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
    assert ids["dead_finalizing"] in stale_ids
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
        await upsert_replicate(
            db, experiment_id=experiment_id, replicate_label="cell-queued", fields={"factor_values": {"x": 1}}
        )

        # Has a live ProtocolRun, still going -- status "running".
        running_run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=owner_id)
        running_run.status = "running"
        await db.flush()
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="cell-running",
            fields={"factor_values": {"x": 2}, "run_id": running_run.id},
        )

        # Scored directly (no ProtocolRun at all) -- status "completed".
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="cell-scored-externally",
            fields={"factor_values": {"x": 3}, "metric_values": {"accuracy": 0.9}},
        )

    async with get_session() as db:
        trials = await list_experiment_trials(db, experiment_id=experiment_id)
        by_label = {trial.replicate_label: trial for trial in trials}
        assert by_label["cell-queued"].status == "not_started"
        assert by_label["cell-running"].status == "running"
        assert by_label["cell-scored-externally"].status == "completed"
        assert by_label["cell-scored-externally"].run_id is None

    async with get_session() as db:
        await delete_experiment(db, experiment_id)


async def test_list_experiment_trials_marks_runs_obsolete_after_a_new_canvas_publish(owner_id: uuid.UUID) -> None:
    """Both revision-pinned and older unpinned rows surface as obsolete.

    The fallback for an unpinned row matters for experiments that were run
    before published-canvas provenance was stored on ProtocolRun.
    """
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"obsolete-trial-test-{uuid.uuid4().hex}", owner_id=owner_id)
        experiment_id = experiment.id
        protocol = await create_protocol(
            db,
            name=f"obsolete-trial-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment_id,
        )
        protocol_id = protocol.id
        first_revision = await publish_protocol(db, protocol)

        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="legacy-cell",
            fields={"factor_values": {"x": 1}},
        )
        legacy_run = await create_protocol_run(
            db,
            protocol_id=protocol_id,
            owner_id=owner_id,
            # Deliberately omit protocol_revision_id to model an older run.
        )
        legacy_run.status = "completed"
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="legacy-cell",
            fields={"run_id": legacy_run.id},
        )

        protocol = await update_protocol(
            db,
            protocol_id,
            fields={
                "graph": {
                    "nodes": [{"id": "new-step", "type": "step", "data": {}}],
                    "edges": [],
                }
            },
        )
        assert protocol is not None
        second_revision = await publish_protocol(db, protocol)
        assert second_revision.id != first_revision.id

        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="current-cell",
            fields={"factor_values": {"x": 2}},
        )
        current_run = await create_protocol_run(
            db,
            protocol_id=protocol_id,
            owner_id=owner_id,
            protocol_revision_id=second_revision.id,
        )
        current_run.status = "completed"
        await upsert_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="current-cell",
            fields={"run_id": current_run.id},
        )

    async with get_session() as db:
        trials = await list_experiment_trials(db, experiment_id=experiment_id)
        by_label = {trial.replicate_label: trial for trial in trials}
        assert by_label["legacy-cell"].obsolete is True
        assert by_label["current-cell"].obsolete is False

    async with get_session() as db:
        await delete_protocol(db, protocol_id)
        await delete_experiment(db, experiment_id)


async def test_measurement_evaluations_are_immutable_per_attempt_with_one_current_projection(
    owner_id: uuid.UUID,
) -> None:
    async with get_session() as db:
        experiment = await create_experiment(
            db,
            name=f"measurement-history-{uuid.uuid4().hex}",
            owner_id=owner_id,
        )
        protocol = await create_protocol(
            db,
            name=f"measurement-history-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment.id,
        )
        replicate = await upsert_replicate(
            db,
            experiment_id=experiment.id,
            replicate_label="cell-1",
            fields={"factor_values": {"tier": "small"}},
        )
        first_run = await create_protocol_run(
            db,
            protocol_id=protocol.id,
            owner_id=owner_id,
            replicate_label=replicate.replicate_label,
            factor_values=replicate.factor_values,
            replicate_result_id=replicate.id,
            design_revision_id=replicate.design_revision_id,
        )
        provenance = ProducerProvenance(
            binding_id="runtime",
            producer_id="asaree.runtime",
            kind="runtime",
            version="1",
        )
        first = MeasurementEvaluation(
            replicate_id=str(replicate.id),
            attempt_id=str(first_run.id),
            observations=(
                MetricObservation(
                    metric_id="cost",
                    metric_name="Cost",
                    value_type="number",
                    status="measured",
                    value=1.25,
                    error=None,
                    attempt_id=str(first_run.id),
                    producer=provenance,
                    input_provenance={"facts": {"protocol_run_id": str(first_run.id)}},
                ),
            ),
            artifacts=(),
        )
        await record_measurement_evaluation(db, first_run.id, first)

        second_run = await create_protocol_run(
            db,
            protocol_id=protocol.id,
            owner_id=owner_id,
            replicate_label=replicate.replicate_label,
            factor_values=replicate.factor_values,
            replicate_result_id=replicate.id,
            design_revision_id=replicate.design_revision_id,
        )
        second = MeasurementEvaluation(
            replicate_id=str(replicate.id),
            attempt_id=str(second_run.id),
            observations=(
                MetricObservation(
                    metric_id="cost",
                    metric_name="Cost",
                    value_type="number",
                    status="unavailable",
                    value=None,
                    error="provider did not report cost",
                    attempt_id=str(second_run.id),
                    producer=provenance,
                    input_provenance={"facts": {"protocol_run_id": str(second_run.id)}},
                ),
            ),
            artifacts=(),
        )
        await record_measurement_evaluation(db, second_run.id, second)
        experiment_id = experiment.id
        protocol_id = protocol.id
        first_run_id = first_run.id
        second_run_id = second_run.id

    async with get_session() as db:
        stored_first = await get_protocol_run(db, first_run_id)
        stored_replicate = await get_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="cell-1",
            revision_id=replicate.design_revision_id,
        )
        assert stored_first is not None
        assert stored_first.attempt_result is not None
        assert stored_first.attempt_result["measurement"]["observations"][0]["value"] == 1.25
        assert stored_replicate is not None
        assert stored_replicate.artifacts is not None
        assert stored_replicate.artifacts["measurement"]["attempt_id"] == str(second_run_id)
        results = await summarize_experiment_run_results(db, experiment_id=experiment_id)
        result = results["replicates"][0]
        assert result["metric_observations"][0]["status"] == "unavailable"
        assert result["evaluation_artifacts"] == []
        assert result["superseded_runs"][0]["metric_observations"][0]["value"] == 1.25

    async with get_session() as db:
        await delete_protocol(db, protocol_id)
        await delete_experiment(db, experiment_id)


async def test_a_truncated_run_keeps_its_numbers_but_does_not_score_its_replicate(
    owner_id: uuid.UUID,
) -> None:
    """An agent cut off by its iteration ceiling measured an unfinished run."""
    async with get_session() as db:
        experiment = await create_experiment(db, name=f"truncated-{uuid.uuid4().hex}", owner_id=owner_id)
        protocol = await create_protocol(
            db,
            name=f"truncated-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment.id,
        )
        replicate = await upsert_replicate(
            db,
            experiment_id=experiment.id,
            replicate_label="cell-1",
            fields={"factor_values": {"tier": "small"}},
        )
        run = await create_protocol_run(
            db,
            protocol_id=protocol.id,
            owner_id=owner_id,
            replicate_label=replicate.replicate_label,
            factor_values=replicate.factor_values,
            replicate_result_id=replicate.id,
            design_revision_id=replicate.design_revision_id,
        )
        run.node_runs = {
            "agent-1": {
                "status": "completed",
                "truncation": {"reason": "max_iterations", "iterations": 15, "max_iterations": 15},
            }
        }
        await db.flush()
        await record_measurement_evaluation(
            db,
            run.id,
            MeasurementEvaluation(
                replicate_id=str(replicate.id),
                attempt_id=str(run.id),
                observations=(
                    MetricObservation(
                        metric_id="accuracy",
                        metric_name="Accuracy",
                        value_type="number",
                        status="measured",
                        value=0.9,
                        error=None,
                        attempt_id=str(run.id),
                        producer=ProducerProvenance(
                            binding_id="reported",
                            producer_id="asaree.reported",
                            kind="reported",
                            version="1",
                        ),
                        input_provenance={"facts": {"protocol_run_id": str(run.id)}},
                    ),
                ),
                artifacts=(),
            ),
        )
        experiment_id = experiment.id
        protocol_id = protocol.id
        run_id = run.id

    async with get_session() as db:
        stored_run = await get_protocol_run(db, run_id)
        assert stored_run is not None
        assert stored_run.attempt_result is not None
        # The measurement itself is real and stays inspectable.
        assert stored_run.attempt_result["metric_values"] == {"Accuracy": 0.9}
        stored_replicate = await get_replicate(
            db,
            experiment_id=experiment_id,
            replicate_label="cell-1",
        )
        assert stored_replicate is not None
        assert not stored_replicate.metric_values
        assert stored_replicate.artifacts is not None
        assert stored_replicate.artifacts["measurement"]["attempt_id"] == str(run_id)

    async with get_session() as db:
        await delete_protocol(db, protocol_id)
        await delete_experiment(db, experiment_id)


async def test_runtime_measurement_is_snapshotted_on_attempt_and_current_replicate(
    owner_id: uuid.UUID, monkeypatch
) -> None:
    plan = {
        "metrics": [
            {
                "id": "cost",
                "name": "Provider cost",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": True,
            }
        ],
        "producers": [
            {
                "id": "runtime",
                "producer_id": "asaree.runtime",
                "kind": "runtime",
                "outputs": {"cost_usd": "cost"},
            }
        ],
        "inputs": [
            {
                "producer_binding_id": "runtime",
                "input_key": "facts",
                "source_key": "attempt.runtime",
            }
        ],
    }
    live_cost = 1.25

    async def fake_collect(run):
        return {
            "protocol_run_id": str(run.id),
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat(),
            "runs": [{"run_id": "worker", "cost_usd": live_cost, "usage": {}, "steps": []}],
            "critic_gates": [],
        }

    monkeypatch.setattr("asaree.services.runtime_metrics.collect_runtime_facts", fake_collect)
    async with get_session() as db:
        experiment = await create_experiment(
            db,
            name=f"runtime-snapshot-{uuid.uuid4().hex}",
            owner_id=owner_id,
            measurement_plan=plan,
        )
        protocol = await create_protocol(
            db,
            name=f"runtime-snapshot-protocol-{uuid.uuid4().hex}",
            owner_id=owner_id,
            experiment_id=experiment.id,
        )
        replicate = await upsert_replicate(
            db,
            experiment_id=experiment.id,
            replicate_label="cell-1",
            fields={"factor_values": {"tier": "small"}},
        )
        run = await create_protocol_run(
            db,
            protocol_id=protocol.id,
            owner_id=owner_id,
            replicate_label=replicate.replicate_label,
            factor_values=replicate.factor_values,
            replicate_result_id=replicate.id,
            design_revision_id=replicate.design_revision_id,
        )
        await set_status(db, run.id, status="running")
        await set_status(db, run.id, status="completed")
        assert await finalize_attempt_measurement(db, run.id) is True
        failed_replicate = await upsert_replicate(
            db,
            experiment_id=experiment.id,
            replicate_label="cell-2",
            fields={"factor_values": {"tier": "large"}},
        )
        failed_run = await create_protocol_run(
            db,
            protocol_id=protocol.id,
            owner_id=owner_id,
            replicate_label=failed_replicate.replicate_label,
            factor_values=failed_replicate.factor_values,
            replicate_result_id=failed_replicate.id,
            design_revision_id=failed_replicate.design_revision_id,
        )
        await set_status(db, failed_run.id, status="running")
        await set_status(db, failed_run.id, status="failed", error="worker failed")
        # Unsuccessful terminal transitions already have their immutable
        # runtime facts, so a repeated finalization request is a no-op.
        assert await finalize_attempt_measurement(db, failed_run.id) is False
        run_id = run.id
        failed_run_id = failed_run.id
        experiment_id = experiment.id
        protocol_id = protocol.id

    live_cost = 99.0
    async with get_session() as db:
        # Finalization is idempotent and never rereads mutable provider pricing.
        assert await finalize_attempt_measurement(db, run_id) is False
        stored_run = await get_protocol_run(db, run_id)
        stored_replicate = await get_replicate(db, experiment_id=experiment_id, replicate_label="cell-1")
        assert stored_run is not None
        assert stored_run.attempt_result["measurement"]["observations"][0]["value"] == 1.25
        assert stored_replicate is not None
        assert stored_replicate.artifacts["measurement"]["observations"][0]["value"] == 1.25
        stored_failed = await get_protocol_run(db, failed_run_id)
        failed_replicate = await get_replicate(db, experiment_id=experiment_id, replicate_label="cell-2")
        assert stored_failed is not None
        assert stored_failed.attempt_result["measurement"]["observations"][0]["value"] == 1.25
        assert failed_replicate is not None
        assert failed_replicate.artifacts["measurement"]["observations"][0]["value"] == 1.25

    async with get_session() as db:
        await delete_protocol(db, protocol_id)
        await delete_experiment(db, experiment_id)
