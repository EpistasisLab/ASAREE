"""Which registered datasets an experiment runs against — a real many-to-many.

This replaces ``ResearchExperiment.dataset_id``, a scalar FK that encoded
"an experiment has exactly one dataset". That stopped being true once an
agent's Dataset connector was uncapped: several Dataset nodes can wire into
one agent (the same shape Skill and Knowledge already had), so the canvas can
express a comparison across datasets that the FK could not represent. Rather
than keep the FK and let it silently mean "the first one", the relationship
itself changed shape and the column was dropped (migration ``d5a3b90c71e4``,
which backfills one row per existing non-null ``dataset_id``).

Deliberately NOT the same call as CLAUDE.md's "agents are not a stored
relationship". An experiment's datasets are answerable only from the canvas
graph otherwise, and unlike agents (reusable per-user templates that an
experiment merely borrows) a dataset is part of what the experiment IS --
the thing its results are about, and the thing whose deletion should be
visible from the experiment. It also keeps the property the scalar FK had and
the SDK/notebook already depend on: an experiment states its own data, without
a caller having to parse a protocol graph to find out.

``position`` keeps the canvas's own wiring order, so ``dataset_ids`` round-trips
in the order the user actually wired the Dataset nodes rather than an arbitrary
row order. Both FKs CASCADE: this row is a link, not a record worth keeping
once either end is gone (contrast the old FK's SET NULL, which was protecting
the experiment itself).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin


class ExperimentDataset(Base, TimestampMixin):
    __tablename__ = "experiment_datasets"

    # Composite PK, which also enforces the thing the app would otherwise have
    # to: the same dataset can't be attached to one experiment twice.
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_experiments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registered_datasets.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__all__ = ["ExperimentDataset"]
