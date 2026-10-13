"""Rebuildable, version-scoped summaries; attempt documents remain authoritative."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base


class ResultRefresh(Base):
    __tablename__ = "result_refreshes"

    scope_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    protocol_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protocols.id", ondelete="CASCADE"),
        index=True,
    )
    protocol_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protocol_revisions.id", ondelete="CASCADE"),
        nullable=True,
    )
    design_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_design_revisions.id", ondelete="CASCADE"),
        nullable=True,
    )
    generation: Mapped[int] = mapped_column(BigInteger, default=1)
    processed_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResultCellRollup(Base):
    __tablename__ = "result_cell_rollups"

    scope_key: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("result_refreshes.scope_key", ondelete="CASCADE"),
        primary_key=True,
    )
    cell_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factorial_cells.id", ondelete="CASCADE"),
        primary_key=True,
    )
    generation: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ResultCellMetric(Base):
    __tablename__ = "result_cell_metrics"
    __table_args__ = (Index("ix_result_cell_metric_rank", "scope_key", "metric_key", "value", "cell_id"),)

    scope_key: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("result_refreshes.scope_key", ondelete="CASCADE"),
        primary_key=True,
    )
    metric_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    cell_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factorial_cells.id", ondelete="CASCADE"),
        primary_key=True,
    )
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation: Mapped[int] = mapped_column(BigInteger)
