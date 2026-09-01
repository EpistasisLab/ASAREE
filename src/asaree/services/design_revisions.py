"""Reading, creating and deleting an experiment's design revisions.

The current revision is the one with ``superseded_at IS NULL``; there is at
most one per experiment, enforced by a partial unique index rather than by
convention (see models/experiment_design_revision.py).

Two rules worth stating up front, because both are load-bearing elsewhere:

- ``get_or_create_current`` is what makes the notebook flow keep working. A
  client that never calls generate-design and just PUTs cells directly (the
  SDK's ``upsert_replicate``, which notebook pipelines use) still needs a
  revision to hang them off, so one is created on demand from whatever
  ``design_spec`` the experiment currently declares.
- ``delete_revision`` refuses the current revision. Deleting history is a
  user's call; deleting the design they're working in isn't a deletion, it's
  a reset, and it would leave the experiment with cells but no current
  revision. Regenerating the design is the supported way to replace it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult


class DesignRevisionError(ValueError):
    """A revision operation that isn't allowed (e.g. deleting the current one)."""


async def get_current_revision(db: AsyncSession, experiment_id: uuid.UUID) -> ExperimentDesignRevision | None:
    return (
        await db.execute(
            select(ExperimentDesignRevision)
            .where(ExperimentDesignRevision.experiment_id == experiment_id)
            .where(ExperimentDesignRevision.superseded_at.is_(None))
        )
    ).scalar_one_or_none()


async def get_revision(db: AsyncSession, revision_id: uuid.UUID) -> ExperimentDesignRevision | None:
    return await db.get(ExperimentDesignRevision, revision_id)


async def list_revisions(db: AsyncSession, *, experiment_id: uuid.UUID) -> Sequence[ExperimentDesignRevision]:
    """Newest design first -- the current revision leads, then history."""
    return (
        (
            await db.execute(
                select(ExperimentDesignRevision)
                .where(ExperimentDesignRevision.experiment_id == experiment_id)
                .order_by(ExperimentDesignRevision.revision.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_or_create_current(
    db: AsyncSession, experiment_id: uuid.UUID, *, design_spec: dict[str, Any] | None = None
) -> ExperimentDesignRevision:
    """The experiment's current revision, creating revision 1 if it has none.

    *design_spec* defaults to the experiment's own declared spec -- the
    lazily-created revision should record what the design actually was at the
    moment its first cell was written, not an empty snapshot.
    """
    current = await get_current_revision(db, experiment_id)
    if current is not None:
        return current
    if design_spec is None:
        experiment = await db.get(ResearchExperiment, experiment_id)
        design_spec = experiment.design_spec if experiment is not None else None
    return await _create_revision(db, experiment_id=experiment_id, design_spec=design_spec)


async def supersede_and_create(
    db: AsyncSession, *, experiment_id: uuid.UUID, design_spec: dict[str, Any] | None
) -> ExperimentDesignRevision:
    """Retire the current revision and open the next one.

    The flush between the two is deliberate: the partial unique index allows
    only one non-superseded row per experiment, so the new row cannot be
    inserted until the old one's ``superseded_at`` has actually landed.
    """
    current = await get_current_revision(db, experiment_id)
    if current is not None:
        current.superseded_at = datetime.now(UTC)
        await db.flush()
    return await _create_revision(db, experiment_id=experiment_id, design_spec=design_spec)


async def _create_revision(
    db: AsyncSession, *, experiment_id: uuid.UUID, design_spec: dict[str, Any] | None
) -> ExperimentDesignRevision:
    # max()+1 over every revision ever, superseded included -- reusing a
    # deleted revision's number would make two different designs share a
    # label in the history UI.
    highest = (
        await db.execute(
            select(func.max(ExperimentDesignRevision.revision)).where(
                ExperimentDesignRevision.experiment_id == experiment_id
            )
        )
    ).scalar_one_or_none()
    revision = ExperimentDesignRevision(
        experiment_id=experiment_id,
        revision=(highest or 0) + 1,
        design_spec=design_spec,
    )
    db.add(revision)
    await db.flush()
    await db.refresh(revision)
    return revision


async def delete_revision(db: AsyncSession, revision_id: uuid.UUID) -> None:
    """Delete a superseded revision and, by FK cascade, its cells.

    Raises :class:`DesignRevisionError` for the current revision -- see this
    module's docstring.
    """
    revision = await db.get(ExperimentDesignRevision, revision_id)
    if revision is None:
        return
    if revision.superseded_at is None:
        raise DesignRevisionError(
            "This is the experiment's current design. Regenerate the design to replace it, "
            "or delete the experiment to remove it entirely."
        )
    await db.delete(revision)
    await db.flush()


@dataclass
class RevisionSummary:
    """A revision plus cell/replicate tallies the history UI needs, so listing the
    history is one query pair rather than one per revision."""

    revision: ExperimentDesignRevision
    cell_count: int
    replicate_count: int
    scored_replicate_count: int


async def list_revision_summaries(db: AsyncSession, *, experiment_id: uuid.UUID) -> list[RevisionSummary]:
    revisions = await list_revisions(db, experiment_id=experiment_id)
    if not revisions:
        return []
    rows = (
        await db.execute(
            select(
                FactorialCell.design_revision_id,
                FactorialCell.id,
                FactorialReplicateResult.metric_values,
            )
            .join(FactorialReplicateResult, FactorialReplicateResult.cell_id == FactorialCell.id)
            .where(FactorialCell.experiment_id == experiment_id)
        )
    ).all()
    cell_keys: dict[uuid.UUID, set[str]] = {}
    replicate_counts: dict[uuid.UUID, int] = {}
    scored_counts: dict[uuid.UUID, int] = {}
    for revision_id, cell_id, metric_values in rows:
        cell_keys.setdefault(revision_id, set()).add(str(cell_id))
        replicate_counts[revision_id] = replicate_counts.get(revision_id, 0) + 1
        if metric_values:
            scored_counts[revision_id] = scored_counts.get(revision_id, 0) + 1
    return [
        RevisionSummary(
            revision=r,
            cell_count=len(cell_keys.get(r.id, set())),
            replicate_count=replicate_counts.get(r.id, 0),
            scored_replicate_count=scored_counts.get(r.id, 0),
        )
        for r in revisions
    ]


__all__ = [
    "DesignRevisionError",
    "RevisionSummary",
    "delete_revision",
    "get_current_revision",
    "get_or_create_current",
    "get_revision",
    "list_revision_summaries",
    "list_revisions",
    "supersede_and_create",
]
