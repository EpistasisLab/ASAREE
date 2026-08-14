"""Research experiment creation and lookup."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.experiment import ResearchExperiment

_SETTABLE_FIELDS = frozenset({"name", "description", "hypothesis", "dataset_id", "design_spec", "archived_at"})


async def create_experiment(
    db: AsyncSession,
    *,
    name: str,
    owner_id: uuid.UUID,
    description: str | None = None,
    design_type: str = "factorial",
    task_brief: dict[str, Any] | None = None,
    design_spec: dict[str, Any] | None = None,
    dataset_id: uuid.UUID | None = None,
) -> ResearchExperiment:
    experiment = ResearchExperiment(
        name=name,
        description=description,
        design_type=design_type,
        task_brief=task_brief,
        design_spec=design_spec,
        owner_id=owner_id,
        dataset_id=dataset_id,
    )
    db.add(experiment)
    await db.flush()
    await db.refresh(experiment)
    return experiment


async def update_experiment(
    db: AsyncSession, experiment_id: uuid.UUID, *, fields: dict[str, Any]
) -> ResearchExperiment | None:
    """Set whichever fields the caller passed (an ``exclude_unset`` dump from
    the API layer) -- e.g. ``name``/``description`` from renaming an
    experiment created with a placeholder name straight from the GUI, or
    ``dataset_id`` (including explicit ``None``, to detach) from the
    notebook's Step 2 attach-after-create flow, ``design_spec`` (a full
    replacement, not a merge) from the protocol canvas's factor-binding UI,
    or ``archived_at`` (a timestamp to archive, ``None`` to unarchive) from
    the canvas menu's Archive/Unarchive action. Same allow-listed setattr
    idiom as ``services.protocols.update_protocol``.
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


async def get_experiment(db: AsyncSession, experiment_id: uuid.UUID) -> ResearchExperiment | None:
    return (
        await db.execute(select(ResearchExperiment).where(ResearchExperiment.id == experiment_id))
    ).scalar_one_or_none()


async def get_experiment_by_name(db: AsyncSession, name: str, *, owner_id: uuid.UUID) -> ResearchExperiment | None:
    """Fetch an owner's experiment by name, or ``None``.

    Names are unique per owner (uq_research_experiments_owner_name), not per
    installation, so this always scopes to the caller — there is no
    any-owner variant, unlike agentic-core's Agent/MCPServerConfig lookups,
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
