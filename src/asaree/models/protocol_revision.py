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
    # An experiment publication freezes the declaration alongside its canvas.
    # Null means a legacy canvas-only publication; never invent its settings.
    experiment_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    design_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        # Defer the check so deleting an experiment can cascade through both
        # its designs and publications in either order. Direct deletion of a
        # published design is refused by the service and by this FK at commit.
        UUID(as_uuid=True), ForeignKey(
            "experiment_design_revisions.id", deferrable=True, initially="DEFERRED"
        ), nullable=True
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["ProtocolRevision"]
