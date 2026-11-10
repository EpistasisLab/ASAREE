"""Retained job dependencies, including jobs created before publication pins."""

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.result_job import ResultJob
from asaree.services.dataset_row_inputs import resolve_dataset_row_plan

RESUMABLE = ("pending", "queued", "awaiting_confirmation")


async def job_sources(
    db: AsyncSession, owner_id: uuid.UUID | None = None, *, include_terminal: bool = False,
) -> AsyncIterator[tuple[ResultJob, str]]:
    query = select(ResultJob, ProtocolRevision.graph, Protocol.graph).join(
        Protocol, Protocol.id == ResultJob.protocol_id,
    ).outerjoin(ProtocolRevision, ProtocolRevision.id == ResultJob.protocol_revision_id).where(ResultJob.kind == "plan")
    if owner_id is not None:
        query = query.where(ResultJob.owner_id == owner_id)
    if not include_terminal:
        query = query.where(ResultJob.status.in_(RESUMABLE))
    # Stream, rather than materializing every job's graph/progress at once.
    async for job, published, draft in await db.stream(query):
        saved = (job.progress or {}).get("source")
        if saved:
            yield job, saved["dataset_id"]
        elif job.status in RESUMABLE:
            plan = resolve_dataset_row_plan(published if published is not None else draft)
            if plan is not None:
                yield job, plan["driver_dataset_id"]


async def required_by_job(db: AsyncSession, dataset_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
    async for _job, source_id in job_sources(db, owner_id):
        if source_id == str(dataset_id):
            return True
    return False
