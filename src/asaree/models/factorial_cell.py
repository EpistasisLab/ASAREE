"""A factorial cell: one unique factor combination and all its replicates."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asaree.models.base import Base, TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from asaree.models.factorial_replicate_result import FactorialReplicateResult


class FactorialCell(Base, TimestampMixin):
    __tablename__ = "factorial_cells"
    __table_args__ = (
        UniqueConstraint("design_revision_id", "cell_label", name="uq_factorial_cells_revision_cell"),
        Index(
            "uq_factorial_cells_revision_factors",
            "design_revision_id",
            "factor_values",
            unique=True,
            postgresql_where=("factor_values IS NOT NULL AND factor_values <> '{}'::jsonb"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    design_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_design_revisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cell_label: Mapped[str] = mapped_column(String(255), nullable=False)
    factor_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    replicates: Mapped[list[FactorialReplicateResult]] = relationship(
        back_populates="cell",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="FactorialReplicateResult.replicate_number",
    )


__all__ = ["FactorialCell"]
