"""Creating, reading, and deleting experiment-level artifacts.

Unlike a cell (``services.factorial_cells``), an artifact is create-once/
append-style, never an upsert target -- there's no merge here, just plain
create/list/get/delete.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.experiment_artifact import ExperimentArtifact


async def create_artifact(
    db: AsyncSession, *, experiment_id: uuid.UUID, name: str, kind: str, content: str
) -> ExperimentArtifact:
    artifact = ExperimentArtifact(experiment_id=experiment_id, name=name, kind=kind, content=content)
    db.add(artifact)
    await db.flush()
    await db.refresh(artifact)
    return artifact


async def get_artifact(
    db: AsyncSession, *, experiment_id: uuid.UUID, artifact_id: uuid.UUID
) -> ExperimentArtifact | None:
    return (
        await db.execute(
            select(ExperimentArtifact).where(
                ExperimentArtifact.experiment_id == experiment_id, ExperimentArtifact.id == artifact_id
            )
        )
    ).scalar_one_or_none()


async def list_artifacts(db: AsyncSession, *, experiment_id: uuid.UUID) -> Sequence[ExperimentArtifact]:
    return (
        (
            await db.execute(
                select(ExperimentArtifact)
                .where(ExperimentArtifact.experiment_id == experiment_id)
                .order_by(ExperimentArtifact.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def delete_artifact(db: AsyncSession, *, artifact_id: uuid.UUID) -> None:
    artifact = await db.get(ExperimentArtifact, artifact_id)
    if artifact is not None:
        await db.delete(artifact)
        await db.flush()
