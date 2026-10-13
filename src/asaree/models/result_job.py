"""Durable commands; intent, execution creation and checkpoints share a database."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class ResultJob(Base, TimestampMixin):
    __tablename__ = "result_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key"),
        Index("ix_result_jobs_pending", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_experiments.id", ondelete="CASCADE"))
    protocol_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protocols.id", ondelete="CASCADE"))
    protocol_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("protocol_revisions.id", ondelete="CASCADE")
    )
    design_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_design_revisions.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    request_key: Mapped[str] = mapped_column(String(100))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResultJobMember(Base):
    __tablename__ = "result_job_members"
    __table_args__ = (Index("ix_result_job_members_pending", "job_id", "processed", "replicate_id", "row_index"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("result_jobs.id", ondelete="CASCADE"), primary_key=True)
    replicate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("factorial_replicate_results.id", ondelete="CASCADE"), primary_key=True
    )
    row_index: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=-1)
    slot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    expected_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    processed: Mapped[bool] = mapped_column(default=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protocol_runs.id", ondelete="SET NULL"), nullable=True)
    # Redis delivery is retried until the authoritative run has left pending.
    delivered: Mapped[bool] = mapped_column(default=False)
