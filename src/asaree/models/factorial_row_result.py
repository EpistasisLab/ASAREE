"""Immutable identity and latest projection for one original dataset row."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class FactorialRowResult(Base, TimestampMixin):
    __tablename__ = "factorial_row_results"
    __table_args__ = (
        CheckConstraint("row_index >= 0", name="ck_factorial_row_results_row_index_nonnegative"),
        UniqueConstraint(
            "replicate_result_id",
            "protocol_revision_id",
            "dataset_id",
            "raw_sha256",
            "row_index",
            name="uq_factorial_row_results_row_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    replicate_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factorial_replicate_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    protocol_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocol_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registered_datasets.id", ondelete="RESTRICT", name="fk_factorial_row_results_dataset"),
        nullable=False,
        index=True,
    )
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metric_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    artifacts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


__all__ = ["FactorialRowResult"]
