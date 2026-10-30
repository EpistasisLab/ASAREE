"""Automatic statistics reports and the immutable inputs they were computed from."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class StatsAnalysis(Base, TimestampMixin):
    __tablename__ = "stats_analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_experiments.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Immutable input/configuration snapshot, including design and replicate provenance.
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB)
