"""One experiment cell's durable result.

Revised after tracing every field back to whether it's genuinely generic
across any ASAREE experiment, or specific to this one use case's pipeline.
Almost everything was the latter — the hyperparameter payload, SHA integrity
guards, process-metric detail, permutation importances, package versions,
class distribution — none of it is read or interpreted by ASAREE itself, all
of it is analyst-facing data one particular use case wants recorded. Naming
each as its own column was the same mistake twice over (factors, then
primary_metric): a schema that hardcodes one use case's shape isn't a
platform primitive, it's the notebook's assumptions relocated.

Three JSON fields, by role, not by use case:

- ``factor_values`` — what was varied (the independent variables).
- ``metric_values`` — what was measured (the dependent variable(s); what a
  future analysis feature fits against). Not "primary_metric": the vision
  itself describes multiple named metrics (accuracy, cost, latency,
  robustness), and which one is "primary" is a per-analysis choice, not a
  fact to freeze into the schema.
- ``artifacts`` — everything else a use case wants durably recorded (payload,
  SHA guards, process metrics, importances, decisions, versions, ...).
  Opaque to ASAREE, exactly like ``ResearchExperiment.task_brief`` already is.

The two-phase durability property this table exists for (a pre-scoring write
survives a failed/interrupted score; a later post-scoring write adds to it
without erasing it) now has to happen at the JSON level, not the column
level — see ``services.factorial_cells.upsert_cell``, which merges into each
of these three dicts rather than replacing them wholesale.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class FactorialCellResult(Base, TimestampMixin):
    __tablename__ = "factorial_cell_results"
    __table_args__ = (
        UniqueConstraint("experiment_id", "cell_label", name="uq_factorial_cell_results_experiment_cell"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cell_label: Mapped[str] = mapped_column(String(255), nullable=False)

    # Opaque, no FK — the Motoro run that produced this cell, in core's
    # own database. Postgres cannot check a cross-database reference.
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # Same reasoning, one level further removed: the on-disk workspace this
    # cell's stages committed to. Not a row anywhere at all (design doc §9) —
    # a string path convention, not a foreign key target.
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    factor_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metric_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    artifacts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


__all__ = ["FactorialCellResult"]
