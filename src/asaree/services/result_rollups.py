"""Coalesced SQL rollups over compact slots, driven by the transactional outbox."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Float, Text, and_, case, cast, delete, false, func, literal, select, true, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.database import get_session
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_run import ProtocolRun
from asaree.models.result_read_model import ResultCellMetric, ResultCellRollup, ResultRefresh
from asaree.services.indexed_row_source import read_indexed_row_source
from asaree.services.result_reads import (
    ResultScope,
    cell_predicates,
    parent_predicates,
    primary_runtime_metric,
    resolve_result_scope,
    runtime_metrics,
    slot_query,
)

logger = logging.getLogger(__name__)
STATUSES = ("not_started", "pending", "running", "finalizing", "completed", "failed", "cancelled", "limit_reached")


def rollup_query(scope: ResultScope) -> Any:
    slots = slot_query(scope).cte("slots")
    parent_query = select(
        FactorialCell.id.label("cell_id"), func.count(FactorialReplicateResult.id).label("replicate_count")
    )
    parents = (
        parent_query.outerjoin(
            FactorialReplicateResult,
            and_(FactorialReplicateResult.cell_id == FactorialCell.id, *parent_predicates(scope)),
        )
        .where(
            FactorialCell.experiment_id == scope.experiment_id,
            FactorialCell.design_revision_id == scope.design_revision_id,
            *cell_predicates(scope),
        )
        .group_by(FactorialCell.id)
        .cte("parents")
    )
    stats_columns = [slots.c.cell_id, func.count().label("total")]
    observations = (
        func.jsonb_array_elements(func.coalesce(slots.c.result["observations"], cast(literal("[]"), JSONB)))
        .table_valued("value")
        .lateral("observations")
    )
    measured = (
        select(literal(1))
        .select_from(observations)
        .where(cast(observations.c.value, JSONB)["status"].astext == "measured")
        .correlate(slots)
        .exists()
    )
    stats_columns.append(
        func.count().filter(and_(slots.c.status == "completed", (slots.c.run_id.is_(None) | measured))).label("scored")
    )
    stats_columns += [func.count().filter(slots.c.status == status).label(status) for status in STATUSES]
    stats_columns += [
        func.count().filter(slots.c.run_id.is_not(None)).label("executed"),
        func.count().filter(slots.c.result["truncated"].as_boolean().is_(True)).label("truncated"),
        func.count().filter(slots.c.result["evaluation_state"].as_string() == "running").label("evaluating"),
        func.count().filter(slots.c.usage["complete"].as_boolean().is_(True)).label("usage_complete"),
        func.count()
        .filter(
            and_(
                slots.c.run_id.is_not(None),
                slots.c.status.in_(("completed", "failed", "cancelled", "limit_reached")),
                slots.c.usage["settled"].as_boolean().is_not(True),
            )
        )
        .label("usage_pending"),
    ]
    for key in ("cost_usd", "input_tokens", "output_tokens", "total_tokens", "duration_seconds", "agent_run_count"):
        stats_columns.append(func.sum(slots.c.usage[key].as_float()).label(key))
    stats = select(*stats_columns).group_by(slots.c.cell_id).cte("stats")
    metrics = func.jsonb_each(slots.c.result["metrics"]).table_valued("key", "value").lateral("metrics")
    identities = {
        identifier: metric["key"]
        for identifier, metric in runtime_metrics(scope).items()
        if metric["aggregation"] in {"mean", "sum"}
    }
    metric_key = case(identities, value=metrics.c.key, else_=None) if identities else metrics.c.key
    metric_query = select(
        slots.c.cell_id,
        metric_key.label("key"),
        func.avg(cast(metrics.c.value, Float)).label("mean"),
        func.sum(cast(metrics.c.value, Float)).label("sum"),
        func.count().label("count"),
    )
    values = (
        metric_query.select_from(slots)
        .join(metrics, true())
        .where(
            metric_key.is_not(None) if identities else false(),
            slots.c.status == "completed",
            slots.c.result["truncated"].as_boolean().is_(False),
        )
        .group_by(slots.c.cell_id, metric_key)
        .cte("metric_values")
    )
    metric_stats = (
        select(
            values.c.cell_id,
            func.jsonb_object_agg(
                values.c.key,
                func.jsonb_build_object("mean", values.c.mean, "sum", values.c.sum, "count", values.c.count),
            ).label("metrics"),
        )
        .group_by(values.c.cell_id)
        .cte("metric_stats")
    )
    usage_keys = ("cost_usd", "input_tokens", "output_tokens", "total_tokens", "duration_seconds", "agent_run_count")
    all_usage = (
        select(
            func.jsonb_build_object(
                *[part for key in usage_keys for part in (key, func.sum(ProtocolRun.usage_summary[key].as_float()))]
            ).label("all_attempt_usage")
        )
        .where(
            ProtocolRun.protocol_id == scope.protocol_id,
            ProtocolRun.protocol_revision_id == scope.protocol_revision_id,
            ProtocolRun.design_revision_id == scope.design_revision_id,
            ProtocolRun.is_test_run.is_(False),
            ProtocolRun.target_node_id.is_(None),
        )
        .cte("all_usage")
    )
    return (
        select(
            all_usage.c.all_attempt_usage,
            parents,
            *[column for column in stats.c if column.key != "cell_id"],
            metric_stats.c.metrics,
        )
        .select_from(
            all_usage.outerjoin(parents, true())
            .outerjoin(stats, stats.c.cell_id == parents.c.cell_id)
            .outerjoin(metric_stats, metric_stats.c.cell_id == parents.c.cell_id)
        )
        .order_by(parents.c.cell_id)
    )


async def rebuild_result_summary(db: AsyncSession, scope: ResultScope, generation: int) -> None:
    """One SQL snapshot supplies both cell and overall counts, published atomically.

    Rebuilding from current contributions makes repeated deliveries and reruns
    idempotent. No decrement/increment arithmetic can drift after a worker crash.
    Cell rows are streamed and upserted in batches to bound worker memory.
    """
    existing = await db.get(ResultRefresh, scope.key)
    cached = existing.summary if existing is not None else None
    totals: dict[str, int] = {status: 0 for status in STATUSES}
    totals.update(
        total=0,
        cells=0,
        replicates=0,
        truncated=0,
        evaluating=0,
        usage_complete=0,
        usage_pending=0,
        executed=0,
        scored=0,
    )
    usage: dict[str, float | None] = {
        key: None
        for key in ("cost_usd", "input_tokens", "output_tokens", "total_tokens", "duration_seconds", "agent_run_count")
    }
    aggregations = {metric["key"]: metric["aggregation"] for metric in runtime_metrics(scope).values()}
    batch = []
    result = await db.stream(rollup_query(scope))
    all_usage_totals: dict[str, Any] = {}
    async for row in result.mappings():
        all_usage_totals = row["all_attempt_usage"]
        if row["cell_id"] is None:
            continue
        counts = {status: int(row[status] or 0) for status in STATUSES}
        metrics = row["metrics"] or {}
        data = {
            "replicate_count": int(row["replicate_count"]),
            "total": int(row["total"] or 0),
            "counts": counts,
            "truncated": int(row["truncated"] or 0),
            "usage": {key: row[key] for key in usage},
            "metric_values": {
                key: value["sum" if aggregations.get(key) == "sum" else "mean"] for key, value in metrics.items()
            }
            if scope.unit == "replicate"
            else {},
            "metric_counts": {key: value["count"] for key, value in metrics.items()}
            if scope.unit == "replicate"
            else {},
        }
        totals["cells"] += 1
        totals["replicates"] += int(row["replicate_count"])
        for key in (
            "total",
            "truncated",
            "evaluating",
            "usage_complete",
            "usage_pending",
            "executed",
            "scored",
            *STATUSES,
        ):
            totals[key] += int(row[key] or 0)
        for key in usage:
            if row[key] is not None:
                usage[key] = (usage[key] or 0) + float(row[key])
        batch.append({"scope_key": scope.key, "cell_id": row["cell_id"], "generation": generation, "data": data})
        if len(batch) == 100:
            await _write_cells(db, batch)
            batch = []
    if batch:
        await _write_cells(db, batch)
    await db.execute(
        delete(ResultCellRollup).where(
            ResultCellRollup.scope_key == scope.key,
            ResultCellRollup.generation != generation,
        )
    )
    await db.execute(
        delete(ResultCellMetric).where(
            ResultCellMetric.scope_key == scope.key,
            ResultCellMetric.generation != generation,
        )
    )
    row_count = cached.get("source_row_count") if cached else None
    if scope.row_plan is not None and row_count is None:
        dataset_id = scope.row_plan.get("driver_dataset_id")
        import uuid

        from asaree.models.dataset import RegisteredDataset

        dataset = await db.get(RegisteredDataset, uuid.UUID(dataset_id)) if dataset_id else None
        if dataset is not None:
            from pathlib import Path

            from asaree.config import get_settings
            from asaree.services.dataset_row_csv import DatasetRowCsvError

            try:
                indexed = await asyncio.to_thread(
                    read_indexed_row_source,
                    dataset_id=str(dataset.id),
                    raw_path=dataset.raw_path,
                    raw_sha256=dataset.raw_sha256,
                    cache_root=Path(get_settings().result_row_index_dir),
                )
                row_count = len(indexed.rows)
            except DatasetRowCsvError:
                row_count = None
    physical_total = totals["total"]
    if scope.unit == "row" and row_count is not None:
        expected = row_count * totals["replicates"]
        totals["not_started"] += max(0, expected - physical_total)
        totals["total"] = max(expected, physical_total)
        # Cell cards use the same forecast as the overview, rather than showing
        # only slots already materialized by the planner.
        await db.execute(
            update(ResultCellRollup)
            .where(ResultCellRollup.scope_key == scope.key)
            .values(
                data=func.jsonb_set(
                    func.jsonb_set(
                        ResultCellRollup.data,
                        cast(literal("{total}"), ARRAY(Text)),
                        func.to_jsonb(ResultCellRollup.data["replicate_count"].as_integer() * row_count),
                    ),
                    cast(literal("{counts,not_started}"), ARRAY(Text)),
                    func.to_jsonb(
                        func.greatest(
                            0,
                            ResultCellRollup.data["replicate_count"].as_integer() * row_count
                            - ResultCellRollup.data["total"].as_integer(),
                        )
                        + ResultCellRollup.data["counts"]["not_started"].as_integer()
                    ),
                )
            )
        )
    primary, direction = primary_runtime_metric(scope)
    if scope.unit == "replicate" and primary:
        ranks = (
            select(
                ResultCellMetric.cell_id,
                func.row_number()
                .over(
                    order_by=(
                        ResultCellMetric.value.asc() if direction == "minimize" else ResultCellMetric.value.desc(),
                        ResultCellMetric.cell_id,
                    )
                )
                .label("rank"),
            )
            .where(
                ResultCellMetric.scope_key == scope.key,
                ResultCellMetric.metric_key == primary,
                ResultCellMetric.generation == generation,
                ResultCellMetric.value.is_not(None),
            )
            .cte("primary_ranks")
        )
        await db.execute(
            update(ResultCellRollup)
            .where(ResultCellRollup.scope_key == scope.key, ResultCellRollup.cell_id == ranks.c.cell_id)
            .values(
                data=func.jsonb_set(
                    ResultCellRollup.data, cast(literal("{primary_rank}"), ARRAY(Text)), func.to_jsonb(ranks.c.rank)
                )
            )
        )
    best_cell = None
    if scope.unit == "replicate" and primary:
        best = (
            (
                await db.execute(
                    select(ResultCellMetric.cell_id, FactorialCell.cell_label, ResultCellMetric.value)
                    .join(FactorialCell)
                    .where(
                        ResultCellMetric.scope_key == scope.key,
                        ResultCellMetric.metric_key == primary,
                        ResultCellMetric.generation == generation,
                        ResultCellMetric.value.is_not(None),
                    )
                    .order_by(
                        ResultCellMetric.value.asc() if direction == "minimize" else ResultCellMetric.value.desc(),
                        ResultCellMetric.cell_id,
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if best:
            best_cell = {
                "cell_id": str(best["cell_id"]),
                "cell_label": best["cell_label"],
                "metric_key": primary,
                "value": best["value"],
            }
    summary = {
        "counts": {status: totals[status] for status in STATUSES},
        "total": totals["total"],
        "planned_count": physical_total,
        "executed_count": totals["executed"],
        "scored_count": totals["scored"],
        "best_cell": best_cell,
        "cell_count": totals["cells"],
        "replicate_count": totals["replicates"],
        "usage": usage,
        "usage_complete_count": totals["usage_complete"],
        "truncated_count": totals["truncated"],
        "evaluating_count": totals["evaluating"],
        "source_row_count": row_count,
        "expected": row_count * totals["replicates"]
        if row_count is not None
        else None
        if scope.unit == "row"
        else totals["total"],
        "has_active": any(totals[key] for key in ("pending", "running", "finalizing", "evaluating", "usage_pending")),
        "usage_pending_count": totals["usage_pending"],
        "as_of": datetime.now(UTC).isoformat(),
    }
    summary["all_attempt_usage"] = all_usage_totals
    # generation may have advanced during this rebuild. Preserve the new dirty
    # generation; the next delivery rebuilds it. Cell/overview publication is one transaction.
    await db.execute(
        update(ResultRefresh)
        .where(ResultRefresh.scope_key == scope.key)
        .values(
            summary=summary,
            processed_generation=generation,
            error=None,
        )
    )


async def _write_cells(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    statement = insert(ResultCellRollup).values(rows)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[ResultCellRollup.scope_key, ResultCellRollup.cell_id],
            set_={"generation": statement.excluded.generation, "data": statement.excluded.data},
        )
    )
    metric_rows = [
        {
            "scope_key": row["scope_key"],
            "cell_id": row["cell_id"],
            "generation": row["generation"],
            "metric_key": key,
            "value": value,
        }
        for row in rows
        for key, value in row["data"]["metric_values"].items()
    ]
    if metric_rows:
        statement = insert(ResultCellMetric).values(metric_rows)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[ResultCellMetric.scope_key, ResultCellMetric.metric_key, ResultCellMetric.cell_id],
                set_={"value": statement.excluded.value, "generation": statement.excluded.generation},
            )
        )


async def refresh_result_summaries(_ctx: dict[str, Any]) -> None:
    """Durable work survives Redis loss; the existing worker sweeps the SQL outbox."""
    async with get_session() as db:
        keys = list(
            (
                await db.execute(
                    select(ResultRefresh.scope_key)
                    .where(
                        ResultRefresh.generation != ResultRefresh.processed_generation,
                    )
                    .order_by(ResultRefresh.updated_at)
                    .limit(8)
                )
            ).scalars()
        )
    for key in keys:
        try:
            async with get_session() as db:
                # At most one projector publishes this scope, without holding an
                # outbox row lock while execution workers deliver new changes.
                locked = await db.scalar(select(func.pg_try_advisory_xact_lock(func.hashtextextended(key, 0))))
                if not locked:
                    continue
                refresh = await db.get(ResultRefresh, key)
                if refresh is None or refresh.generation == refresh.processed_generation:
                    continue
                experiment_id = await db.scalar(
                    select(Protocol.experiment_id).where(Protocol.id == refresh.protocol_id)
                )
                if experiment_id is None:
                    continue
                scope = await resolve_result_scope(
                    db,
                    experiment_id=experiment_id,
                    protocol_id=refresh.protocol_id,
                    protocol_revision_id=refresh.protocol_revision_id,
                    design_revision_id=refresh.design_revision_id,
                    use_current_publication=False,
                )
                if scope.key != key:
                    await db.execute(
                        update(ResultRefresh)
                        .where(ResultRefresh.scope_key == key)
                        .values(
                            processed_generation=refresh.generation,
                        )
                    )
                    continue
                await rebuild_result_summary(db, scope, refresh.generation)
        except Exception:
            logger.exception("result_summary_refresh_failed", extra={"scope_key": key})
            async with get_session() as failed_db:
                await failed_db.execute(
                    update(ResultRefresh)
                    .where(ResultRefresh.scope_key == key)
                    .values(error="Summary refresh failed; retrying in the background.", updated_at=func.now())
                )
