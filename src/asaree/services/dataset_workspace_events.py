"""Recording and reading dataset workspace lineage — see design doc §9."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.dataset_workspace_event import DatasetWorkspaceEvent, WorkspaceEventType


async def record_event(
    db: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    workspace_id: str,
    stage: str,
    event_type: WorkspaceEventType,
    sha256_train: str | None = None,
    sha256_test: str | None = None,
) -> DatasetWorkspaceEvent:
    event = DatasetWorkspaceEvent(
        dataset_id=dataset_id,
        workspace_id=workspace_id,
        stage=stage,
        event_type=event_type,
        sha256_train=sha256_train,
        sha256_test=sha256_test,
    )
    db.add(event)
    await db.flush()
    await db.refresh(event)
    return event


async def list_events(
    db: AsyncSession, *, dataset_id: uuid.UUID, workspace_id: str | None = None
) -> Sequence[DatasetWorkspaceEvent]:
    stmt = select(DatasetWorkspaceEvent).where(DatasetWorkspaceEvent.dataset_id == dataset_id)
    if workspace_id is not None:
        stmt = stmt.where(DatasetWorkspaceEvent.workspace_id == workspace_id)
    stmt = stmt.order_by(DatasetWorkspaceEvent.created_at)
    return (await db.execute(stmt)).scalars().all()
