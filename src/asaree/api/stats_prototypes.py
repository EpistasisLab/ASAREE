"""Owner-scoped, database-backed statistics playgrounds."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import defer

from asaree.api.experiments import _get_owned_experiment
from asaree.deps import CurrentUser, DbSession
from asaree.models.stats_analysis import StatsAnalysis
from asaree.services.stats_prototypes import StatsRequest, catalog, prepare_frame, read_source
from asaree.worker.enqueue import _get_pool

router = APIRouter(prefix="/stats-prototypes", tags=["stats-prototypes"])
Backend = Literal["stats", "stats_sm"]


@router.get("/capabilities")
async def capabilities(user: CurrentUser, backend: Backend) -> dict:
    if backend == "stats_sm":
        from asaree.stats_sm.analysis import FAMILIES, UNAVAILABLE

        return dict(
            families=list(FAMILIES),
            unavailable=[dict(title=title, reason=reason, reference="/stats") for title, reason in UNAVAILABLE],
        )
    from asaree.stats.families import FAMILIES

    return dict(families=[*FAMILIES, "auto"], unavailable=[])


def summary(analysis: StatsAnalysis, *, detail: bool = False) -> dict:
    result = dict(
        id=str(analysis.id),
        backend=analysis.backend,
        status=analysis.status,
        error=analysis.error,
        created_at=analysis.created_at.isoformat(),
        config=analysis.request["config"],
        fingerprint=analysis.request["source"]["fingerprint"],
        design_revision=analysis.request["source"]["design_revision"],
    )
    if detail:
        result["output"] = analysis.output
        result["source"] = catalog(analysis.request["source"])
    return result


@router.get("/{experiment_id}/source")
async def source(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict:
    await _get_owned_experiment(db, experiment_id, user)
    try:
        return catalog(await read_source(db, experiment_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{experiment_id}/analyses")
async def history(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession, backend: Backend) -> list[dict]:
    await _get_owned_experiment(db, experiment_id, user)
    analyses = (
        await db.scalars(
            select(StatsAnalysis)
            .options(defer(StatsAnalysis.output))
            .where(StatsAnalysis.experiment_id == experiment_id, StatsAnalysis.backend == backend)
            .order_by(StatsAnalysis.created_at.desc())
            .limit(30)
        )
    ).all()
    return [summary(a) for a in analyses]


@router.get("/{experiment_id}/analyses/{analysis_id}")
async def detail(experiment_id: uuid.UUID, analysis_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict:
    await _get_owned_experiment(db, experiment_id, user)
    analysis = await db.get(StatsAnalysis, analysis_id)
    if analysis is None or analysis.experiment_id != experiment_id:
        raise HTTPException(404, "Analysis not found")
    return summary(analysis, detail=True)


@router.post("/{experiment_id}/analyses", status_code=202)
async def create(
    experiment_id: uuid.UUID, backend: Backend, body: StatsRequest, user: CurrentUser, db: DbSession
) -> dict:
    await _get_owned_experiment(db, experiment_id, user)
    if body.source_analysis_id:
        previous = await db.get(StatsAnalysis, body.source_analysis_id)
        if previous is None or previous.experiment_id != experiment_id:
            raise HTTPException(404, "Source analysis not found")
        snapshot = previous.request["source"]
    else:
        try:
            snapshot = await read_source(db, experiment_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    config = body.model_dump(mode="json", exclude={"source_analysis_id"})
    if backend == "stats_sm":
        from asaree.stats_sm.analysis import FAMILIES as MAPPED_FAMILIES

        families = MAPPED_FAMILIES
    else:
        from asaree.stats.families import FAMILIES as ORIGINAL_FAMILIES

        families = (*ORIGINAL_FAMILIES, "auto")
    if config["family"] not in families:
        raise HTTPException(422, "No mapping for the selected family on this backend.")
    try:
        prepare_frame(snapshot, config)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    analysis = StatsAnalysis(
        experiment_id=experiment_id,
        backend=backend,
        status="pending",
        request=dict(source=snapshot, config=config),
        output={},
    )
    db.add(analysis)
    await db.flush()
    await db.refresh(analysis)
    # Commit the immutable input before the worker can pick it up.
    await db.commit()
    try:
        pool = await _get_pool()
        await pool.enqueue_job("execute_stats_task", str(analysis.id), _job_id=f"stats:{analysis.id}", _expires=600)
    except Exception as exc:
        analysis.status, analysis.error = "failed", "Could not enqueue analysis; check the worker and Redis."
        await db.commit()
        raise HTTPException(503, analysis.error) from exc
    return summary(analysis)
