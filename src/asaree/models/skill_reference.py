"""ASAREE retention of registered Motoro skill identities, without content copies."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, generate_uuid


class SkillReference(Base):
    __tablename__ = "skill_references"
    __table_args__ = (
        CheckConstraint("num_nonnulls(protocol_id, publication_id, run_id, experiment_id, design_id) = 1",
                        name="ck_skill_reference_scope"),
        *(Index(f"uq_skill_reference_{scope}", f"{scope}_id", "skill_id", unique=True)
          for scope in ("protocol", "publication", "run", "experiment", "design")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    # Motoro is a separate database; this deliberately is not a foreign key.
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    protocol_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protocols.id", ondelete="CASCADE"))
    publication_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protocol_revisions.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protocol_runs.id", ondelete="CASCADE"))
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_experiments.id", ondelete="CASCADE"))
    design_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_design_revisions.id", ondelete="CASCADE"))
