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
) -> ResearchExperiment:
    experiment = ResearchExperiment(
        name=name, description=description, design_type=design_type, task_brief=task_brief, owner_id=owner_id
    )
    db.add(experiment)
    await db.flush()
    await db.refresh(experiment)
    return experiment


async def get_experiment(db: AsyncSession, experiment_id: uuid.UUID) -> ResearchExperiment | None:
    return (
        await db.execute(select(ResearchExperiment).where(ResearchExperiment.id == experiment_id))
    ).scalar_one_or_none()


async def get_experiment_by_name(db: AsyncSession, name: str) -> ResearchExperiment | None:
    return (await db.execute(select(ResearchExperiment).where(ResearchExperiment.name == name))).scalar_one_or_none()


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
