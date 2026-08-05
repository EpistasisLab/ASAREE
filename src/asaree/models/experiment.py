"""A research experiment — the top-level grouping for a factorial sweep.

The `ResearchExperiment`-equivalent from project_plan/core_asaree_use_case.md
§5, factorial design only (design doc explicitly scopes out ab_experiments,
discoveries, and the rest of ARES's broader experiment types).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class ResearchExperiment(Base, TimestampMixin):
    __tablename__ = "research_experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A plain string, not an enum — same reasoning as UserLLMSetting.provider:
    # this table shouldn't need a migration just because a new design type
    # gets added. Only "factorial" is meaningful today.
    design_type: Mapped[str] = mapped_column(String(32), nullable=False, default="factorial")
    # Kept for provenance — the notebook's task_brief dict, verbatim.
    task_brief: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # RESTRICT, not CASCADE: deleting a user shouldn't silently discard the
    # experiments they ran. Matches RegisteredDataset.owner_id.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


__all__ = ["ResearchExperiment"]
