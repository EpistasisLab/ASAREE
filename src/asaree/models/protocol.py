"""A protocol -- the executable agent/tool graph a visual canvas edits.

Distinct from a ResearchExperiment: an experiment's design_spec says what
varies, a protocol says what runs. The whole node/edge graph is kept as one
opaque JSONB blob rather than normalized Node/Edge/Port tables -- nothing in
ASAREE itself ever queries into the graph's structure, only the canvas UI
(and later, a protocol executor) ever read it, and both read it whole. Same
reasoning FactorialCellResult's JSONB-by-role columns already use.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class Protocol(Base, TimestampMixin):
    __tablename__ = "protocols"
    # Unique per owner, not per installation -- a protocol is a standalone
    # reusable object, matching uq_research_experiments_owner_name.
    __table_args__ = (Index("uq_protocols_owner_name", "owner_id", "name", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {"nodes": [{"id","type","position":{"x","y"},"data":{...}}, ...],
    #  "edges": [{"id","source","target","sourceHandle","targetHandle"}, ...]}
    # Deliberately close to @xyflow/react's own Node/Edge shape (avoids a
    # translation layer) but holds only durable fields -- ephemeral UI state
    # (selected, dragging, measured dimensions) is stripped by the frontend
    # before saving.
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # RESTRICT, not CASCADE: same reasoning as ResearchExperiment.owner_id --
    # deleting a user shouldn't silently discard the protocols they built.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Nullable, SET NULL on delete -- a protocol is a standalone reusable
    # object that MAY be tagged to the experiment it was built for; losing
    # that experiment isn't a reason to lose the protocol. Which experiment
    # a protocol belongs to (if any) is a UX convention the canvas page
    # enforces (one protocol per experiment, created lazily on first visit),
    # not a schema constraint -- reusing one protocol across experiments
    # later needs no migration.
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_experiments.id", ondelete="SET NULL"), nullable=True, index=True
    )


__all__ = ["Protocol"]
