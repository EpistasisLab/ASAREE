"""One factorial cell's durable result — where the data from
`client.runs.update(mlm_run_id, metadata={...})` actually belongs.

Traced the real notebook (`run_cell`/`rescore` in the spinal_surgery use case)
rather than assuming: none of factors/payload/SHAs/test_metrics/process_metrics
are read by agentic-core's runtime — they're analyst-facing, written so a cell
is "self-describing and queryable" later. That's real, structured experiment
data, not a generic bag, so the factors and the primary metric get real
columns; only the genuinely variable-shape parts (the hyperparameter payload,
full metrics, process-metric detail) stay JSON.

Two-phase writes, matching the notebook's own durability property: the
pre-scoring fields (factors, payload, SHA guards) are written before scoring
runs, so a failed/interrupted score can still be replayed (`rescore`) from
durable state with no agent re-run; the post-scoring fields (test_metrics,
importances) are added once scoring completes. The service layer's upsert
merges rather than overwrites, so the second write doesn't erase the first.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
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

    # Opaque, no FK — the agentic-core MLM run that produced this cell, in
    # core's own database. Same reasoning as everywhere else this pattern
    # appears: Postgres cannot check a cross-database reference.
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # Same reasoning again, one level further removed: the on-disk workspace
    # this cell's DC/FTE/FS stages committed to. Not a row anywhere at all
    # (see design doc §9) — a string path convention, not a foreign key target.
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Factors — real columns because these are exactly what analysis groups
    # and filters by (Step 11's per-cell rollup), not opaque data.
    tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    critic: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    replicate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The primary output of the whole benchmark — real column so it can be
    # sorted/filtered directly instead of unpacked out of test_metrics JSON
    # every time.
    primary_metric: Mapped[float | None] = mapped_column(Numeric(precision=18, scale=10), nullable=True)

    # Pre-scoring (written durably before score_payload runs, so a failed or
    # interrupted score can be replayed with no agent re-run).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    payload_sanitize_notes: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    process_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expected_payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_script_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Post-scoring.
    test_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    permutation_importance_top15: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    model_decisions: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    package_versions: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    test_class_distribution: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    n_test: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = ["FactorialCellResult"]
