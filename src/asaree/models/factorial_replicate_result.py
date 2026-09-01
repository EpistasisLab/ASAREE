"""The result of one replicate within a factorial cell."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asaree.models.base import Base, TimestampMixin, generate_uuid
from asaree.models.factorial_cell import FactorialCell


class FactorialReplicateResult(Base, TimestampMixin):
    __tablename__ = "factorial_replicate_results"
    __table_args__ = (
        UniqueConstraint("cell_id", "replicate_number", name="uq_factorial_replicate_results_cell_number"),
        UniqueConstraint("cell_id", "replicate_label", name="uq_factorial_replicate_results_cell_label"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    cell_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factorial_cells.id", ondelete="CASCADE"), nullable=False, index=True
    )
    replicate_number: Mapped[int] = mapped_column(Integer, nullable=False)
    replicate_label: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metric_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    artifacts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    cell: Mapped[FactorialCell] = relationship(back_populates="replicates")

    @property
    def cell_label(self) -> str:
        return self.cell.cell_label

    @property
    def experiment_id(self) -> uuid.UUID:
        return self.cell.experiment_id

    @property
    def design_revision_id(self) -> uuid.UUID:
        return self.cell.design_revision_id

    @property
    def factor_values(self) -> dict[str, Any] | None:
        return self.cell.factor_values

__all__ = ["FactorialReplicateResult"]
