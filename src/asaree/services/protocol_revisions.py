"""Publishing and reading immutable protocol canvas revisions."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun


async def get_revision(db: AsyncSession, revision_id: uuid.UUID) -> ProtocolRevision | None:
    return await db.get(ProtocolRevision, revision_id)


async def get_published_revision(db: AsyncSession, protocol: Protocol) -> ProtocolRevision | None:
    if protocol.published_revision_id is None:
        return None
    return await get_revision(db, protocol.published_revision_id)


async def publish_protocol(db: AsyncSession, protocol: Protocol) -> ProtocolRevision:
    """Freeze the protocol's current draft as its next production revision."""
    highest = (
        await db.execute(
            select(func.max(ProtocolRevision.revision)).where(ProtocolRevision.protocol_id == protocol.id)
        )
    ).scalar_one()
    revision = ProtocolRevision(
        protocol_id=protocol.id,
        revision=(highest or 0) + 1,
        graph=copy.deepcopy(protocol.graph),
        published_at=datetime.now(UTC),
    )
    db.add(revision)
    await db.flush()
    protocol.published_revision_id = revision.id
    await db.flush()
    # A published canvas creates a new definition of "current". Preserve any
    # old latest-attempt score on that attempt itself, then clear the mutable
    # replicate projection so Results/CSV/top-bar chips cannot keep treating
    # an older canvas execution as current.
    stale_pairs = (
        await db.execute(
            select(ProtocolRun, FactorialReplicateResult)
            .join(FactorialReplicateResult, FactorialReplicateResult.run_id == ProtocolRun.id)
            .where(ProtocolRun.protocol_id == protocol.id)
        )
    ).all()
    for run, replicate in stale_pairs:
        stale = (
            (run.protocol_revision_id is not None and run.protocol_revision_id != revision.id)
            or (run.protocol_revision_id is None and run.created_at < revision.published_at)
        )
        if not stale:
            continue
        snapshot = dict(run.attempt_result or {})
        if "metric_values" not in snapshot and isinstance(replicate.metric_values, dict):
            snapshot["metric_values"] = dict(replicate.metric_values)
        evaluation = (replicate.artifacts or {}).get("metric_evaluation")
        if "metric_evaluation" not in snapshot and isinstance(evaluation, dict):
            snapshot["metric_evaluation"] = dict(evaluation)
        run.attempt_result = snapshot or None
        replicate.metric_values = None
        replicate.artifacts = None
    await db.flush()
    # TimestampMixin's server-side update can expire fields such as
    # ``updated_at`` on the existing Protocol instance. Refresh while still
    # inside AsyncSession so the API response never tries an implicit lazy
    # load (which would raise MissingGreenlet).
    await db.refresh(protocol)
    return revision


def is_draft_published(protocol: Protocol, published: ProtocolRevision | None) -> bool:
    return published is not None and protocol.graph == published.graph


__all__ = ["get_published_revision", "get_revision", "is_draft_published", "publish_protocol"]
