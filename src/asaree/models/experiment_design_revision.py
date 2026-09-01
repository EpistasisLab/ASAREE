"""One generation of an experiment's design.

A ``FactorialCell`` and its replicate results belong to a particular design.
Before this table, cells hung directly off the experiment, which quietly
asserted that an experiment has exactly one design for all time -- so
regenerating after a factor change left the previous design's cells behind
with nothing to distinguish them. They still counted toward "0/6 scored" and
"Run all cells" still ran them, because every reader saw one undifferentiated
pile of rows.

Revisions make the two questions separable: "what is this experiment's design
*now*" (the row with ``superseded_at IS NULL``) and "what did we observe under
the design we had before" (the superseded ones). Nothing is destroyed by a
design change, and nothing stale leaks into the current view.

A revision is created only when the generated set of cell labels actually
changes -- see ``services.design_generation.generate_design_cells``.
Re-generating an unchanged design reuses the current revision rather than
piling up empty near-duplicates, and edits that don't affect which cells exist
(``randomization_seed``, a renamed metric) don't supersede anything.

Deleting a revision cascades through ``FactorialCell.design_revision_id`` to
its replicate results rather than relying on application code --
history is user-deletable (see ``services.design_revisions.delete_revision``),
and a delete that left orphaned cells behind would recreate the exact bug this
table exists to fix.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class ExperimentDesignRevision(Base, TimestampMixin):
    __tablename__ = "experiment_design_revisions"
    __table_args__ = (
        UniqueConstraint("experiment_id", "revision", name="uq_experiment_design_revisions_experiment_revision"),
        # At most one current revision per experiment. A partial unique index
        # rather than a bare flag column: "current" is a property only one row
        # may hold, and letting Postgres enforce that is what stops a
        # half-failed supersede from leaving two live designs behind -- which
        # would put the cell set right back into the ambiguous state.
        Index(
            "uq_experiment_design_revisions_one_current",
            "experiment_id",
            unique=True,
            postgresql_where=("superseded_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 1-based and per-experiment, so it's the number a user can actually be
    # shown ("Design 2"). Not globally sequential and not the PK -- the PK
    # stays a UUID like every other table here.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # The ``ResearchExperiment.design_spec`` that produced this revision,
    # copied at generation time. The experiment's own column keeps moving as
    # the user edits; this snapshot is what makes a superseded revision's
    # results interpretable later ("these numbers came from *these* levels").
    design_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Null = this is the experiment's current design. Set when a later
    # revision replaces it. A timestamp rather than a bool gives "when" for
    # free -- same reasoning as ResearchExperiment.archived_at.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["ExperimentDesignRevision"]
