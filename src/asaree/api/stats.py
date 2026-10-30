"""Owner-scoped automatic statistics reports for an experiment version."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import defer

from asaree.api.experiments import _get_owned_experiment
from asaree.deps import CurrentUser, DbSession
from asaree.models.experiment import ResearchExperiment
from asaree.models.stats_analysis import StatsAnalysis
from asaree.services.stats_distribution import report_distribution
from asaree.services.stats_findings import describe_report_findings
from asaree.services.stats_source import StatsRequest, catalog, read_source
from asaree.worker.enqueue import _get_pool

router = APIRouter(prefix="/stats", tags=["stats"])
AUTOMATIC = StatsAnalysis.request["config"]["family"].as_string() == "auto"


async def replace_automatic_reports(
    db, experiment_id: uuid.UUID, version_id: uuid.UUID, *, metric: str | None = None,
) -> None:
    """Replace reports atomically, serializing concurrent requests for this experiment."""
    await db.execute(
        select(ResearchExperiment.id).where(ResearchExperiment.id == experiment_id).with_for_update()
    )
    scope = [
        StatsAnalysis.experiment_id == experiment_id,
        AUTOMATIC,
        StatsAnalysis.request["source"]["protocol_revision_id"].as_string() == str(version_id),
    ]
    if metric is not None:
        scope.append(StatsAnalysis.request["config"]["metric"].as_string() == metric)
    active = await db.scalar(
        select(StatsAnalysis.id).where(*scope, StatsAnalysis.status.in_(["pending", "running"])).limit(1)
    )
    if active:
        raise HTTPException(409, "Statistics are already being calculated for this experiment version.")
    await db.execute(delete(StatsAnalysis).where(*scope).execution_options(synchronize_session=False))


def summary(analysis: StatsAnalysis, *, detail: bool = False, report_status: str | None = None) -> dict:
    result = dict(
        id=str(analysis.id),
        status=analysis.status,
        error=analysis.error,
        created_at=analysis.created_at.isoformat(),
        config=analysis.request["config"],
        fingerprint=analysis.request["source"]["fingerprint"],
        design_revision=analysis.request["source"]["design_revision"],
        report_status=report_status,
    )
    if detail:
        result["output"] = dict(analysis.output)
        if report := analysis.output.get("summary"):
            result["output"]["summary"] = dict(report, findings=describe_report_findings(report))
            if distribution := report_distribution(
                analysis.output, analysis.request["source"], analysis.request["config"],
            ):
                result["output"]["summary"]["distribution"] = distribution
        result["report_status"] = (analysis.output.get("summary") or {}).get("status")
        result["source"] = catalog(analysis.request["source"])
    return result


@router.get("/{experiment_id}/source")
async def source(
    experiment_id: uuid.UUID, user: CurrentUser, db: DbSession, protocol_revision_id: uuid.UUID | None = None,
) -> dict:
    await _get_owned_experiment(db, experiment_id, user)
    try:
        return catalog(await read_source(db, experiment_id, protocol_revision_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{experiment_id}/analyses")
async def history(
    experiment_id: uuid.UUID, user: CurrentUser, db: DbSession, protocol_revision_id: uuid.UUID | None = None,
) -> list[dict]:
    await _get_owned_experiment(db, experiment_id, user)
    statement = (
        select(StatsAnalysis, StatsAnalysis.output["summary"]["status"].as_string())
        .options(defer(StatsAnalysis.output))
        .where(StatsAnalysis.experiment_id == experiment_id, AUTOMATIC)
    )
    if protocol_revision_id is not None:
        statement = statement.where(
            StatsAnalysis.request["source"]["protocol_revision_id"].as_string() == str(protocol_revision_id)
        )
    analyses = (
        await db.execute(
            statement
            .order_by(StatsAnalysis.created_at.desc())
            .limit(100)
        )
    ).all()
    return [summary(a, report_status=report_status) for a, report_status in analyses]


@router.get("/{experiment_id}/analyses/{analysis_id}")
async def detail(experiment_id: uuid.UUID, analysis_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict:
    from asaree.services.stats_html import report_filename

    experiment = await _get_owned_experiment(db, experiment_id, user)
    analysis = await db.get(StatsAnalysis, analysis_id)
    if analysis is None or analysis.experiment_id != experiment_id:
        raise HTTPException(404, "Analysis not found")
    saved = summary(analysis, detail=True)
    saved["report_filename"] = report_filename(
        saved, experiment_name=experiment.name,
        protocol_revision=analysis.request["source"].get("protocol_revision"),
    )
    return saved


@router.get("/{experiment_id}/analyses/{analysis_id}/report", response_class=HTMLResponse)
async def report(experiment_id: uuid.UUID, analysis_id: uuid.UUID, user: CurrentUser, db: DbSession):
    from asaree.services.stats_html import render_html

    saved = await detail(experiment_id, analysis_id, user, db)
    if saved["status"] not in {"completed", "incomplete"} or not saved["output"].get("summary"):
        raise HTTPException(409, "The analysis has not completed.")
    try:
        html = render_html(saved)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return HTMLResponse(
        html, headers={"Content-Disposition": f'attachment; filename="{saved["report_filename"]}"'}
    )


@router.post("/{experiment_id}/automatic-analyses", status_code=202)
async def automatic(
    experiment_id: uuid.UUID, user: CurrentUser, db: DbSession, protocol_revision_id: uuid.UUID | None = None,
) -> list[dict]:
    """One action analyzes declared metrics on one immutable input snapshot."""
    await _get_owned_experiment(db, experiment_id, user)
    if protocol_revision_id is None:
        raise HTTPException(422, "Select a published experiment version before analyzing metrics.")
    try:
        snapshot = await read_source(db, experiment_id, protocol_revision_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    source_catalog = catalog(snapshot)
    if not source_catalog["n_measured_replicates"]:
        raise HTTPException(422, "This experiment version has no completed replicates with recorded measurements.")
    metrics = source_catalog["metrics"]
    if not metrics or len(metrics) > 64:
        raise HTTPException(422, "An automatic batch requires between one and 64 metrics.")
    await replace_automatic_reports(db, experiment_id, protocol_revision_id)
    analyses = []
    for metric in metrics:
        config = StatsRequest(metric=metric["name"], n_boot=1000, n_perm=1999).model_dump(mode="json")
        analysis = StatsAnalysis(
            experiment_id=experiment_id,
            status="pending",
            request=dict(source=snapshot, config=config),
            output={},
        )
        db.add(analysis)
        analyses.append(analysis)
    await db.flush()
    for analysis in analyses:
        await db.refresh(analysis)
    await db.commit()
    try:
        pool = await _get_pool()
        for analysis in analyses:
            await pool.enqueue_job(
                "execute_stats_task", str(analysis.id), _job_id=f"stats:{analysis.id}", _expires=3600
            )
    except Exception as exc:
        for analysis in analyses:
            if analysis.status == "pending":
                analysis.status, analysis.error = "failed", "Could not enqueue batch; check the worker and Redis."
        await db.commit()
        raise HTTPException(
            503, "Could not enqueue the complete analysis batch; retry after checking the worker."
        ) from exc
    return [summary(analysis) for analysis in analyses]
