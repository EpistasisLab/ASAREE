"""Research experiment creation and lookup."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.experiment import ResearchExperiment


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
    db: AsyncSession, experiment_id: uuid.UUID, *, dataset_id: uuid.UUID | None
) -> ResearchExperiment | None:
    """Set ``dataset_id`` on an existing experiment.

    ``dataset_id=None`` here means "detach," not "leave unchanged" -- unlike
    ``update_agent``'s fields, this is the only mutable field on an
    experiment so far, and there's no other way to clear it. Registration
    (Step 2 of the notebook) happens after the experiment is created (Step
    1), so this exists to attach a dataset after the fact rather than only
    at creation time.
    """
    experiment = await get_experiment(db, experiment_id)
    if experiment is None:
        return None
    experiment.dataset_id = dataset_id
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


async def list_experiments(db: AsyncSession, *, owner_id: uuid.UUID) -> Sequence[ResearchExperiment]:
    return (await db.execute(select(ResearchExperiment).where(ResearchExperiment.owner_id == owner_id))).scalars().all()


async def delete_experiment(db: AsyncSession, experiment_id: uuid.UUID) -> bool:
    """Delete the experiment, cascading to every cell result FK'd to it."""
    experiment = await get_experiment(db, experiment_id)
    if experiment is None:
        return False
    await db.delete(experiment)
    await db.flush()
    return True
