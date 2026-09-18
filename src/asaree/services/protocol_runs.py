"""ProtocolRun creation, lookup, and progress updates.

Mirrors Motoro's AgentRun lifecycle helpers (create_run/fail_run) --
the same "force-fail from outside, race-safe against a live executor's own
commit" idiom, since ProtocolRun has no other precedent to follow in this
codebase.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.experiment import ResearchExperiment
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.factorial_cells import get_replicate, list_replicates
from asaree.services.measurement_engine import MeasurementEvaluation, normalize_measurement_plan
from asaree.services.measurement_migration import normalize_experiment_measurement_plan

# "limit_reached" is terminal too: a conversation that spent its consultation
# budget and could not then produce an answer is finished, not broken, and
# calling it "failed" would hide the one thing a user needs to know to fix it
# (see services.agent_messenger's budget constants).
logger = logging.getLogger(__name__)

TERMINAL_PROTOCOL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "limit_reached"})


def _apply_status(run: ProtocolRun, *, status: str, error: str | None, now: datetime) -> None:
    run.status = status
    if error is not None:
        run.error = error
    if status == "running" and run.started_at is None:
        run.started_at = now
    if status in TERMINAL_PROTOCOL_RUN_STATUSES:
        run.completed_at = now


async def _finalize_unsuccessful_measurement(db: AsyncSession, run: ProtocolRun) -> None:
    if run.status not in TERMINAL_PROTOCOL_RUN_STATUSES or run.status == "completed":
        return
    try:
        # Local import avoids a module cycle: the runtime adapter records its
        # result through record_measurement_evaluation in this module.
        from asaree.services.runtime_metrics import finalize_attempt_measurement

        await finalize_attempt_measurement(db, run.id)
    except Exception:
        logger.exception("measurement_finalization_failed", extra={"protocol_run_id": str(run.id)})


async def create_protocol_run(
    db: AsyncSession,
    *,
    protocol_id: uuid.UUID,
    owner_id: uuid.UUID,
    replicate_label: str | None = None,
    factor_values: dict[str, Any] | None = None,
    replicate_result_id: uuid.UUID | None = None,
    target_node_id: str | None = None,
    design_revision_id: uuid.UUID | None = None,
    protocol_revision_id: uuid.UUID | None = None,
    is_test_run: bool = False,
) -> ProtocolRun:
    """``replicate_label``/``factor_values``/``design_revision_id`` are set together
    only for a run created by "run all cells"
    (``services.protocol_execution.plan_cell_runs``) -- all stay ``None`` for a
    plain graph run, the existing behavior. ``design_revision_id`` pins which
    generation of the design this run's result belongs to, so a regenerate
    mid-flight can't redirect the write-back (see the model's own comment).
    ``target_node_id`` is set only for a single-node "Play" run (see
    ``ProtocolRun`` model's own comment) -- mutually exclusive with
    replicate_label/factor_values in practice, though nothing enforces that here."""
    measurement_plan_snapshot: dict[str, Any] | None = None
    reference_values: dict[str, Any] = {}
    protocol = await db.get(Protocol, protocol_id)
    if protocol is not None and protocol.experiment_id is not None:
        experiment = await db.get(ResearchExperiment, protocol.experiment_id)
        if experiment is not None:
            effective_plan = (
                experiment.locked_measurement_plan if experiment.locked_at is not None else experiment.measurement_plan
            )
            effective_design_spec = (
                experiment.locked_design_spec if experiment.locked_at is not None else experiment.design_spec
            )
            attempt_plan = normalize_experiment_measurement_plan(
                effective_plan,
                (effective_design_spec or {}).get("metrics"),
            )
            if attempt_plan["metrics"]:
                measurement_plan_snapshot = normalize_measurement_plan(attempt_plan)
            task_brief = experiment.task_brief if isinstance(experiment.task_brief, dict) else {}
            declared_references = task_brief.get("reference_values")
            if isinstance(declared_references, dict):
                reference_values = dict(declared_references)

    run = ProtocolRun(
        protocol_id=protocol_id,
        owner_id=owner_id,
        status="pending",
        is_test_run=is_test_run,
        node_runs={},
        replicate_label=replicate_label,
        factor_values=factor_values,
        replicate_result_id=replicate_result_id,
        target_node_id=target_node_id,
        design_revision_id=design_revision_id,
        protocol_revision_id=protocol_revision_id,
        attempt_result=(
            {
                **(
                    {"measurement_plan_snapshot": measurement_plan_snapshot}
                    if measurement_plan_snapshot is not None
                    else {}
                ),
                **({"reference_values": reference_values} if reference_values else {}),
            }
            or None
        ),
    )
    db.add(run)
    await db.flush()
    if replicate_result_id is not None:
        # A planned run is a new attempt for this stable replicate slot. Its
        # result projection must immediately become "latest attempt only" --
        # no old score/output may survive a pending, failed, or cancelled
        # replacement attempt. The immutable old values belong on that old
        # ProtocolRun.attempt_result instead.
        replicate = await db.get(FactorialReplicateResult, replicate_result_id)
        if replicate is not None:
            # Legacy/current projections may predate attempt_result. Snapshot
            # their last values before replacing the slot so a manual rerun
            # never erases inspectable history.
            if replicate.run_id is not None:
                previous_run = await get_protocol_run(db, replicate.run_id)
                if previous_run is not None:
                    snapshot = dict(previous_run.attempt_result or {})
                    if "metric_values" not in snapshot and isinstance(replicate.metric_values, dict):
                        snapshot["metric_values"] = dict(replicate.metric_values)
                    evaluation = (replicate.artifacts or {}).get("metric_evaluation")
                    if "metric_evaluation" not in snapshot and isinstance(evaluation, dict):
                        snapshot["metric_evaluation"] = dict(evaluation)
                    previous_run.attempt_result = snapshot or None
            replicate.run_id = run.id
            replicate.workspace_id = None
            replicate.metric_values = None
            replicate.artifacts = None
            await db.flush()
    await db.refresh(run)
    return run


async def create_test_run(
    db: AsyncSession, *, protocol_id: uuid.UUID, owner_id: uuid.UUID, protocol_revision_id: uuid.UUID
) -> ProtocolRun:
    """Create the experiment's next canvas validation attempt.

    The pointer changes before the obsolete row is removed, so a replacement
    can never leave the experiment with no current Test Run.
    """
    protocol = await db.get(Protocol, protocol_id)
    if protocol is None or protocol.experiment_id is None:
        raise ValueError("Test Runs require a protocol linked to an experiment")
    # Serialize replacement per experiment. Without this row lock, two starts
    # could both observe the same old pointer and leave one obsolete Test Run.
    experiment = (
        await db.execute(
            select(ResearchExperiment).where(ResearchExperiment.id == protocol.experiment_id).with_for_update()
        )
    ).scalar_one_or_none()
    if experiment is None:
        raise ValueError("Test Runs require an existing experiment")
    previous_id = experiment.latest_test_run_id
    run = await create_protocol_run(
        db,
        protocol_id=protocol_id,
        owner_id=owner_id,
        protocol_revision_id=protocol_revision_id,
        is_test_run=True,
    )
    experiment.latest_test_run_id = run.id
    await db.flush()
    if previous_id is not None and previous_id != run.id:
        previous = await db.get(ProtocolRun, previous_id)
        if previous is not None and previous.is_test_run:
            await db.delete(previous)
    await db.flush()
    return run


async def get_protocol_run(db: AsyncSession, protocol_run_id: uuid.UUID) -> ProtocolRun | None:
    return (await db.execute(select(ProtocolRun).where(ProtocolRun.id == protocol_run_id))).scalar_one_or_none()


async def get_cancel_requested_at(db: AsyncSession, protocol_run_id: uuid.UUID) -> datetime | None:
    """Single-column read, not a full get_protocol_run -- this is polled
    every ~1.5s for the duration of a live agent run (see
    services.protocol_execution._monitor_protocol_run) to detect a Stop click
    fast enough to interrupt mid-agent via Motoro's own cancel_event,
    not just at run_protocol's own between-nodes check. Fetching the whole
    row (and deserializing node_runs' JSONB) on that cadence would be pure
    waste -- this reads nothing else."""
    return (
        await db.execute(select(ProtocolRun.cancel_requested_at).where(ProtocolRun.id == protocol_run_id))
    ).scalar_one_or_none()


async def touch_protocol_run_heartbeat(db: AsyncSession, protocol_run_id: uuid.UUID) -> None:
    """Refresh liveness without loading or rewriting the run's JSON documents."""
    await db.execute(
        update(ProtocolRun)
        .where(ProtocolRun.id == protocol_run_id, ProtocolRun.status.not_in(TERMINAL_PROTOCOL_RUN_STATUSES))
        .values(last_heartbeat_at=datetime.now(UTC))
    )


async def list_protocol_runs(db: AsyncSession, *, protocol_id: uuid.UUID) -> Sequence[ProtocolRun]:
    return (
        (
            await db.execute(
                select(ProtocolRun)
                .where(ProtocolRun.protocol_id == protocol_id, ProtocolRun.is_test_run.is_(False))
                .order_by(ProtocolRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def list_stale_protocol_runs(
    db: AsyncSession, *, running_cutoff: datetime, pending_cutoff: datetime
) -> Sequence[ProtocolRun]:
    """Non-terminal runs that have shown no sign of life for long enough to
    call their worker dead.

    The backstop for a run whose worker died mid-flight, or whose task was
    cancelled somewhere it could not record why (``worker.tasks`` makes a
    best-effort attempt, but a hard kill or a lost DB connection defeats it).
    Without this nothing ever reconciled ``protocol_runs`` -- ``check_stale_runs``
    only covered Motoro's agent ``Run``s -- so a run interrupted early enough
    sat at "pending" forever, indistinguishable from one never picked up.

    ``pending`` is included, not just ``running``, because a run cancelled
    before its first status write never leaves "pending" -- that is exactly the
    case that stranded rows. It gets its own, far more generous cutoff: a
    pending run with no heartbeat is equally consistent with "queued behind
    max_jobs, waiting its turn", and failing those would be worse than the bug.
    (A precise version would ask arq whether the job is still in Redis; that
    couples this to the queue's internals, and the timing here only decides how
    long a genuinely dead row lingers.)

    Both arms key on ``last_heartbeat_at`` where there is one (written by every
    ``set_status``/``update_node_run``), falling back to ``created_at``.
    """
    last_seen = func.coalesce(ProtocolRun.last_heartbeat_at, ProtocolRun.created_at)
    return (
        (
            await db.execute(
                select(ProtocolRun)
                .where(
                    or_(
                        and_(ProtocolRun.status.in_(("running", "finalizing")), last_seen < running_cutoff),
                        and_(ProtocolRun.status == "pending", last_seen < pending_cutoff),
                    )
                )
                .order_by(ProtocolRun.created_at)
            )
        )
        .scalars()
        .all()
    )


async def set_status(
    db: AsyncSession, protocol_run_id: uuid.UUID, *, status: str, error: str | None = None
) -> ProtocolRun | None:
    run = await get_protocol_run(db, protocol_run_id)
    if run is None:
        return None
    now = datetime.now(UTC)
    _apply_status(run, status=status, error=error, now=now)
    attempt_result = dict(run.attempt_result or {})
    if status == "finalizing":
        attempt_result.setdefault("task_completed_at", now.isoformat())
        attempt_result.setdefault("evaluation_started_at", now.isoformat())
    if status in TERMINAL_PROTOCOL_RUN_STATUSES:
        attempt_result.setdefault("task_completed_at", now.isoformat())
        if "measurement" not in attempt_result:
            attempt_result.setdefault("evaluation_started_at", now.isoformat())
    run.attempt_result = attempt_result or None
    run.last_heartbeat_at = now
    await db.flush()
    await db.refresh(run)
    await _finalize_unsuccessful_measurement(db, run)
    return run


async def update_node_run(
    db: AsyncSession, protocol_run_id: uuid.UUID, node_id: str, patch: dict[str, Any]
) -> ProtocolRun | None:
    """Shallow-merge *patch* into ``node_runs[node_id]`` -- the same
    read-modify-write idiom ``upsert_replicate`` uses for its JSONB columns, one
    level deeper (merging into one key of the blob, not the blob itself)."""
    run = await get_protocol_run(db, protocol_run_id)
    if run is None:
        return None
    node_runs = dict(run.node_runs or {})
    node_runs[node_id] = {**node_runs.get(node_id, {}), **patch}
    run.node_runs = node_runs
    run.last_heartbeat_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(run)
    return run


async def update_conversation(
    db: AsyncSession, protocol_run_id: uuid.UUID, conversation: dict[str, Any]
) -> ProtocolRun | None:
    """Checkpoint the whole transcript as one document.

    Assigned whole rather than merged: the messenger holds the authoritative
    in-memory copy for the life of the run and appends to it, so a partial
    merge here could only ever reorder what it already knows. Called before a
    peer is allowed to execute and again after it replies, which is what makes
    a worker retry able to see exactly how far the conversation got.
    """
    run = await get_protocol_run(db, protocol_run_id)
    if run is None:
        return None
    run.conversation = conversation
    run.last_heartbeat_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(run)
    return run


async def update_attempt_result(
    db: AsyncSession, protocol_run_id: uuid.UUID, *, fields: dict[str, Any]
) -> ProtocolRun | None:
    """Replace named immutable-result facets for one execution attempt.

    ``metric_values`` is deliberately assigned as a whole, never merged with
    an earlier attempt's values.  Other callers may add independently named
    result facets without overwriting the stored node timeline.
    """
    run = await get_protocol_run(db, protocol_run_id)
    if run is None:
        return None
    result = dict(run.attempt_result or {})
    result.update(fields)
    run.attempt_result = result
    await db.flush()
    await db.refresh(run)
    return run


async def record_measurement_evaluation(
    db: AsyncSession, protocol_run_id: uuid.UUID, evaluation: MeasurementEvaluation
) -> ProtocolRun | None:
    """Freeze one engine result on its attempt and update only its current projection.

    A later attempt writes its own ``ProtocolRun.attempt_result`` and may replace
    the replicate projection, but it never edits this attempt's document.
    """
    run = await get_protocol_run(db, protocol_run_id)
    if run is None:
        return None
    if evaluation.attempt_id != str(run.id):
        raise ValueError("measurement evaluation attempt does not match the protocol run")
    expected_subject_id = str(run.replicate_result_id or run.id)
    if evaluation.replicate_id != expected_subject_id:
        raise ValueError("measurement evaluation replicate does not match the protocol run")

    attempt_result = dict(run.attempt_result or {})
    if "measurement" in attempt_result:
        raise ValueError("measurement evaluation is immutable once recorded")
    document = evaluation.to_document()
    attempt_result["measurement"] = document
    attempt_result["evaluation_completed_at"] = datetime.now(UTC).isoformat()
    measured_values = {
        observation.metric_name: observation.value
        for observation in evaluation.observations
        if observation.status == "measured" and observation.producer.kind != "runtime"
    }
    if measured_values:
        attempt_result["metric_values"] = {
            **(attempt_result.get("metric_values") or {}),
            **measured_values,
        }
    run.attempt_result = attempt_result

    # Canvas Test Runs and per-node Play runs retain the immutable measurement
    # document on the run itself; neither projects into a factorial replicate.
    if run.is_test_run or run.target_node_id is not None:
        await db.flush()
        await db.refresh(run)
        return run

    protocol = await db.get(Protocol, run.protocol_id)
    if protocol is None or protocol.experiment_id is None or run.replicate_label is None:
        raise ValueError("measurement evaluation run is not scoped to an experiment replicate")
    replicate = await get_replicate(
        db,
        experiment_id=protocol.experiment_id,
        replicate_label=run.replicate_label,
        revision_id=run.design_revision_id,
    )
    if replicate is None or replicate.id != run.replicate_result_id:
        raise ValueError("measurement evaluation replicate is outside the run's design scope")
    if replicate.run_id == run.id:
        artifacts = dict(replicate.artifacts or {})
        artifacts["measurement"] = evaluation.to_document()
        replicate.artifacts = artifacts
        if measured_values:
            replicate.metric_values = {**(replicate.metric_values or {}), **measured_values}
    await db.flush()
    await db.refresh(run)
    return run


async def is_current_replicate_attempt(db: AsyncSession, protocol_run_id: uuid.UUID) -> bool:
    """Whether this run still owns its replicate slot's latest projection."""
    run = await get_protocol_run(db, protocol_run_id)
    if run is None or run.replicate_result_id is None:
        return False
    replicate = await db.get(FactorialReplicateResult, run.replicate_result_id)
    return replicate is not None and replicate.run_id == run.id


async def request_protocol_run_cancellation(db: AsyncSession, protocol_run_id: uuid.UUID) -> ProtocolRun | None:
    """Flags a non-terminal run for cancellation from outside the executor
    -- a no-op if it's already terminal (mirrors fail_protocol_run's own
    race-safety). Does NOT change status itself: the run's task executor and
    built-in measurement finalizer poll cancel_requested_at and safely retain
    any work that completed before cancellation."""
    run = await get_protocol_run(db, protocol_run_id)
    if run is None or run.status in TERMINAL_PROTOCOL_RUN_STATUSES:
        return run
    run.cancel_requested_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(run)
    return run


async def fail_protocol_run(db: AsyncSession, protocol_run_id: uuid.UUID, *, error: str) -> ProtocolRun | None:
    """Force-fail a non-terminal run from outside the executor -- a no-op if
    already terminal, race-safe against a slow-but-live executor's own
    completion commit (mirrors Motoro's ``fail_run``)."""
    run = await get_protocol_run(db, protocol_run_id)
    if run is None or run.status in TERMINAL_PROTOCOL_RUN_STATUSES:
        return run
    _apply_status(run, status="failed", error=error, now=datetime.now(UTC))
    await db.flush()
    await db.refresh(run)
    await _finalize_unsuccessful_measurement(db, run)
    return run


@dataclass
class ExperimentTrial:
    """One row of the Runs tab's trial list -- "trial" means one replicate,
    not "ProtocolRun": a replicate that's never been run is still a trial
    (status "not_started"), which a query
    scoped to ProtocolRun rows alone would miss entirely."""

    replicate_label: str
    factor_values: dict[str, Any]
    metric_values: dict[str, Any]
    status: str  # "not_started" | "pending" | "running" | "finalizing" | "completed" | "failed"
    run_id: uuid.UUID | None
    # The run used an older immutable published canvas revision than the
    # protocol's current one. This is derived on read, preserving the run's
    # actual lifecycle status and history rather than mutating either.
    obsolete: bool
    error: str | None
    updated_at: datetime


async def list_experiment_trials(
    db: AsyncSession, *, experiment_id: uuid.UUID, revision_id: uuid.UUID | None = None
) -> list[ExperimentTrial]:
    """Every cell of *experiment_id*'s current design (or of *revision_id*,
    to inspect a superseded one), cross-referenced with its most recent run
    (``FactorialReplicateResult.run_id`` is kept pointing at the latest
    ``ProtocolRun`` that touched the cell -- see ``run_protocol``'s pre-write
    in services.protocol_execution) for status/error/timestamp. A cell can be
    scored without ever having gone through a ProtocolRun at all (e.g.
    upserted directly by a notebook) -- such a cell has no run_id but real
    metric_values, and is reported "completed" rather than "not_started".

    Goes through ``factorial_cells.list_replicates`` rather than querying
    ``FactorialReplicateResult`` without joining its cell: that query would also
    return every superseded design's cells, which is exactly what design
    revisions exist to keep out of the current view."""
    replicates = await list_replicates(db, experiment_id=experiment_id, revision_id=revision_id)
    run_ids = [replicate.run_id for replicate in replicates if replicate.run_id is not None]
    runs_by_id: dict[uuid.UUID, ProtocolRun] = {}
    if run_ids:
        result = await db.execute(select(ProtocolRun).where(ProtocolRun.id.in_(run_ids)))
        runs_by_id = {r.id: r for r in result.scalars().all()}

    protocol_ids = {run.protocol_id for run in runs_by_id.values()}
    # Keep the publication timestamp too.  Every new cell run pins its
    # revision, but pre-revision records have no such ID.  For those legacy
    # records we can still safely tell that a later canvas publication made
    # the result stale by comparing the run's creation time to the current
    # published revision's timestamp.
    current_revisions: dict[uuid.UUID, tuple[uuid.UUID | None, datetime | None]] = {}
    if protocol_ids:
        result = await db.execute(
            select(Protocol.id, Protocol.published_revision_id, ProtocolRevision.published_at)
            .outerjoin(ProtocolRevision, Protocol.published_revision_id == ProtocolRevision.id)
            .where(Protocol.id.in_(protocol_ids))
        )
        current_revisions = {
            protocol_id: (revision_id, published_at) for protocol_id, revision_id, published_at in result.all()
        }

    trials = []
    for replicate in replicates:
        run = runs_by_id.get(replicate.run_id) if replicate.run_id else None
        current_revision = current_revisions.get(run.protocol_id) if run is not None else None
        current_revision_id, published_at = current_revision or (None, None)
        obsolete = (
            run is not None
            and current_revision_id is not None
            and (
                # Normal case: a run explicitly records the immutable canvas it
                # executed against.
                (run.protocol_revision_id is not None and run.protocol_revision_id != current_revision_id)
                # Compatibility case: rows made before that provenance column was
                # populated.  A current canvas published after the run began is
                # necessarily a newer version than the one it could have used.
                or (run.protocol_revision_id is None and published_at is not None and run.created_at < published_at)
            )
        )
        if run is not None:
            status = run.status
            error = run.error
            updated_at = run.updated_at
        elif replicate.metric_values:
            status, error, updated_at = "completed", None, replicate.updated_at
        else:
            status, error, updated_at = "not_started", None, replicate.updated_at
        trials.append(
            ExperimentTrial(
                replicate_label=replicate.replicate_label,
                factor_values=replicate.factor_values or {},
                metric_values=replicate.metric_values or {},
                status=status,
                run_id=replicate.run_id,
                obsolete=obsolete,
                error=error,
                updated_at=updated_at,
            )
        )
    return trials
