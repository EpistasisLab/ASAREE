"""Background CSV v2 exports streamed by PostgreSQL into owned, expiring files."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from sqlalchemy import and_, case, func, literal, select, update
from sqlalchemy.dialects import postgresql

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol_run import ProtocolRun
from asaree.models.result_job import ResultJob
from asaree.services.result_jobs import job_scope
from asaree.services.result_reads import slot_query
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


async def execute_result_export(_ctx: dict[str, Any], job_id: str) -> None:
    identifier = uuid.UUID(job_id)
    destination = export_path(identifier)
    temporary = destination.with_suffix(".part")
    try:
        async with get_session() as db:
            # One expensive export globally; transaction locks also release on
            # worker death. Durable running jobs are relayed again after ARQ loss.
            if not await db.scalar(select(func.pg_try_advisory_xact_lock(73912842))):
                return
            job = await db.get(ResultJob, identifier)
            if job is None or job.kind != "export" or job.status not in ("pending", "running"):
                return
            scope = await job_scope(db, job)
            cutoff = datetime.fromisoformat(job.params["cutoff"])
            source = slot_query(scope, cutoff=cutoff)
            selection = job.params["selection"]
            if selection.get("cell_id"):
                source = source.where(FactorialCell.id == uuid.UUID(selection["cell_id"]))
            if selection.get("replicate_ids") is not None:
                source = source.where(
                    FactorialReplicateResult.id.in_([uuid.UUID(i) for i in selection["replicate_ids"]])
                )
            if selection.get("row_indices") is not None:
                source = source.where(FactorialRowResult.row_index.in_(selection["row_indices"]))
            slots = source.subquery("export_slots")
            statement = (
                select(
                    slots,
                    ProtocolRun.attempt_result,
                    ProtocolRun.node_runs,
                    case(
                        (
                            and_(
                                slots.c.run_id.is_(None),
                                slots.c.status == "completed",
                                literal(scope.unit == "replicate"),
                            ),
                            func.jsonb_build_object(
                                "metric_values",
                                FactorialReplicateResult.metric_values,
                                "artifacts",
                                FactorialReplicateResult.artifacts,
                            ),
                        ),
                        else_=None,
                    ).label("legacy_result"),
                )
                .join(FactorialReplicateResult, FactorialReplicateResult.id == slots.c.replicate_id)
                .outerjoin(ProtocolRun, ProtocolRun.id == slots.c.run_id)
                .order_by(slots.c.slot_id)
            )
            dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
            sql = str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
            schema = {
                "format": "asaree.execution-results.csv.v2",
                "scope": scope.key,
                "attempts_created_before": cutoff.isoformat(),
                "columns": list(statement.selected_columns.keys()),
                "metrics": (scope.design_spec or {}).get("metrics", []),
                "json_columns": ["factor_values", "result", "usage", "attempt_result", "node_runs", "legacy_result"],
                "evidence": "Exact JSONB values and producer provenance are in attempt_result; JSON null remains null.",
            }
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

                connection = await db.connection()
                raw = await connection.get_raw_connection()
                driver = raw.driver_connection
                if driver is None:
                    raise RuntimeError("PostgreSQL driver connection unavailable")
                await driver.copy_from_query(sql, output=write, format="csv", header=True)
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
