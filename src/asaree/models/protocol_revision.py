"""Immutable, published snapshots of a protocol canvas.

``Protocol.graph`` is the user's autosaved draft.  A revision is created only
when that draft is explicitly published, and production runs pin one of these
rows so later canvas edits cannot change queued or resumed work.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class ProtocolRevision(Base, TimestampMixin):
    __tablename__ = "protocol_revisions"
    __table_args__ = (
        UniqueConstraint("protocol_id", "revision", name="uq_protocol_revisions_protocol_revision"),
        Index("ix_protocol_revisions_protocol_id", "protocol_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    protocol_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocols.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["ProtocolRevision"]
