"""Background analysis-CSV exports, streamed in chunks into owned, expiring files."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.experiment import ResearchExperiment
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.result_job import ResultJob
from asaree.services.csv_export import layout_stub, result_csv_layout, result_rows_schema
from asaree.services.experiment_run_results import summarize_experiment_run_results
from asaree.services.result_jobs import job_scope
from asaree.services.result_reads import ResultScope
from asaree.worker.enqueue import _get_pool

logger = logging.getLogger(__name__)
RETENTION = timedelta(hours=24)


def export_path(identifier: uuid.UUID, *, metadata: bool = False) -> Path:
    root = Path(get_settings().result_export_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / f"{identifier}.{'schema.json' if metadata else 'csv'}"


def download_token(job: ResultJob) -> str:
    return jwt.encode(
        {
            "type": "result_export",
            "job": str(job.id),
            "sub": str(job.owner_id),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        get_settings().auth_secret_key,
        algorithm="HS256",
    )


def validate_download_token(token: str, identifier: uuid.UUID) -> uuid.UUID:
    try:
        payload = jwt.decode(token, get_settings().auth_secret_key, algorithms=["HS256"])
        if payload.get("type") != "result_export" or payload.get("job") != str(identifier):
            raise ValueError("invalid_download_token")
        return uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, TypeError) as exc:
        raise ValueError("invalid_download_token") from exc


async def relay_exports(_ctx: dict[str, Any]) -> None:
    async with get_session() as db:
        identifiers = list(
            (
                await db.scalars(
                    select(ResultJob.id)
                    .where(ResultJob.kind == "export", ResultJob.status.in_(("pending", "running")))
                    .order_by(ResultJob.created_at)
                    .limit(4)
                )
            ).all()
        )
        expired = list(
            (
                await db.scalars(
                    select(ResultJob.id)
                    .where(
                        ResultJob.kind == "export",
                        ResultJob.status.in_(("completed", "failed", "cancelled")),
                        ResultJob.created_at < datetime.now(UTC) - RETENTION,
                    )
                    .limit(20)
                )
            ).all()
        )
        for identifier in expired:
            for path in (
                export_path(identifier),
                export_path(identifier, metadata=True),
                export_path(identifier).with_suffix(".part"),
            ):
                await asyncio.to_thread(path.unlink, missing_ok=True)
        if expired:
            await db.execute(update(ResultJob).where(ResultJob.id.in_(expired)).values(status="expired"))
    pool = await _get_pool()
    for identifier in identifiers:
        await pool.enqueue_job(
            "execute_result_export",
            str(identifier),
            _job_id=f"result-export:{identifier}",
            _queue_name="arq:results",
        )


EXPORT_CHUNK_ROWS = 1000


async def _write_analysis_csv(
    db: AsyncSession, job: ResultJob, scope: ResultScope, write: Callable[[bytes], Awaitable[None]]
) -> dict[str, Any]:
    """Stream the Results tab's analysis-ready CSV, a chunk of replicates at a time.

    Rows come from the same projection the synchronous CSV uses, restricted to a
    chunk, so both downloads have identical columns and values: one column per
    factor, duration/tokens/cost, one column per metric, statuses. Pass one keeps
    only what the column layout needs (:func:`layout_stub`); pass two rebuilds
    each chunk and writes it in the layout's row order. Memory stays bounded by
    the chunk, however large the experiment.
    """
    experiment = await db.get(ResearchExperiment, job.experiment_id)
    selection = job.params["selection"]
    replicates = select(
        FactorialReplicateResult.id, FactorialCell.cell_label, FactorialReplicateResult.replicate_number
    ).join(FactorialCell, FactorialCell.id == FactorialReplicateResult.cell_id).where(
        FactorialCell.experiment_id == job.experiment_id,
        FactorialCell.design_revision_id == scope.design_revision_id,
    )
    if selection.get("cell_id"):
        replicates = replicates.where(FactorialCell.id == uuid.UUID(selection["cell_id"]))
    if selection.get("replicate_ids") is not None:
        replicates = replicates.where(
            FactorialReplicateResult.id.in_([uuid.UUID(i) for i in selection["replicate_ids"]])
        )
    ordered = (
        await db.execute(replicates.order_by(FactorialCell.cell_label, FactorialReplicateResult.replicate_number))
    ).all()
    # (cell label, replicate number) is unique within a design revision;
    # a replicate label alone is only unique within its cell.
    id_by_key = {(cell_label, number): identifier for identifier, cell_label, number in ordered}

    def key(row: dict[str, Any]) -> tuple[str, int]:
        return row["cell_label"], row["replicate_number"]

    row_indices = set(selection["row_indices"]) if selection.get("row_indices") is not None else None

    async def project(chunk: list[uuid.UUID]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        results = await summarize_experiment_run_results(
            db,
            experiment_id=job.experiment_id,
            design_spec=experiment.design_spec if experiment else None,
            protocol_id=scope.protocol_id,
            design_revision_id=scope.design_revision_id,
            protocol_revision_id=scope.protocol_revision_id,
            bounded=False,
            replicate_ids=set(chunk),
        )
        # The rows are plain dicts; drop the chunk's ORM objects (runs carry
        # their full evidence) so the session doesn't accumulate the experiment.
        db.expunge_all()
        mode = results["consumption_mode"]
        rows = results["row_results"] if mode == "per_row" else results["replicates"]
        if mode == "per_row" and row_indices is not None:
            rows = [row for row in rows if (row.get("dataset_row") or {}).get("row_index") in row_indices]
        return mode, rows, results

    # A per-row replicate expands into one row per source row, so size its
    # chunks by the rows they produce rather than by replicates.
    ids = [identifier for identifier, _, _ in ordered]
    size = EXPORT_CHUNK_ROWS
    mode, stubs = "whole_dataset", []
    design_spec: dict[str, Any] | None = experiment.design_spec if experiment else None
    metric_types: dict[str, Any] = {}
    metric_aggregations: dict[str, Any] = {}
    position = 0
    while position < len(ids):
        chunk = ids[position : position + size]
        mode, rows, results = await project(chunk)
        design_spec = results.get("selected_design_spec", design_spec)
        metric_types.update(results.get("metric_types") or {})
        metric_aggregations.update(results.get("metric_aggregations") or {})
        for row in rows:
            stubs.append({**layout_stub(row, consumption_mode=mode), "cell_label": row.get("cell_label")})
        if mode == "per_row" and position == 0 and rows:
            size = max(1, EXPORT_CHUNK_ROWS * len(chunk) // len(rows))
        position += len(chunk)
    layout = result_csv_layout(stubs, design_spec, consumption_mode=mode)
    if layout.sort_key is not None:
        # The analysis order (factor levels, then replicate number) is a property
        # of whole replicates, so it fixes the order chunks are rebuilt in.
        ids = [id_by_key[key(stub)] for stub in sorted(stubs, key=layout.sort_key)]
    header = io.StringIO()
    csv.DictWriter(header, fieldnames=layout.fields, extrasaction="ignore").writeheader()
    await write(header.getvalue().encode())
    for start in range(0, len(ids), size):
        chunk = ids[start : start + size]
        _, rows, _ = await project(chunk)
        if layout.sort_key is not None:
            rank = {label_id: index for index, label_id in enumerate(chunk)}
            rows.sort(key=lambda row: rank[id_by_key[key(row)]])
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=layout.fields, extrasaction="ignore")
        for row in rows:
            writer.writerow(layout.write_row(row))
        await write(buffer.getvalue().encode())
    return result_rows_schema(stubs, metric_types, metric_aggregations, design_spec, consumption_mode=mode)


async def execute_result_export(_ctx: dict[str, Any], job_id: str) -> None:
    identifier = uuid.UUID(job_id)
    destination = export_path(identifier)
    temporary = destination.with_suffix(".part")
    try:
        async with get_session() as db:
            # Every chunk below reads one snapshot, so the file is consistent
            # even while runs keep finishing during a long export.
            await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            # One expensive export globally; transaction locks also release on
            # worker death. Durable running jobs are relayed again after ARQ loss.
            if not await db.scalar(select(func.pg_try_advisory_xact_lock(73912842))):
                return
            job = await db.get(ResultJob, identifier)
            if job is None or job.kind != "export" or job.status not in ("pending", "running"):
                return
            scope = await job_scope(db, job)
            async with get_session() as status_db:
                await status_db.execute(
                    update(ResultJob)
                    .where(ResultJob.id == identifier)
                    .values(status="running", progress={"bytes": 0}, error=None)
                )
            written = 0
            checkpoint = time.monotonic()
            with temporary.open("wb") as output:
                temporary.chmod(0o600)

                async def write(chunk: bytes) -> None:
                    nonlocal written, checkpoint
                    await asyncio.to_thread(output.write, chunk)
                    written += len(chunk)
                    if time.monotonic() - checkpoint >= 5:
                        async with get_session() as status_db:
                            status = await status_db.scalar(select(ResultJob.status).where(ResultJob.id == identifier))
                            if status == "cancelled":
                                raise ValueError("Export cancelled.")
                            await status_db.execute(
                                update(ResultJob).where(ResultJob.id == identifier).values(progress={"bytes": written})
                            )
                        checkpoint = time.monotonic()

                schema = await _write_analysis_csv(db, job, scope, write)
            await asyncio.to_thread(temporary.replace, destination)
            await asyncio.to_thread(export_path(identifier, metadata=True).write_text, json.dumps(schema, indent=2))
            async with get_session() as status_db:
                await status_db.execute(
                    update(ResultJob)
                    .where(ResultJob.id == identifier, ResultJob.status != "cancelled")
                    .values(status="completed", progress={"bytes": written}, error=None)
                )
    except Exception:
        logger.exception("result_export_failed", extra={"job_id": job_id})
        async with get_session() as db:
            await db.execute(
                update(ResultJob)
                .where(ResultJob.id == identifier, ResultJob.status != "cancelled")
                .values(status="failed", error="Export failed. Request a new export to retry.")
            )
        await asyncio.to_thread(temporary.unlink, missing_ok=True)
