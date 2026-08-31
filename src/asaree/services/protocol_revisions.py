"""Publishing and reading immutable protocol canvas revisions."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision


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
    # TimestampMixin's server-side update can expire fields such as
    # ``updated_at`` on the existing Protocol instance. Refresh while still
    # inside AsyncSession so the API response never tries an implicit lazy
    # load (which would raise MissingGreenlet).
    await db.refresh(protocol)
    return revision


def is_draft_published(protocol: Protocol, published: ProtocolRevision | None) -> bool:
    return published is not None and protocol.graph == published.graph


__all__ = ["get_published_revision", "get_revision", "is_draft_published", "publish_protocol"]
