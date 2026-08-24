"""Research experiment creation and lookup."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_dataset import ExperimentDataset

# No "dataset_id" here any more -- an experiment's datasets are rows in
# experiment_datasets, not a column, so they're written by
# set_experiment_datasets rather than by this generic setattr path.
_SETTABLE_FIELDS = frozenset({"name", "description", "hypothesis", "design_spec", "archived_at"})

# Placeholder-name allocation for the GUI's one-click create (no
# name/description gate, you rename on the canvas). The browser used to pick
# this name itself by scanning GET /experiments, which is unfixable from the
# client: the list it reads and the POST it then sends are two round trips, so
# any name it computes is a guess about a shared, mutable namespace. It guessed
# wrong in two distinct ways -- the list hid archived experiments while
# uq_research_experiments_owner_name still reserved their names, and a second
# tab/SDK caller could take the name in between -- and the only symptom was an
# opaque 409. Allocating here instead closes both: the read and the insert are
# one transaction, and Postgres serializes the rest.
_UNTITLED_PREFIX = "Untitled Experiment"
# A bare, un-numbered "Untitled Experiment" predates this numbering; count it as
# 1 so it is never handed out a second time.
_UNTITLED_PATTERN = re.compile(rf"^{re.escape(_UNTITLED_PREFIX)}(?: (\d+))?$")
_UNIQUE_NAME_INDEX = "uq_research_experiments_owner_name"
# Each retry means losing a race to a concurrent create for the SAME owner, so
# even two is generous; this is a backstop, not an expected path.
_ALLOCATION_ATTEMPTS = 5


class ExperimentNameAllocationError(RuntimeError):
    """Raised when :func:`create_untitled_experiment` lost the insert race
    ``_ALLOCATION_ATTEMPTS`` times in a row -- one owner would have to be
    creating experiments from five sessions at once, so treat it as a bug or a
    runaway client rather than something to handle."""


async def create_experiment(
    db: AsyncSession,
    *,
    name: str,
    owner_id: uuid.UUID,
    description: str | None = None,
    design_type: str = "factorial",
    task_brief: dict[str, Any] | None = None,
    design_spec: dict[str, Any] | None = None,
    dataset_ids: Sequence[uuid.UUID] | None = None,
) -> ResearchExperiment:
    experiment = ResearchExperiment(
        name=name,
        description=description,
        design_type=design_type,
        task_brief=task_brief,
        design_spec=design_spec,
        owner_id=owner_id,
    )
    db.add(experiment)
    await db.flush()
    if dataset_ids:
        await set_experiment_datasets(db, experiment.id, dataset_ids)
    await db.refresh(experiment)
    return experiment


async def _next_untitled_number(db: AsyncSession, owner_id: uuid.UUID) -> int:
    """Lowest free N for this owner's ``Untitled Experiment N``.

    Deliberately does NOT filter archived rows: the unique index has no
    ``archived_at`` predicate, so an archived experiment still owns its name and
    handing it out again would 409 (and un-archiving would collide). Reserved,
    not reusable, is the consistent reading.
    """
    names = (
        (
            await db.execute(
                select(ResearchExperiment.name).where(
                    ResearchExperiment.owner_id == owner_id,
                    ResearchExperiment.name.like(f"{_UNTITLED_PREFIX}%"),
                )
            )
        )
        .scalars()
        .all()
    )
    highest = 0
    for name in names:
        match = _UNTITLED_PATTERN.match(name)
        if match is None:  # e.g. "Untitled Experiment Old" -- prefixed, but not ours to number
            continue
        highest = max(highest, int(match[1]) if match[1] else 1)
    return highest + 1


async def create_untitled_experiment(db: AsyncSession, *, owner_id: uuid.UUID, **fields: Any) -> ResearchExperiment:
    """Create an experiment under the next free placeholder name -- see the
    ``_UNTITLED_PREFIX`` comment for why the server owns this and not the GUI.

    Airtight rather than merely unlikely: the only true guard is the unique
    index itself, so this inserts optimistically and retries on the violation.
    Each attempt runs in its own SAVEPOINT so a loser rolls back just its own
    failed insert, leaving the caller's transaction (and anything it did before
    this call) intact.
    """
    for _ in range(_ALLOCATION_ATTEMPTS):
        name = f"{_UNTITLED_PREFIX} {await _next_untitled_number(db, owner_id)}"
        try:
            async with db.begin_nested():
                return await create_experiment(db, name=name, owner_id=owner_id, **fields)
        except IntegrityError as exc:
            # Only a lost name race is retryable; anything else (a bad
            # owner_id FK, say) would just fail identically five times.
            if _UNIQUE_NAME_INDEX not in str(exc.orig):
                raise
    raise ExperimentNameAllocationError(
        f"could not allocate an unused {_UNTITLED_PREFIX!r} name after {_ALLOCATION_ATTEMPTS} attempts"
    )


async def update_experiment(
    db: AsyncSession, experiment_id: uuid.UUID, *, fields: dict[str, Any]
) -> ResearchExperiment | None:
    """Set whichever fields the caller passed (an ``exclude_unset`` dump from
    the API layer) -- e.g. ``name``/``description`` from renaming an
    experiment created with a placeholder name straight from the GUI,
    ``design_spec`` (a full replacement, not a merge) from the protocol
    canvas's factor-binding UI, or ``archived_at`` (a timestamp to archive,
    ``None`` to unarchive) from the canvas menu's Archive/Unarchive action.
    Same allow-listed setattr idiom as ``services.protocols.update_protocol``.

    Datasets are NOT settable here -- they're join-table rows now, not a
    column; use :func:`set_experiment_datasets` (the API layer's PATCH handler
    calls both).
    """
    unknown = set(fields) - _SETTABLE_FIELDS
    if unknown:
        raise ValueError(f"not settable on an experiment: {sorted(unknown)}")

    experiment = await get_experiment(db, experiment_id)
    if experiment is None:
        return None
    for key, value in fields.items():
        setattr(experiment, key, value)
    await db.flush()
    await db.refresh(experiment)
    return experiment


async def set_experiment_datasets(
    db: AsyncSession, experiment_id: uuid.UUID, dataset_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """Replace this experiment's whole dataset list, in the given order.

    Replacement rather than merge, matching how ``design_spec`` and
    ``Protocol.graph`` are already PATCHed: the caller (the protocol canvas)
    knows the complete set of Dataset nodes wired into the graph, so sending
    that set is both the add path and the remove path. Duplicates are dropped
    while keeping first-seen order -- two Dataset nodes naming the same
    registered dataset is a legal graph (the run de-dupes it too), just not
    two rows here.

    Ownership is NOT checked here; the API layer validates every id against
    the caller before calling this, the same split ``_validated_dataset_ids``
    already used.
    """
    ordered: list[uuid.UUID] = []
    for dataset_id in dataset_ids:
        if dataset_id not in ordered:
            ordered.append(dataset_id)

    await db.execute(delete(ExperimentDataset).where(ExperimentDataset.experiment_id == experiment_id))
    for position, dataset_id in enumerate(ordered):
        db.add(ExperimentDataset(experiment_id=experiment_id, dataset_id=dataset_id, position=position))
    await db.flush()
    return ordered


async def get_experiment_dataset_ids(db: AsyncSession, experiment_id: uuid.UUID) -> list[uuid.UUID]:
    """This experiment's datasets in canvas wiring order (see
    ``ExperimentDataset.position``); ``[]`` when none are attached."""
    return list(
        (
            await db.execute(
                select(ExperimentDataset.dataset_id)
                .where(ExperimentDataset.experiment_id == experiment_id)
                .order_by(ExperimentDataset.position)
            )
        )
        .scalars()
        .all()
    )


async def get_dataset_ids_by_experiment(
    db: AsyncSession, experiment_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """The same thing for many experiments in one query -- what
    ``GET /experiments`` uses so listing N experiments doesn't cost N+1
    round trips. Experiments with no datasets are simply absent from the
    result; callers default them to ``[]``."""
    if not experiment_ids:
        return {}
    rows = (
        await db.execute(
            select(ExperimentDataset.experiment_id, ExperimentDataset.dataset_id)
            .where(ExperimentDataset.experiment_id.in_(experiment_ids))
            .order_by(ExperimentDataset.experiment_id, ExperimentDataset.position)
        )
    ).all()
    by_experiment: dict[uuid.UUID, list[uuid.UUID]] = {}
    for experiment_id, dataset_id in rows:
        by_experiment.setdefault(experiment_id, []).append(dataset_id)
    return by_experiment


async def get_experiment(db: AsyncSession, experiment_id: uuid.UUID) -> ResearchExperiment | None:
    return (
        await db.execute(select(ResearchExperiment).where(ResearchExperiment.id == experiment_id))
    ).scalar_one_or_none()


async def get_experiment_by_name(db: AsyncSession, name: str, *, owner_id: uuid.UUID) -> ResearchExperiment | None:
    """Fetch an owner's experiment by name, or ``None``.

    Names are unique per owner (uq_research_experiments_owner_name), not per
    installation, so this always scopes to the caller — there is no
    any-owner variant, unlike Motoro's Agent/MCPServerConfig lookups,
    since every call site here is a per-owner conflict pre-check."""
    return (
        await db.execute(
            select(ResearchExperiment).where(
                ResearchExperiment.name == name, ResearchExperiment.owner_id == owner_id
            )
        )
    ).scalar_one_or_none()


async def list_experiments(
    db: AsyncSession, *, owner_id: uuid.UUID, include_archived: bool = False
) -> Sequence[ResearchExperiment]:
    query = select(ResearchExperiment).where(ResearchExperiment.owner_id == owner_id)
    if not include_archived:
        query = query.where(ResearchExperiment.archived_at.is_(None))
    return (await db.execute(query)).scalars().all()


async def delete_experiment(db: AsyncSession, experiment_id: uuid.UUID) -> bool:
    """Delete the experiment, cascading to every cell result FK'd to it."""
    experiment = await get_experiment(db, experiment_id)
    if experiment is None:
        return False
    await db.delete(experiment)
    await db.flush()
    return True
