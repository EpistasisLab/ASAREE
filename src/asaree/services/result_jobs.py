"""Durable, resumable result commands. HTTP only records intent and reads progress.

Planning seals exact slot/attempt identities before confirmation. Each small
creation transaction also advances its member checkpoint; Redis is a retryable
delivery mechanism, never the source of truth for whether a run was created.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from math import prod
from pathlib import Path
from typing import Any

from sqlalchemy import Text, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.dataset import RegisteredDataset
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_run import ProtocolRun
from asaree.models.result_job import ResultJob, ResultJobMember
from asaree.services.dataset_row_csv import RowSource
from asaree.services.indexed_row_source import IndexedRows, read_indexed_row_source
from asaree.services.protocol_execution import plan_cell_runs
from asaree.services.protocol_runs import (
    RERUN_ON_REQUEST_STATUSES,
    RETRYABLE_PROTOCOL_RUN_STATUSES,
    request_protocol_run_cancellation,
)
from asaree.services.result_reads import (
    ResultScope,
    cell_predicates,
    parent_predicates,
    resolve_result_scope,
    slot_query,
)
from asaree.worker.enqueue import enqueue_protocol_run

logger = logging.getLogger(__name__)
ACTIVE = ("pending", "running", "finalizing")
RETRYABLE = ("not_started", *sorted(RETRYABLE_PROTOCOL_RUN_STATUSES))
WINDOW = 1000


async def create_job(
    db: AsyncSession,
    scope: ResultScope,
    owner_id: uuid.UUID,
    *,
    kind: str,
    request_key: str,
    params: dict[str, Any],
) -> ResultJob:
    previous = await db.scalar(
        select(ResultJob).where(ResultJob.owner_id == owner_id, ResultJob.request_key == request_key)
    )
    if previous is not None:
        if (
            previous.kind != kind
            or previous.params.get("selection") != params
            or previous.experiment_id != scope.experiment_id
            or previous.protocol_revision_id != scope.protocol_revision_id
            or previous.protocol_id != scope.protocol_id
            or previous.design_revision_id != scope.design_revision_id
        ):
            raise ValueError("idempotency_key_conflict")
        return previous
    if kind == "plan" and scope.protocol_revision_id is None:
        raise ValueError("Publish the protocol before preparing a production batch.")
    job = ResultJob(
        owner_id=owner_id,
        experiment_id=scope.experiment_id,
        protocol_id=scope.protocol_id,
        protocol_revision_id=scope.protocol_revision_id,
        design_revision_id=scope.design_revision_id,
        kind=kind,
        request_key=request_key,
        status="pending",
        params={"selection": params, "cutoff": datetime.now(UTC).isoformat()},
        progress={},
    )
    inserted = await db.scalar(
        insert(ResultJob)
        .values(
            id=uuid.uuid4(),
            owner_id=job.owner_id,
            experiment_id=job.experiment_id,
            protocol_id=job.protocol_id,
            protocol_revision_id=job.protocol_revision_id,
            design_revision_id=job.design_revision_id,
            kind=job.kind,
            status=job.status,
            request_key=job.request_key,
            params=job.params,
            progress={},
        )
        .on_conflict_do_nothing(index_elements=[ResultJob.owner_id, ResultJob.request_key])
        .returning(ResultJob.id)
    )
    if inserted is None:
        return await create_job(db, scope, owner_id, kind=kind, request_key=request_key, params=params)
    stored = await db.get(ResultJob, inserted)
    assert stored is not None
    job = stored
    if kind == "cancel":
        # Serialize against planning transactions so Stop cannot be refilled by
        # an already-confirmed batch after this request commits.
        await db.execute(select(Protocol.id).where(Protocol.id == scope.protocol_id).with_for_update())
        job.params = {**job.params, "cutoff": datetime.now(UTC).isoformat()}
        planners = update(ResultJob).where(
            ResultJob.protocol_id == scope.protocol_id,
            ResultJob.protocol_revision_id == scope.protocol_revision_id,
            ResultJob.design_revision_id == scope.design_revision_id,
            ResultJob.kind == "plan",
            ResultJob.status.in_(("pending", "awaiting_confirmation", "queued")),
        )
        if params.get("cell_id"):
            exclusions = func.coalesce(ResultJob.params["excluded_cell_ids"], cast(literal("[]"), JSONB))
            await db.execute(
                planners.values(
                    params=func.jsonb_set(
                        ResultJob.params,
                        cast(literal("{excluded_cell_ids}"), ARRAY(Text)),
                        exclusions.op("||")(func.jsonb_build_array(params["cell_id"])),
                    )
                )
            )
        else:
            await db.execute(planners.values(status="cancelled"))
    return job


async def job_scope(db: AsyncSession, job: ResultJob) -> ResultScope:
    return await resolve_result_scope(
        db,
        experiment_id=job.experiment_id,
        protocol_id=job.protocol_id,
        protocol_revision_id=job.protocol_revision_id,
        design_revision_id=job.design_revision_id,
        use_current_publication=False,
    )


async def indexed_source(db: AsyncSession, scope: ResultScope, owner_id: uuid.UUID) -> RowSource | None:
    if scope.row_plan is None:
        return None
    dataset = await db.scalar(
        select(RegisteredDataset).where(
            RegisteredDataset.id == uuid.UUID(scope.row_plan["driver_dataset_id"]),
            RegisteredDataset.owner_id == owner_id,
        )
    )
    if dataset is None:
        raise ValueError("Registered row source is unavailable.")
    return await asyncio.to_thread(
        read_indexed_row_source,
        dataset_id=str(dataset.id),
        raw_path=dataset.raw_path,
        raw_sha256=dataset.raw_sha256,
        cache_root=Path(get_settings().result_row_index_dir),
    )


def restored_source(progress: dict[str, Any]) -> RowSource | None:
    source = progress.get("source")
    if not source:
        return None
    return RowSource(
        source["dataset_id"],
        source["sha256"],
        tuple(source["columns"]),
        IndexedRows(Path(get_settings().result_row_index_dir) / f"{source['sha256']}.sqlite", source["count"]),
    )


def selected_parents(scope: ResultScope, selection: dict[str, Any]) -> Any:
    query = (
        select(FactorialReplicateResult.id)
        .join(FactorialCell)
        .where(
            FactorialCell.experiment_id == scope.experiment_id,
            FactorialCell.design_revision_id == scope.design_revision_id,
            *cell_predicates(scope),
            *parent_predicates(scope),
        )
    )
    if selection.get("cell_id"):
        query = query.where(FactorialCell.id == uuid.UUID(selection["cell_id"]))
    if selection.get("replicate_ids") is not None:
        query = query.where(FactorialReplicateResult.id.in_([uuid.UUID(i) for i in selection["replicate_ids"]]))
    if selection.get("excluded_cell_ids"):
        query = query.where(FactorialCell.id.not_in([uuid.UUID(i) for i in selection["excluded_cell_ids"]]))
    return query.order_by(FactorialReplicateResult.id)


def eligible(status: str, slot_id: str, rerun_slots: set[str], rerun_completed: bool = False) -> bool:
    return status in RETRYABLE or status in RERUN_ON_REQUEST_STATUSES and (rerun_completed or slot_id in rerun_slots)


async def prepare_step(db: AsyncSession, job: ResultJob, scope: ResultScope) -> None:
    progress = dict(job.progress)
    selection = {**job.params["selection"], "excluded_cell_ids": job.params.get("excluded_cell_ids", [])}
    if not progress.get("validated"):
        source = restored_source(progress) or await indexed_source(db, scope, job.owner_id)
        await plan_cell_runs(
            db,
            protocol_id=scope.protocol_id,
            experiment_id=scope.experiment_id,
            owner_id=job.owner_id,
            graph=scope.graph,
            protocol_revision_id=scope.protocol_revision_id,
            replicate_ids=set(),
            indexed_source=source,
            validate_design_impact=False,
        )
        factors = (scope.design_spec or {}).get("factors")
        if scope.versioned and isinstance(factors, list) and factors:
            expected = prod(len(factor["levels"]) for factor in factors) * (scope.design_spec or {}).get(
                "replicates", 1
            )
            actual = await db.scalar(
                selected_parents(scope, {}).with_only_columns(func.count(), maintain_column_froms=True).order_by(None)
            )
            if actual != expected:
                raise ValueError("Published design cells are incomplete. Review and regenerate before running.")
        if source:
            progress["source"] = {
                "dataset_id": source.dataset_id,
                "sha256": source.raw_sha256,
                "columns": list(source.columns),
                "count": len(source.rows),
            }
        progress["validated"] = True
        selected = selected_parents(scope, selection)
        progress["replicate_count"] = await db.scalar(
            selected.with_only_columns(func.count(), maintain_column_froms=True).order_by(None)
        )
        progress["cell_count"] = await db.scalar(
            selected.with_only_columns(
                func.count(func.distinct(FactorialReplicateResult.cell_id)), maintain_column_froms=True
            ).order_by(None)
        )
    source = restored_source(progress)
    parents = selected_parents(scope, selection)
    if progress.get("parent"):
        parents = parents.where(FactorialReplicateResult.id >= uuid.UUID(progress["parent"]))
    identifiers = list((await db.scalars(parents.limit(1 if source else 100))).all())
    if not identifiers:
        progress["planned"] = await db.scalar(
            select(func.count()).select_from(ResultJobMember).where(ResultJobMember.job_id == job.id)
        )
        job.status = "awaiting_confirmation"
        job.progress = progress
        return
    query = slot_query(scope, cutoff=datetime.fromisoformat(job.params["cutoff"])).where(
        FactorialReplicateResult.id.in_(identifiers)
    )
    if source:
        start = int(progress.get("row", 0))
        indices = selection.get("row_indices")
        indexes = (
            indices[start : start + WINDOW]
            if indices is not None
            else range(start, min(start + WINDOW, len(source.rows)))
        )
        if any(index >= len(source.rows) for index in indexes):
            raise ValueError("Row index exceeds the verified CSV source.")
        query = query.where(FactorialRowResult.row_index.in_(indexes))
    rows = [dict(row) for row in (await db.execute(query)).mappings()]
    if source:
        by_index = {row["row_index"]: row for row in rows}
        rows = [
            by_index.get(
                index,
                {
                    "replicate_id": identifiers[0],
                    "row_index": index,
                    "slot_id": None,
                    "run_id": None,
                    "status": "not_started",
                },
            )
            for index in indexes
        ]
    reruns = set(selection.get("rerun_slot_ids", []))
    members = [
        {
            "job_id": job.id,
            "replicate_id": row["replicate_id"],
            "row_index": row["row_index"] if source else -1,
            "slot_id": row["slot_id"],
            "expected_run_id": row["run_id"],
            "processed": False,
            "delivered": False,
        }
        for row in rows
        if eligible(row["status"], str(row["slot_id"]), reruns, selection.get("rerun_completed", False))
    ]
    if members:
        await db.execute(insert(ResultJobMember).values(members).on_conflict_do_nothing())
    progress["scanned"] = progress.get("scanned", 0) + len(rows)
    if source and start + WINDOW < (len(indices) if indices is not None else len(source.rows)):
        progress.update(parent=str(identifiers[0]), row=start + WINDOW)
    else:
        # UUID successor gives a stable continuation without OFFSET.
        progress.update(parent=str(uuid.UUID(int=identifiers[-1].int + 1)), row=0)
    job.progress = progress


async def plan_step(db: AsyncSession, job: ResultJob, scope: ResultScope) -> None:
    protocol = await db.scalar(select(Protocol).where(Protocol.id == scope.protocol_id).with_for_update())
    if protocol is None or protocol.published_revision_id != scope.protocol_revision_id:
        raise ValueError("Publication changed. Prepare and confirm a new batch for the current version.")
    members = list(
        (
            await db.scalars(
                select(ResultJobMember)
                .where(ResultJobMember.job_id == job.id, ResultJobMember.processed.is_(False))
                .order_by(ResultJobMember.replicate_id, ResultJobMember.row_index)
                .limit(20)
            )
        ).all()
    )
    if not members:
        job.status = "completed"
        return
    progress = dict(job.progress)
    source = restored_source(progress)
    for member in members:
        parent_record = (
            await db.execute(
                select(FactorialReplicateResult.replicate_label, FactorialCell.id)
                .join(FactorialCell)
                .where(
                    FactorialReplicateResult.id == member.replicate_id,
                    FactorialCell.experiment_id == scope.experiment_id,
                    FactorialCell.design_revision_id == scope.design_revision_id,
                    *cell_predicates(scope),
                    *parent_predicates(scope),
                )
            )
        ).one_or_none()
        if parent_record is None or str(parent_record[1]) in job.params.get("excluded_cell_ids", []):
            member.processed = True
            progress["skipped"] = progress.get("skipped", 0) + 1
            continue
        query = slot_query(scope).where(FactorialReplicateResult.id == member.replicate_id)
        if source:
            query = query.where(FactorialRowResult.row_index == member.row_index)
        latest = (await db.execute(query)).mappings().one_or_none()
        member.processed = True
        # A slot changed after preview: never silently re-bill its new attempt.
        if latest and (latest["run_id"] != member.expected_run_id or latest["status"] in ACTIVE):
            progress["skipped"] = progress.get("skipped", 0) + 1
            continue
        parent = parent_record[0]
        finished = bool(latest and latest["status"] in RERUN_ON_REQUEST_STATUSES)
        retry = source and latest and latest["status"] in RETRYABLE_PROTOCOL_RUN_STATUSES
        kwargs = (
            {"retry_row_result_ids": [member.slot_id]}
            if retry
            else {
                "replicate_labels": {parent},
                "rerun_replicate_labels": {parent} if finished else set(),
                "row_indices": [member.row_index] if source else None,
            }
        )
        created, skipped = await plan_cell_runs(
            db,
            protocol_id=scope.protocol_id,
            experiment_id=scope.experiment_id,
            owner_id=job.owner_id,
            graph=scope.graph,
            protocol_revision_id=scope.protocol_revision_id,
            replicate_ids={member.replicate_id},
            prevalidated=True,
            indexed_source=source,
            **kwargs,
        )
        member.run_id = created[0].id if created else None
        progress["created"] = progress.get("created", 0) + len(created)
        progress["skipped"] = progress.get("skipped", 0) + skipped
    job.progress = progress


async def cancel_step(db: AsyncSession, job: ResultJob, scope: ResultScope) -> None:
    query = (
        select(ProtocolRun.id)
        .where(
            ProtocolRun.protocol_id == scope.protocol_id,
            ProtocolRun.protocol_revision_id == scope.protocol_revision_id,
            ProtocolRun.design_revision_id == scope.design_revision_id,
            ProtocolRun.status.in_(ACTIVE),
            ProtocolRun.is_test_run.is_(False),
            ProtocolRun.target_node_id.is_(None),
            ProtocolRun.created_at <= datetime.fromisoformat(job.params["cutoff"]),
            ProtocolRun.cancel_requested_at.is_(None),
        )
        .order_by(ProtocolRun.id)
        .limit(20)
    )
    if job.params["selection"].get("cell_id"):
        query = query.where(ProtocolRun.replicate_result_id.in_(selected_parents(scope, job.params["selection"])))
    identifiers = list((await db.scalars(query)).all())
    for identifier in identifiers:
        await request_protocol_run_cancellation(db, identifier)
    job.progress = {"cancelled": job.progress.get("cancelled", 0) + len(identifiers)}
    if not identifiers:
        job.status = "completed"


async def drain_result_commands(_ctx: dict[str, Any]) -> None:
    async with get_session() as db:
        await db.execute(
            update(ResultJob)
            .where(
                ResultJob.kind == "plan",
                ResultJob.status.in_(("pending", "awaiting_confirmation")),
                ResultJob.updated_at < datetime.now(UTC) - timedelta(hours=24),
            )
            .values(status="expired")
        )
        ids = list(
            (
                await db.scalars(
                    select(ResultJob.id)
                    .where(ResultJob.status.in_(("pending", "queued")), ResultJob.kind.in_(("plan", "cancel")))
                    .order_by(ResultJob.updated_at)
                    .limit(4)
                )
            ).all()
        )
    for identifier in ids:
        try:
            async with get_session() as db:
                if not await db.scalar(
                    select(func.pg_try_advisory_xact_lock(func.hashtextextended(str(identifier), 0)))
                ):
                    continue
                job = await db.get(ResultJob, identifier)
                if not job or job.status not in ("pending", "queued"):
                    continue
                scope = await job_scope(db, job)
                if job.kind == "plan" and job.status == "pending":
                    # File verification can be slow. It must not hold the job
                    # or protocol row lock that HTTP progress/cancel needs.
                    if scope.row_plan is not None and not job.progress.get("source"):
                        source = await indexed_source(db, scope, job.owner_id)
                        assert source is not None
                        progress = {
                            **job.progress,
                            "source": {
                                "dataset_id": source.dataset_id,
                                "sha256": source.raw_sha256,
                                "columns": list(source.columns),
                                "count": len(source.rows),
                            },
                        }
                        await db.execute(
                            update(ResultJob)
                            .where(ResultJob.id == identifier, ResultJob.status == "pending")
                            .values(progress=progress, updated_at=datetime.now(UTC))
                        )
                    else:
                        await prepare_step(db, job, scope)
                        status, progress = job.status, job.progress
                        db.expunge(job)
                        await db.execute(
                            update(ResultJob)
                            .where(ResultJob.id == identifier, ResultJob.status == "pending")
                            .values(status=status, progress=progress, updated_at=datetime.now(UTC))
                        )
                    continue
                await db.execute(select(Protocol.id).where(Protocol.id == job.protocol_id).with_for_update())
                job = await db.get(ResultJob, identifier, with_for_update=True, populate_existing=True)
                if not job or job.status not in ("pending", "queued"):
                    continue
                scope = await job_scope(db, job)
                if job.kind == "cancel":
                    await cancel_step(db, job, scope)
                else:
                    await plan_step(db, job, scope)
                job.updated_at = datetime.now(UTC)
        except DBAPIError:
            logger.exception("result_command_database_retry", extra={"job_id": str(identifier)})
        except Exception as exc:
            logger.exception("result_command_failed", extra={"job_id": str(identifier)})
            async with get_session() as db:
                await db.execute(
                    update(ResultJob)
                    .where(ResultJob.id == identifier, ResultJob.status != "cancelled")
                    .values(
                        status="failed",
                        error=str(exc)[:512] if isinstance(exc, ValueError) else "Command failed; retry preparation.",
                    )
                )
    await deliver_planned_runs()


async def deliver_planned_runs() -> None:
    async with get_session() as db:
        rows = (
            await db.execute(
                select(ResultJobMember.run_id)
                .join(ProtocolRun, ProtocolRun.id == ResultJobMember.run_id)
                .join(ResultJob, ResultJob.id == ResultJobMember.job_id)
                .where(
                    ProtocolRun.status == "pending",
                    ResultJob.status.in_(("queued", "completed", "cancelled", "failed")),
                )
                .order_by(ProtocolRun.created_at)
                .limit(100)
            )
        ).all()
    for (identifier,) in rows:
        try:
            await enqueue_protocol_run(identifier)
        except Exception:
            logger.exception("result_command_delivery_failed")
            return
