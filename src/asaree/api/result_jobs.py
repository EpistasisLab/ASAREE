"""Owned, version-scoped asynchronous commands and native export downloads."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from asaree.api.result_reads import Scope
from asaree.deps import CurrentUser, DbSession
from asaree.models.result_job import ResultJob
from asaree.services import result_exports, result_jobs

router = APIRouter(prefix="/experiments/{experiment_id}/execution-results/jobs", tags=["execution-results"])


class Command(BaseModel):
    kind: str = Field(pattern="^(plan|cancel|export)$")
    request_key: str = Field(min_length=1, max_length=100)
    cell_id: uuid.UUID | None = None
    replicate_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    row_indices: list[int] | None = Field(default=None, max_length=100)
    rerun_slot_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    rerun_completed: bool = False


def payload(job: ResultJob) -> dict[str, Any]:
    result = {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "progress": {
            key: value
            for key, value in job.progress.items()
            if key
            in {"scanned", "planned", "created", "skipped", "cancelled", "bytes", "cell_count", "replicate_count"}
        },
        "error": job.error,
        "protocol_revision_id": str(job.protocol_revision_id) if job.protocol_revision_id else None,
    }
    if job.kind == "export" and job.status == "completed":
        token = result_exports.download_token(job)
        base = f"/api/experiments/{job.experiment_id}/execution-results/jobs/{job.id}/download"
        result["download_url"] = f"{base}?token={token}"
        result["schema_url"] = f"{base}?token={token}&metadata=true"
    return result


async def owned_job(db: DbSession, experiment_id: uuid.UUID, identifier: uuid.UUID, owner_id: uuid.UUID) -> ResultJob:
    await db.execute(text("SET LOCAL statement_timeout = '5s'"))
    job = await db.scalar(
        select(ResultJob).where(
            ResultJob.id == identifier, ResultJob.experiment_id == experiment_id, ResultJob.owner_id == owner_id
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return job


@router.post("", status_code=202)
async def create(command: Command, user: CurrentUser, db: DbSession, scope: Scope) -> dict[str, Any]:
    if command.row_indices is not None and (
        scope.unit != "row"
        or command.replicate_ids is None
        or len(command.replicate_ids) != 1
        or any(index < 0 for index in command.row_indices)
    ):
        raise HTTPException(status_code=422, detail="Row selection requires one replicate and nonnegative indices.")
    try:
        job = await result_jobs.create_job(
            db,
            scope,
            user.id,
            kind=command.kind,
            request_key=command.request_key,
            params=command.model_dump(mode="json", exclude={"kind", "request_key"}, exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return payload(job)


@router.get("/{job_id}")
async def get(experiment_id: uuid.UUID, job_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return payload(await owned_job(db, experiment_id, job_id, user.id))


@router.post("/{job_id}/confirm", status_code=202)
async def confirm(experiment_id: uuid.UUID, job_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    await db.execute(text("SET LOCAL statement_timeout = '5s'"))
    await db.execute(
        select(ResultJob.id)
        .where(ResultJob.id == job_id, ResultJob.owner_id == user.id, ResultJob.experiment_id == experiment_id)
        .with_for_update()
    )
    job = await owned_job(db, experiment_id, job_id, user.id)
    if job.kind != "plan" or job.status not in ("awaiting_confirmation", "queued", "completed"):
        raise HTTPException(status_code=409, detail="Batch preview is not ready for confirmation.")
    if job.status == "awaiting_confirmation":
        job.status = "queued"
    return payload(job)


@router.post("/{job_id}/cancel")
async def cancel(
    experiment_id: uuid.UUID, job_id: uuid.UUID, user: CurrentUser, db: DbSession, preview_only: bool = False
) -> dict[str, Any]:
    await db.execute(text("SET LOCAL statement_timeout = '5s'"))
    await db.execute(
        select(ResultJob.id)
        .where(ResultJob.id == job_id, ResultJob.owner_id == user.id, ResultJob.experiment_id == experiment_id)
        .with_for_update()
    )
    job = await owned_job(db, experiment_id, job_id, user.id)
    if preview_only and job.status not in ("pending", "awaiting_confirmation"):
        return payload(job)
    if job.status not in ("completed", "expired"):
        job.status = "cancelled"
    return payload(job)


@router.get("/{job_id}/download", response_class=FileResponse)
async def download(
    experiment_id: uuid.UUID, job_id: uuid.UUID, token: str, db: DbSession, metadata: bool = False
) -> FileResponse:
    try:
        owner = result_exports.validate_download_token(token, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Download link expired or invalid.") from exc
    job = await owned_job(db, experiment_id, job_id, owner)
    path = result_exports.export_path(job_id, metadata=metadata)
    if (
        job.kind != "export"
        or job.status != "completed"
        or datetime.now(UTC) - job.created_at > result_exports.RETENTION
        or not path.is_file()
    ):
        raise HTTPException(status_code=410, detail="Export expired or unavailable; request a new export.")
    return FileResponse(
        path,
        media_type="application/json" if metadata else "text/csv",
        filename=f"results-{job_id}.{'schema.json' if metadata else 'csv'}",
        headers={"Cache-Control": "private, no-store"},
    )
