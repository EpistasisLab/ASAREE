"""ProtocolRun creation, lookup, and progress updates.

Mirrors agentic-core's AgentRun lifecycle helpers (create_run/fail_run) --
the same "force-fail from outside, race-safe against a live executor's own
commit" idiom, since ProtocolRun has no other precedent to follow in this
codebase.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_cell_result import FactorialCellResult
from asaree.models.protocol_run import ProtocolRun

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


async def create_protocol_run(
    db: AsyncSession,
    *,
    protocol_id: uuid.UUID,
    owner_id: uuid.UUID,
    cell_label: str | None = None,
    factor_values: dict[str, Any] | None = None,
    target_node_id: str | None = None,
) -> ProtocolRun:
    """``cell_label``/``factor_values`` are set together only for a run
    created by "run all cells" (``services.protocol_execution.plan_cell_runs``)
    -- both stay ``None`` for a plain graph run, the existing behavior.
    ``target_node_id`` is set only for a single-node "Play" run (see
    ``ProtocolRun`` model's own comment) -- mutually exclusive with
    cell_label/factor_values in practice, though nothing enforces that here."""
    run = ProtocolRun(
        protocol_id=protocol_id,
        owner_id=owner_id,
        status="pending",
        node_runs={},
        cell_label=cell_label,
        factor_values=factor_values,
        target_node_id=target_node_id,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def get_protocol_run(db: AsyncSession, protocol_run_id: uuid.UUID) -> ProtocolRun | None:
    return (
        await db.execute(select(ProtocolRun).where(ProtocolRun.id == protocol_run_id))
    ).scalar_one_or_none()


async def get_cancel_requested_at(db: AsyncSession, protocol_run_id: uuid.UUID) -> datetime | None:
    """Single-column read, not a full get_protocol_run -- this is polled
    every ~1.5s for the duration of a live agent run (see
    services.protocol_execution._poll_cancel_flag) to detect a Stop click
    fast enough to interrupt mid-agent via agentic-core's own cancel_event,
    not just at run_protocol's own between-nodes check. Fetching the whole
    row (and deserializing node_runs' JSONB) on that cadence would be pure
    waste -- this reads nothing else."""
    return (
        await db.execute(select(ProtocolRun.cancel_requested_at).where(ProtocolRun.id == protocol_run_id))
    ).scalar_one_or_none()


async def list_protocol_runs(db: AsyncSession, *, protocol_id: uuid.UUID) -> Sequence[ProtocolRun]:
    return (
        (
            await db.execute(
                select(ProtocolRun)
                .where(ProtocolRun.protocol_id == protocol_id)
                .order_by(ProtocolRun.created_at.desc())
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
    run.status = status
    if error is not None:
        run.error = error
    run.last_heartbeat_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(run)
    return run


async def update_node_run(
    db: AsyncSession, protocol_run_id: uuid.UUID, node_id: str, patch: dict[str, Any]
) -> ProtocolRun | None:
    """Shallow-merge *patch* into ``node_runs[node_id]`` -- the same
    read-modify-write idiom ``upsert_cell`` uses for its JSONB columns, one
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


async def request_protocol_run_cancellation(db: AsyncSession, protocol_run_id: uuid.UUID) -> ProtocolRun | None:
    """Flags a non-terminal run for cancellation from outside the executor
    -- a no-op if it's already terminal (mirrors fail_protocol_run's own
    race-safety). Does NOT change status itself: the run's own node loop
    (services.protocol_execution.run_protocol) polls cancel_requested_at
    between nodes and is the only thing that safely transitions status to
    "cancelled" -- setting it here instead would race a live executor that's
    mid-node and about to write its own status."""
    run = await get_protocol_run(db, protocol_run_id)
    if run is None or run.status in _TERMINAL_STATUSES:
        return run
    run.cancel_requested_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(run)
    return run


async def fail_protocol_run(db: AsyncSession, protocol_run_id: uuid.UUID, *, error: str) -> ProtocolRun | None:
    """Force-fail a non-terminal run from outside the executor -- a no-op if
    already terminal, race-safe against a slow-but-live executor's own
    completion commit (mirrors agentic-core's ``fail_run``)."""
    run = await get_protocol_run(db, protocol_run_id)
    if run is None or run.status in _TERMINAL_STATUSES:
        return run
    run.status = "failed"
    run.error = error
    await db.flush()
    await db.refresh(run)
    return run


@dataclass
class ExperimentTrial:
    """One row of the Runs tab's trial list -- "trial" means cell (one
    factor-level combination x replicate), not "ProtocolRun": a cell that's
    never been run at all is still a trial (status "queued"), which a query
    scoped to ProtocolRun rows alone would miss entirely."""

    cell_label: str
    factor_values: dict[str, Any]
    metric_values: dict[str, Any]
    status: str  # "queued" | "pending" | "running" | "completed" | "failed"
    run_id: uuid.UUID | None
    error: str | None
    updated_at: datetime


async def list_experiment_trials(db: AsyncSession, *, experiment_id: uuid.UUID) -> list[ExperimentTrial]:
    """Every cell under *experiment_id*, cross-referenced with its most
    recent run (``FactorialCellResult.run_id`` is kept pointing at the
    latest ``ProtocolRun`` that touched the cell -- see
    ``run_protocol``'s pre-write in services.protocol_execution) for
    status/error/timestamp. A cell can be scored without ever having gone
    through a ProtocolRun at all (e.g. upserted directly by a notebook) --
    such a cell has no run_id but real metric_values, and is reported
    "completed" rather than "queued"."""
    cells = (
        (
            await db.execute(
                select(FactorialCellResult)
                .where(FactorialCellResult.experiment_id == experiment_id)
                .order_by(FactorialCellResult.cell_label)
            )
        )
        .scalars()
        .all()
    )
    run_ids = [c.run_id for c in cells if c.run_id is not None]
    runs_by_id: dict[uuid.UUID, ProtocolRun] = {}
    if run_ids:
        result = await db.execute(select(ProtocolRun).where(ProtocolRun.id.in_(run_ids)))
        runs_by_id = {r.id: r for r in result.scalars().all()}

    trials = []
    for cell in cells:
        run = runs_by_id.get(cell.run_id) if cell.run_id else None
        if run is not None:
            status = run.status
            error = run.error
            updated_at = run.updated_at
        elif cell.metric_values:
            status, error, updated_at = "completed", None, cell.updated_at
        else:
            status, error, updated_at = "queued", None, cell.updated_at
        trials.append(
            ExperimentTrial(
                cell_label=cell.cell_label,
                factor_values=cell.factor_values or {},
                metric_values=cell.metric_values or {},
                status=status,
                run_id=cell.run_id,
                error=error,
                updated_at=updated_at,
            )
        )
    return trials
