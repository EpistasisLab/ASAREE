"""ProtocolRun creation, lookup, and progress updates.

Mirrors agentic-core's AgentRun lifecycle helpers (create_run/fail_run) --
the same "force-fail from outside, race-safe against a live executor's own
commit" idiom, since ProtocolRun has no other precedent to follow in this
codebase.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.protocol_run import ProtocolRun

_TERMINAL_STATUSES = frozenset({"completed", "failed"})


async def create_protocol_run(
    db: AsyncSession,
    *,
    protocol_id: uuid.UUID,
    owner_id: uuid.UUID,
    cell_label: str | None = None,
    factor_values: dict[str, Any] | None = None,
) -> ProtocolRun:
    """``cell_label``/``factor_values`` are set together only for a run
    created by "run all cells" (``services.protocol_execution.plan_cell_runs``)
    -- both stay ``None`` for a plain graph run, the existing behavior."""
    run = ProtocolRun(
        protocol_id=protocol_id,
        owner_id=owner_id,
        status="pending",
        node_runs={},
        cell_label=cell_label,
        factor_values=factor_values,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def get_protocol_run(db: AsyncSession, protocol_run_id: uuid.UUID) -> ProtocolRun | None:
    return (
        await db.execute(select(ProtocolRun).where(ProtocolRun.id == protocol_run_id))
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
