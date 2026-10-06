"""One publication freezes the experiment's canvas, settings, and design scope."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.experiment import ResearchExperiment
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.services.design_revisions import get_current_revision


def experiment_settings(experiment: ResearchExperiment) -> dict[str, Any]:
    return copy.deepcopy({
        "hypothesis": experiment.hypothesis,
        "design_type": experiment.design_type,
        "design_spec": experiment.design_spec,
        "measurement_plan": experiment.measurement_plan,
        "task_brief": experiment.task_brief,
    })


async def publication_matches_experiment(
    db: AsyncSession, protocol: Protocol, publication: ProtocolRevision | None
) -> bool:
    if publication is None:
        return False
    if protocol.experiment_id is None:
        return True
    experiment = await db.get(ResearchExperiment, protocol.experiment_id)
    if experiment is None:
        return False
    current = await get_current_revision(db, experiment.id)
    return (
        publication.experiment_snapshot == experiment_settings(experiment)
        and publication.design_revision_id == (current.id if current else None)
    )


def version_design_spec(publication: ProtocolRevision | None, fallback: dict | None) -> dict | None:
    snapshot = publication.experiment_snapshot if publication is not None else None
    return snapshot.get("design_spec") if isinstance(snapshot, dict) else fallback


def version_measurement_plan(publication: ProtocolRevision | None, fallback: dict | None) -> dict | None:
    snapshot = publication.experiment_snapshot if publication is not None else None
    return snapshot.get("measurement_plan") if isinstance(snapshot, dict) else fallback
