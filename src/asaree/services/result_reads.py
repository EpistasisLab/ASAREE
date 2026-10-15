"""Bounded reads over stable execution slots and rebuildable version summaries.

No normal read consults Motoro or selects an attempt's evidence documents.
The same scope and slot query is used by reads, rollups, batch controls and exports.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import BigInteger, Integer, Text, and_, case, cast, false, func, literal, or_, select, true, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, array, insert
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.result_job import ResultJob, ResultJobMember
from asaree.models.result_read_model import ResultCellMetric, ResultCellRollup, ResultRefresh
from asaree.services.dataset_row_inputs import resolve_dataset_row_plan
from asaree.services.execution_selection import cell_filters, natural_label, slot_filters
from asaree.services.experiment_run_results import (
    RunResultsProjectionError,
    _node_labels,
)
from asaree.services.measurement_engine import parse_measurement_plan
from asaree.services.measurement_migration import normalize_experiment_measurement_plan
from asaree.services.metrics import METRIC_CATALOG, normalize_metrics

PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class ResultScope:
    experiment_id: uuid.UUID
    protocol_id: uuid.UUID
    protocol_revision_id: uuid.UUID | None
    design_revision_id: uuid.UUID | None
    design_spec: dict[str, Any] | None
    graph: dict[str, Any]
    versioned: bool
    row_plan: dict[str, Any] | None
    measurement_plan: dict[str, Any] | None = None

    @property
    def key(self) -> str:
        return f"{self.protocol_id}:{self.protocol_revision_id or 'legacy'}:{self.design_revision_id or 'none'}"

    @property
    def unit(self) -> str:
        return "row" if self.row_plan is not None else "replicate"


def runtime_metrics(scope: ResultScope) -> dict[str, dict[str, Any]]:
    """Scalar identity comes from builtin producer bindings, never a value/name."""
    catalog = {item["key"]: item for item in METRIC_CATALOG}
    definitions = {}
    for metric in normalize_metrics((scope.design_spec or {}).get("metrics")):
        key = metric.get("catalogKey")
        if metric["kind"] == "runtime" and key in catalog:
            definitions[metric["id"]] = {**metric, "key": key}
    if scope.measurement_plan:
        plan = parse_measurement_plan(normalize_experiment_measurement_plan(scope.measurement_plan, None))
        builtin = {
            identifier: output
            for binding in plan.producers
            if binding.producer_id in {"asaree.runtime", "asaree.node_runtime"}
            for output, identifier in binding.outputs.items()
        }
        definitions = {
            identifier: definition for identifier, definition in definitions.items() if identifier in builtin
        }
        for declaration in plan.metrics:
            if declaration.id not in builtin:
                continue
            if declaration.value_type == "opaque":
                definitions.pop(declaration.id, None)
                continue
            defaults = catalog.get(builtin[declaration.id], {})
            previous = definitions.get(declaration.id, {})
            definitions[declaration.id] = {
                "id": declaration.id,
                "name": declaration.name,
                "key": previous.get("key", declaration.id),
                "valueType": declaration.value_type or defaults.get("valueType", "number"),
                "aggregation": declaration.aggregation
                or previous.get("aggregation")
                or defaults.get("aggregation", "sum"),
                "direction": declaration.direction
                or previous.get("direction")
                or defaults.get("defaultDirection", "neutral"),
                "primary": declaration.primary if declaration.primary is not None else previous.get("primary", False),
            }
    return definitions


def primary_runtime_metric(scope: ResultScope) -> tuple[str | None, str | None]:
    for metric in runtime_metrics(scope).values():
        if (
            metric.get("primary")
            and metric.get("direction") in {"maximize", "minimize"}
            and metric.get("aggregation") in {"mean", "sum"}
        ):
            return metric["key"], metric["direction"]
    return None, None


def cell_predicates(scope: ResultScope) -> list[Any]:
    """A publication's declared cohort cannot grow with its additive revision.

    Legacy/imported designs without declarations retain revision-based scope.
    Factor comparisons use original JSON types and never enumerate combinations.
    """
    factors = (scope.design_spec or {}).get("factors")
    if not scope.versioned or not isinstance(factors, list) or not factors:
        return []
    predicates: list[Any] = []
    names = []
    for factor in factors:
        if not isinstance(factor, dict):
            return [false()]
        name, levels = factor.get("name"), factor.get("levels")
        if not isinstance(name, str) or not isinstance(levels, list) or not levels:
            return [false()]
        names.append(name)
        predicates.append(
            FactorialCell.factor_values[name].in_(
                [cast(literal(json.dumps(level, allow_nan=False)), JSONB) for level in levels]
            )
        )
    predicates.append(
        FactorialCell.factor_values.op("-")(cast(array(names), ARRAY(Text))) == cast(literal("{}"), JSONB)
    )
    return predicates


def parent_predicates(scope: ResultScope) -> list[Any]:
    if not cell_predicates(scope):
        return []
    count = (scope.design_spec or {}).get("replicates", 1)
    return (
        [FactorialReplicateResult.replicate_number <= count]
        if isinstance(count, int) and count > 0
        else [false()]
    )


async def resolve_result_scope(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    protocol_id: uuid.UUID | None = None,
    protocol_revision_id: uuid.UUID | None = None,
    design_revision_id: uuid.UUID | None = None,
    use_current_publication: bool = True,
) -> ResultScope:
    statement = select(Protocol.id, Protocol.published_revision_id).where(Protocol.experiment_id == experiment_id)
    if protocol_id is not None:
        statement = statement.where(Protocol.id == protocol_id)
    protocols = (await db.execute(statement.limit(2))).all()
    if not protocols:
        raise RunResultsProjectionError("protocol_not_found")
    if len(protocols) > 1:
        raise RunResultsProjectionError("ambiguous_protocol")
    selected_protocol_id, current_publication = protocols[0]
    publication_id = (protocol_revision_id or current_publication) if use_current_publication else protocol_revision_id
    publication = None
    if publication_id is not None:
        publication = await db.scalar(
            select(ProtocolRevision).where(
                ProtocolRevision.id == publication_id,
                ProtocolRevision.protocol_id == selected_protocol_id,
            )
        )
        if publication is None:
            raise RunResultsProjectionError("protocol_revision_not_found")
    versioned = publication is not None and publication.experiment_snapshot is not None
    if versioned:
        assert publication is not None and publication.experiment_snapshot is not None
        if design_revision_id is not None and design_revision_id != publication.design_revision_id:
            raise RunResultsProjectionError("design_does_not_belong_to_experiment_version")
        design_revision_id = publication.design_revision_id
    revision_query = select(ExperimentDesignRevision).where(
        ExperimentDesignRevision.experiment_id == experiment_id,
    )
    if design_revision_id is not None:
        revision_query = revision_query.where(ExperimentDesignRevision.id == design_revision_id)
    else:
        revision_query = revision_query.where(ExperimentDesignRevision.superseded_at.is_(None))
    revision = None if versioned and design_revision_id is None else await db.scalar(revision_query)
    if design_revision_id is not None and revision is None:
        raise RunResultsProjectionError("design_revision_not_found")
    design = revision.design_spec if revision else None
    if versioned:
        assert publication is not None and publication.experiment_snapshot is not None
        design = publication.experiment_snapshot.get("design_spec")
    graph = publication.graph if publication is not None and isinstance(publication.graph, dict) else {}
    return ResultScope(
        experiment_id,
        selected_protocol_id,
        publication_id,
        revision.id if revision else None,
        design,
        graph,
        versioned,
        resolve_dataset_row_plan(graph) if publication is not None else None,
        publication.experiment_snapshot.get("measurement_plan")
        if versioned and publication and publication.experiment_snapshot
        else None,
    )


def legacy_flag(scope: ResultScope) -> Any:
    """Whether pre-versioning (legacy) runs can match, as a SQL constant.

    Written into the statement, never bound as a parameter: Postgres folds a
    constant away even in a generic plan, so ``replicate_result_id = x OR (false
    AND ...)`` keeps using the per-replicate index. Bound, a reused connection's
    generic plan couldn't simplify the OR and scanned every run of the design
    for each slot -- the summary rebuild of a 15,786-run experiment went from
    0.4 s to 66 s mid-batch.
    """
    return false() if scope.versioned else true()


def slot_query(scope: ResultScope, *, cutoff: Any = None) -> Any:
    """One current execution per stable slot, including slots never attempted.

    Lateral lookup uses the slot/version indexes; no client-sized IN lists or full
    JSON documents participate. Legacy label fallback also matches factor values.
    """
    cell, parent, run, row = FactorialCell, FactorialReplicateResult, ProtocolRun, FactorialRowResult
    base = (
        select(
            cell.id.label("cell_id"),
            cell.cell_label,
            cell.factor_values,
            parent.id.label("replicate_id"),
            parent.replicate_label,
            parent.replicate_number,
        )
        .select_from(cell)
        .join(parent, parent.cell_id == cell.id)
        .where(
            cell.experiment_id == scope.experiment_id,
            cell.design_revision_id == scope.design_revision_id,
            *cell_predicates(scope),
            *parent_predicates(scope),
        )
    )
    if scope.unit == "row":
        base = (
            base.join(row, row.replicate_result_id == parent.id)
            .where(
                row.protocol_revision_id == scope.protocol_revision_id,
            )
            .add_columns(
                row.id.label("slot_id"),
                row.id.label("row_result_id"),
                row.row_index,
                row.dataset_id,
                row.raw_sha256,
            )
        )
        match = run.row_result_id == row.id
    else:
        base = base.add_columns(
            parent.id.label("slot_id"),
            literal(None).label("row_result_id"),
            cast(literal(None), BigInteger).label("row_index"),
            literal(None).label("dataset_id"),
            literal(None).label("raw_sha256"),
        )
        match = and_(
            run.row_result_id.is_(None),
            or_(
                run.replicate_result_id == parent.id,
                and_(
                    run.replicate_result_id.is_(None),
                    legacy_flag(scope),
                    run.replicate_label == parent.replicate_label,
                    run.factor_values == cell.factor_values,
                ),
            ),
        )
    latest = (
        select(
            run.id.label("run_id"),
            run.status,
            run.created_at,
            run.updated_at,
            run.started_at,
            run.completed_at,
            func.left(run.error, 512).label("error"),
            run.result_summary,
            run.usage_summary,
            func.count().over().label('attempt_count'),
            func.bool_or(run.status == 'completed').over().label('has_previous_success'),
        )
        .where(
            run.protocol_id == scope.protocol_id,
            or_(
                run.design_revision_id == scope.design_revision_id,
                and_(legacy_flag(scope), run.design_revision_id.is_(None)),
            ),
            run.protocol_revision_id == scope.protocol_revision_id,
            match,
            run.is_test_run.is_(False),
            run.target_node_id.is_(None),
            run.created_at <= cutoff if cutoff is not None else true(),
        )
        .order_by(run.created_at.desc(), run.id.desc())
        .limit(1)
        .correlate(cell, parent, row)
        .lateral("latest")
    )
    imported = and_(
        true() if not scope.versioned and scope.unit == "replicate" else false(),
        parent.metric_values.is_not(None),
        func.jsonb_typeof(parent.metric_values) == "object",
        parent.metric_values != cast(literal("{}"), JSONB),
        or_(parent.run_id.is_(None), ~select(ProtocolRun.id).where(ProtocolRun.id == parent.run_id).exists()),
    )
    status = func.coalesce(latest.c.status, case((imported, "completed"), else_="not_started"))
    return base.outerjoin(latest, true()).add_columns(
        latest.c.run_id,
        status.label("status"),
        latest.c.created_at,
        latest.c.updated_at,
        latest.c.started_at,
        latest.c.completed_at,
        latest.c.error,
        func.coalesce(latest.c.result_summary, cast(literal("{}"), JSONB)).label("result"),
        latest.c.usage_summary.label("usage"),
        func.coalesce(latest.c.attempt_count, 0).label('attempt_count'),
        latest.c.has_previous_success,
    )


def preview_slots(scope: ResultScope, job_id: uuid.UUID) -> Any:
    """Read exactly the sealed members, including row slots not materialized yet."""
    member, parent, cell, run, job = ResultJobMember, FactorialReplicateResult, FactorialCell, ProtocolRun, ResultJob
    return select(
        cell.id.label('cell_id'), cell.cell_label, cell.factor_values,
        parent.id.label('replicate_id'), parent.replicate_label, parent.replicate_number,
        (func.coalesce(cast(member.slot_id, Text), cast(run.row_result_id, Text),
                       literal('unplanned:') + cast(parent.id, Text) + literal(':') + cast(member.row_index, Text))
         if scope.unit == 'row' else cast(parent.id, Text)).label('slot_id'),
        (func.coalesce(member.slot_id, run.row_result_id) if scope.unit == 'row'
         else literal(None)).label('row_result_id'),
        (member.row_index if scope.unit == 'row' else cast(literal(None), BigInteger)).label('row_index'),
        literal(None).label('dataset_id'), literal(None).label('raw_sha256'),
        run.id.label('run_id'), func.coalesce(run.status, 'not_started').label('status'),
        run.updated_at, run.error, run.result_summary.label('result'), run.usage_summary.label('usage'),
    ).select_from(member).join(job, job.id == member.job_id).join(parent, parent.id == member.replicate_id).join(
        cell, cell.id == parent.cell_id).outerjoin(run, run.id == case(
            (job.status.in_(('pending', 'awaiting_confirmation', 'cancelled')), member.expected_run_id),
            else_=func.coalesce(member.run_id, member.expected_run_id))).where(
        job.id == job_id, job.experiment_id == scope.experiment_id, job.protocol_id == scope.protocol_id,
        job.protocol_revision_id == scope.protocol_revision_id, job.design_revision_id == scope.design_revision_id,
        cell.experiment_id == scope.experiment_id, cell.design_revision_id == scope.design_revision_id)


def forecast_slots(scope: ResultScope, row_count: int) -> Any:
    """Forecast rows in SQL so unstarted-row filters include unmaterialized slots.

    Only a bounded ordered page leaves PostgreSQL; no Cartesian product is
    constructed in the API process or sent to the browser.
    """
    parent, cell = FactorialReplicateResult, FactorialCell
    indexes = func.generate_series(0, row_count - 1).table_valued('row_index').render_derived()
    actual = slot_query(scope).subquery('actual_rows')
    return select(
        cell.id.label('cell_id'), cell.cell_label, cell.factor_values,
        parent.id.label('replicate_id'), parent.replicate_label, parent.replicate_number,
        func.coalesce(cast(actual.c.slot_id, Text), literal('unplanned:') + cast(parent.id, Text) +
                      literal(':') + cast(indexes.c.row_index, Text)).label('slot_id'),
        actual.c.row_result_id, indexes.c.row_index,
        actual.c.dataset_id, actual.c.raw_sha256, actual.c.run_id,
        func.coalesce(actual.c.status, 'not_started').label('status'),
        actual.c.updated_at, actual.c.error, actual.c.result, actual.c.usage,
        actual.c.attempt_count, actual.c.has_previous_success,
    ).select_from(cell).join(parent, parent.cell_id == cell.id).join(indexes, true()).outerjoin(
        actual, and_(actual.c.replicate_id == parent.id, actual.c.row_index == indexes.c.row_index)
    ).where(cell.experiment_id == scope.experiment_id, cell.design_revision_id == scope.design_revision_id,
            *cell_predicates(scope), *parent_predicates(scope))


def encode_cursor(scope: ResultScope, kind: str, position: list[Any], filters: dict[str, Any]) -> str:
    document = {"scope": scope.key, "kind": kind, "position": position, "filters": filters}
    return base64.urlsafe_b64encode(json.dumps(document, separators=(",", ":")).encode()).decode()


def decode_cursor(cursor: str | None, scope: ResultScope, kind: str, filters: dict[str, Any]) -> list[Any] | None:
    if cursor is None:
        return None
    if len(cursor) > 2048:
        raise ValueError("invalid_cursor")
    try:
        document = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if document["scope"] != scope.key or document["kind"] != kind or document["filters"] != filters:
            raise ValueError("cursor_scope_mismatch")
        position = document["position"]
        if not isinstance(position, list):
            raise ValueError("invalid_cursor")
        return position
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_cursor") from exc


def page_size(size: int) -> int:
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise ValueError("invalid_page_size")
    return size


async def request_refresh(db: AsyncSession, scope: ResultScope) -> None:
    await db.execute(
        insert(ResultRefresh)
        .values(
            scope_key=scope.key,
            protocol_id=scope.protocol_id,
            protocol_revision_id=scope.protocol_revision_id,
            design_revision_id=scope.design_revision_id,
            generation=1,
            processed_generation=0,
        )
        .on_conflict_do_nothing(index_elements=[ResultRefresh.scope_key])
    )


async def read_summary(db: AsyncSession, scope: ResultScope) -> dict[str, Any]:
    refresh = await db.get(ResultRefresh, scope.key)
    if refresh is None:
        await request_refresh(db, scope)
    metrics = list(runtime_metrics(scope).values())
    primary, direction = primary_runtime_metric(scope)
    return {
        "scope_key": scope.key,
        "unit": scope.unit,
        "protocol_id": str(scope.protocol_id),
        "protocol_revision_id": str(scope.protocol_revision_id) if scope.protocol_revision_id else None,
        "design_revision_id": str(scope.design_revision_id) if scope.design_revision_id else None,
        "ready": bool(refresh and refresh.summary is not None),
        "generation": refresh.processed_generation if refresh else 0,
        "change_id": refresh.generation if refresh else 1,
        "refreshing": refresh is None or refresh.generation != refresh.processed_generation,
        "updated_at": refresh.updated_at if refresh else None,
        "design_spec": scope.design_spec,
        "metric_types": {
            metric["key"]: metric["valueType"] for metric in metrics if metric["aggregation"] in {"mean", "sum"}
        },
        "metric_labels": {metric["key"]: metric["name"] for metric in metrics},
        "metric_aggregations": {metric["key"]: metric["aggregation"] for metric in metrics},
        "metric_directions": {metric["key"]: metric["direction"] for metric in metrics},
        "primary_metric": primary if scope.unit == "replicate" else None,
        "primary_metric_direction": direction,
        **(refresh.summary if refresh and refresh.summary is not None else {}),
    }


async def read_executions(
    db: AsyncSession,
    scope: ResultScope,
    *,
    cursor: str | None = None,
    limit: int = PAGE_SIZE,
    cell_id: uuid.UUID | None = None,
    replicate_id: uuid.UUID | None = None,
    status: str | None = None,
    search: str = '',
    factors: dict[str, Any] | None = None,
    sort: str = 'number',
    sort_direction: str = 'asc',
    job_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if sort not in {'number', 'status', 'updated'} or sort_direction not in {'asc', 'desc'}:
        raise ValueError('invalid_sort')
    filters = {
        "cell_id": str(cell_id) if cell_id else None,
        "replicate_id": str(replicate_id) if replicate_id else None,
        "status": status,
        'search': search, 'factors': factors or {}, 'sort': sort, 'sort_direction': sort_direction,
        'job_id': str(job_id) if job_id else None,
    }
    refresh = await db.get(ResultRefresh, scope.key) if scope.unit == "row" else None
    row_count = (refresh.summary or {}).get("source_row_count") if refresh else None
    position = decode_cursor(cursor, scope, "executions", filters)
    query = (preview_slots(scope, job_id) if job_id else
             forecast_slots(scope, int(row_count)) if scope.unit == 'row' and row_count is not None
             and status in {None, 'not_started'} else slot_query(scope))
    query = query.where(*cell_filters(FactorialCell, search, factors))
    if cell_id is not None:
        query = query.where(FactorialCell.id == cell_id)
    if replicate_id is not None:
        query = query.where(FactorialReplicateResult.id == replicate_id)
    slots = query.subquery("slots")
    statement = select(slots)
    statement = statement.where(*slot_filters(slots, status))
    status_order = case((slots.c.status == 'failed', 0),
                        (or_(slots.c.status == 'limit_reached', slots.c.result['truncated'].astext == 'true'), 1),
                        (slots.c.result['evaluation_state'].astext == 'failed', 2),
                        (slots.c.status == 'cancelled', 3), (slots.c.status == 'running', 4),
                        (slots.c.status == 'finalizing', 5), (slots.c.status == 'pending', 6),
                        (slots.c.status == 'not_started', 7), else_=8)
    order_value = (func.coalesce(func.extract('epoch', slots.c.updated_at), 0) if sort == 'updated' else
                   status_order if sort == 'status' else slots.c.replicate_number)
    # Include the dataset row in the ordering; the slot id breaks remaining ties.
    row_order = func.coalesce(cast(slots.c.row_index, BigInteger), -1)
    if position is not None:
        if len(position) != 3:
            raise ValueError("invalid_cursor")
        key = tuple_(order_value, row_order, cast(slots.c.slot_id, Text))
        bound = (position[0], position[1], position[2])
        statement = statement.where(key > bound if sort_direction == 'asc' else key < bound)
    orders = [order_value, row_order, cast(slots.c.slot_id, Text)]
    rows = (await db.execute(statement.add_columns(order_value.label('order_value')).order_by(
        *[value.asc() if sort_direction == 'asc' else value.desc() for value in orders]
    ).limit(page_size(limit) + 1))).mappings().all()
    items = [execution_payload(dict(row)) for row in rows[:limit]]
    return {
        "items": items,
        "next_cursor": encode_cursor(scope, "executions", [
            float(rows[limit - 1]['order_value']) if sort == 'updated' else rows[limit - 1]['order_value'],
            rows[limit - 1]['row_index'] if rows[limit - 1]['row_index'] is not None else -1,
            str(rows[limit - 1]['slot_id'])], filters)
        if len(rows) > limit
        else None,
    }


def execution_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: str(row[key]) if row.get(key) is not None else None
            for key in ("slot_id", "cell_id", "replicate_id", "row_result_id", "run_id", "dataset_id")
        },
        **{
            key: row.get(key)
            for key in (
                "cell_label",
                "factor_values",
                "replicate_label",
                "replicate_number",
                "row_index",
                "raw_sha256",
                "status",
                "error",
                "updated_at",
                'attempt_count',
                'has_previous_success',
            )
        },
        "result": row.get("result") or {},
        "usage": row.get("usage"),
    }


async def read_cells(
    db: AsyncSession,
    scope: ResultScope,
    *,
    cursor: str | None = None,
    limit: int = PAGE_SIZE,
    metric: str | None = None,
    direction: str = "maximize",
    search: str = '',
    status: str | None = None,
    factors: dict[str, Any] | None = None,
    sort: str = 'label',
    sort_direction: str = 'asc',
    job_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if sort not in {'label', 'failed', 'updated'} and not sort.startswith('factor:'):
        raise ValueError('invalid_sort')
    if sort_direction not in {'asc', 'desc'}:
        raise ValueError('invalid_sort_direction')
    if metric is not None and (
        scope.unit == "row" or metric not in {m["key"] for m in runtime_metrics(scope).values()}
    ):
        raise ValueError("invalid_sort_metric")
    if direction not in {"minimize", "maximize"}:
        raise ValueError("invalid_sort_direction")
    filters = {"metric": metric, "direction": direction, 'search': search, 'status': status,
               'factors': factors or {}, 'sort': sort, 'sort_direction': sort_direction,
               'job_id': str(job_id) if job_id else None}
    position = decode_cursor(cursor, scope, "cells", filters)
    cached = ResultCellRollup
    statement = (
        select(FactorialCell.id, FactorialCell.cell_label, FactorialCell.factor_values, cached.data)
        .outerjoin(
            cached,
            and_(cached.cell_id == FactorialCell.id, cached.scope_key == scope.key),
        )
        .where(
            FactorialCell.experiment_id == scope.experiment_id,
            FactorialCell.design_revision_id == scope.design_revision_id,
            *cell_predicates(scope),
            *cell_filters(FactorialCell, search, factors),
        )
    )
    matching = (preview_slots(scope, job_id) if job_id else slot_query(scope)).subquery('matching_slots')
    if status or job_id:
        has_match = select(matching.c.slot_id).where(
            matching.c.cell_id == FactorialCell.id, *slot_filters(matching, status)).exists()
        if scope.unit == 'row' and status == 'not_started' and not job_id:
            refresh = await db.get(ResultRefresh, scope.key)
            expected_rows = (refresh.summary or {}).get('source_row_count', 0) if refresh else 0
            actual_rows = select(func.count()).select_from(matching).where(
                matching.c.cell_id == FactorialCell.id).scalar_subquery()
            parents = select(func.count()).select_from(FactorialReplicateResult).where(
                FactorialReplicateResult.cell_id == FactorialCell.id, *parent_predicates(scope)).scalar_subquery()
            has_match = or_(has_match, actual_rows < parents * expected_rows)
        statement = statement.where(has_match)
    rows = []
    if metric:
        refresh = await db.get(ResultRefresh, scope.key)
        generation = refresh.processed_generation if refresh else 0
        if position is not None and (len(position) != 4 or position[3] != generation):
            raise ValueError("stale_sort_cursor")
        ranked = statement.join(
            ResultCellMetric,
            and_(
                ResultCellMetric.scope_key == scope.key,
                ResultCellMetric.cell_id == FactorialCell.id,
                ResultCellMetric.metric_key == metric,
                ResultCellMetric.generation == generation,
                ResultCellMetric.value.is_not(None),
            ),
        )
        value = ResultCellMetric.value
        if position is None or position[0] == 0:
            if position is not None:
                v, identifier = position[1], uuid.UUID(position[2])
                ranked = ranked.where(
                    or_(
                        value < v if direction == "maximize" else value > v,
                        and_(value == v, FactorialCell.id > identifier),
                    )
                )
            ranked = ranked.add_columns(literal(0).label("missing"), value.label("order_value"))
            ranked = ranked.order_by(value.desc() if direction == "maximize" else value.asc(), FactorialCell.id)
            rows = list((await db.execute(ranked.limit(page_size(limit) + 1))).mappings())
        if len(rows) <= limit:
            missing = statement.where(
                ~select(ResultCellMetric.cell_id)
                .where(
                    ResultCellMetric.scope_key == scope.key,
                    ResultCellMetric.metric_key == metric,
                    ResultCellMetric.generation == generation,
                    ResultCellMetric.cell_id == FactorialCell.id,
                    ResultCellMetric.value.is_not(None),
                )
                .exists()
            )
            if position is not None and position[0] == 1:
                missing = missing.where(FactorialCell.id > uuid.UUID(position[2]))
            missing = missing.add_columns(literal(1).label("missing"), literal(None).label("order_value"))
            rows += list((await db.execute(missing.order_by(FactorialCell.id).limit(limit + 1 - len(rows)))).mappings())
    else:
        if sort == 'failed':
            order_value = cast(func.coalesce(cached.data['counts']['failed'].astext, '0'), Integer)
        elif sort == 'updated':
            order_value = select(func.coalesce(func.max(func.extract('epoch', matching.c.updated_at)), 0)).where(
                matching.c.cell_id == FactorialCell.id).scalar_subquery()
        elif sort.startswith('factor:'):
            factor = FactorialCell.factor_values[sort[7:]]
            # JSONB retains numeric ordering for numeric levels and type identity.
            order_value = func.coalesce(factor, cast(literal('null'), JSONB))
        else:
            order_value = natural_label(FactorialCell.cell_label)
        if position is not None:
            if len(position) != 2:
                raise ValueError("invalid_cursor")
            bound_value = cast(literal(json.dumps(position[0])), JSONB) if sort.startswith('factor:') else position[0]
            key = tuple_(order_value, FactorialCell.id)
            bound = (bound_value, uuid.UUID(position[1]))
            statement = statement.where(key > bound if sort_direction == 'asc' else key < bound)
        statement = statement.add_columns(order_value.label('order_value'))
        rows = list((await db.execute(statement.order_by(
            order_value.asc() if sort_direction == 'asc' else order_value.desc(),
            FactorialCell.id.asc() if sort_direction == 'asc' else FactorialCell.id.desc()
        ).limit(page_size(limit) + 1))).mappings())
    matching_counts = {}
    if rows and (status or job_id):
        counts = (await db.execute(select(
            matching.c.cell_id, func.count().label('matching_count'),
            func.count(func.distinct(matching.c.replicate_id)).label('matching_replicate_count'),
        ).where(matching.c.cell_id.in_([row['id'] for row in rows[:limit]]),
                *slot_filters(matching, status)).group_by(matching.c.cell_id))).mappings().all()
        matching_counts = {row['cell_id']: dict(row) for row in counts}
    items = [
        {
            "cell_id": str(row["id"]),
            "cell_label": row["cell_label"],
            "factor_values": row["factor_values"] or {},
            **(row["data"] or {}),
            **{key: value for key, value in matching_counts.get(row['id'], {}).items() if key != 'cell_id'},
        }
        for row in rows[:limit]
    ]
    next_position = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_position = (
            [last["missing"], last["order_value"], str(last["id"]), generation] if metric else [
                float(last['order_value']) if sort == 'updated' else last['order_value'], str(last['id'])]
        )
    return {
        "items": items,
        "next_cursor": encode_cursor(scope, "cells", next_position, filters) if next_position else None,
    }


async def read_factor_options(db: AsyncSession, scope: ResultScope) -> dict[str, Any]:
    pairs = func.jsonb_each(FactorialCell.factor_values).table_valued('key', 'value').lateral()
    rows = (await db.execute(select(pairs.c.key, pairs.c.value).select_from(FactorialCell).join(pairs, true()).where(
        FactorialCell.experiment_id == scope.experiment_id,
        FactorialCell.design_revision_id == scope.design_revision_id, *cell_predicates(scope)
    ).distinct().order_by(pairs.c.key, pairs.c.value).limit(1001))).all()
    options: dict[str, list[Any]] = {}
    for key, value in rows[:1000]:
        options.setdefault(key, []).append(value)
    return {'factors': options, 'truncated': len(rows) > 1000}


async def read_execution_replicates(
    db: AsyncSession, scope: ResultScope, *, cell_id: uuid.UUID, cursor: str | None = None,
    limit: int = PAGE_SIZE, status: str | None = None, sort: str = 'number', sort_direction: str = 'asc',
    job_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Page row-mode parents without expanding the dataset Cartesian product."""
    if sort not in {'number', 'status', 'updated'} or sort_direction not in {'asc', 'desc'}:
        raise ValueError('invalid_sort')
    filters = {'cell_id': str(cell_id), 'status': status, 'sort': sort,
               'sort_direction': sort_direction, 'job_id': str(job_id) if job_id else None}
    position = decode_cursor(cursor, scope, 'replicates', filters)
    slots = (preview_slots(scope, job_id) if job_id else slot_query(scope)).subquery('replicate_slots')
    aggregate = select(
        slots.c.replicate_id, func.count().label('total'),
        *[func.count().filter(slots.c.status == value).label(value) for value in
          ('not_started', 'pending', 'running', 'finalizing', 'completed', 'failed', 'cancelled', 'limit_reached')],
        func.max(slots.c.updated_at).label('updated_at'),
        func.count().filter(and_(true(), *slot_filters(slots, status))).label('matching'),
        func.count().filter(and_(*slot_filters(slots, 'scoring_failed'))).label('scoring_failed'),
        func.count().filter(and_(*slot_filters(slots, 'limit_reached'))).label('limited'),
    ).where(slots.c.cell_id == cell_id).group_by(slots.c.replicate_id).subquery()
    parent = FactorialReplicateResult
    refresh = await db.get(ResultRefresh, scope.key)
    expected = (refresh.summary or {}).get('source_row_count', 0) if refresh else 0
    total = (func.coalesce(aggregate.c.total, 0) if job_id else
             func.greatest(func.coalesce(aggregate.c.total, 0), expected or 0))
    matching = (total - func.coalesce(aggregate.c.total, 0) + func.coalesce(aggregate.c.not_started, 0)
                if status == 'not_started' else func.coalesce(aggregate.c.matching, 0))
    statement = select(parent.id, parent.replicate_number, parent.replicate_label, total.label('total'),
                       matching.label('matching'), aggregate).select_from(parent).join(FactorialCell).outerjoin(
        aggregate, aggregate.c.replicate_id == parent.id).where(
        FactorialCell.id == cell_id, FactorialCell.experiment_id == scope.experiment_id,
        FactorialCell.design_revision_id == scope.design_revision_id,
        *cell_predicates(scope), *parent_predicates(scope))
    if status or job_id:
        statement = statement.where(matching > 0)
    status_order = case((aggregate.c.failed > 0, 0), (aggregate.c.limited > 0, 1),
                        (aggregate.c.scoring_failed > 0, 2), (aggregate.c.cancelled > 0, 3),
                        (aggregate.c.running > 0, 4), (aggregate.c.finalizing > 0, 5),
                        (aggregate.c.pending > 0, 6), (total > func.coalesce(aggregate.c.total, 0), 7),
                        (aggregate.c.not_started > 0, 7), else_=8)
    order = (func.coalesce(func.extract('epoch', aggregate.c.updated_at), 0) if sort == 'updated' else
             status_order if sort == 'status' else parent.replicate_number)
    if position:
        key, bound = tuple_(order, parent.id), (position[0], uuid.UUID(position[1]))
        statement = statement.where(key > bound if sort_direction == 'asc' else key < bound)
    rows = list((await db.execute(statement.add_columns(order.label('order_value')).order_by(
        order.asc() if sort_direction == 'asc' else order.desc(),
        parent.id.asc() if sort_direction == 'asc' else parent.id.desc()).limit(page_size(limit) + 1))).mappings())
    items = [{'cell_id': str(cell_id), 'replicate_id': str(row['id']), 'replicate_number': row['replicate_number'],
              'replicate_label': row['replicate_label'], 'total': row['total'], 'matching': row['matching'],
              'counts': {key: row[key] or 0 for key in
                         ('pending', 'running', 'finalizing', 'completed', 'failed', 'cancelled', 'limit_reached')}}
             for row in rows[:limit]]
    last = rows[limit - 1] if len(rows) > limit else None
    next_position = ([float(last['order_value']) if sort == 'updated' else last['order_value'], str(last['id'])]
                     if last else None)
    return {'items': items, 'next_cursor': encode_cursor(scope, 'replicates', next_position, filters)
            if next_position else None}


async def read_attempts(
    db: AsyncSession,
    scope: ResultScope,
    *,
    slot_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = PAGE_SIZE,
) -> dict[str, Any]:
    filters = {"slot_id": str(slot_id)}
    position = decode_cursor(cursor, scope, "attempts", filters)
    slots = slot_query(scope).subquery("slots")
    slot = (await db.execute(select(slots).where(slots.c.slot_id == slot_id))).mappings().one_or_none()
    if slot is None:
        raise RunResultsProjectionError("slot_not_found")
    run = ProtocolRun
    match = (
        run.row_result_id == slot_id
        if scope.unit == "row"
        else and_(
            run.row_result_id.is_(None),
            or_(
                run.replicate_result_id == slot_id,
                and_(
                    run.replicate_result_id.is_(None),
                    legacy_flag(scope),
                    run.replicate_label == slot["replicate_label"],
                    run.factor_values == slot["factor_values"],
                ),
            ),
        )
    )
    query = select(
        run.id,
        run.status,
        run.created_at,
        run.completed_at,
        func.left(run.error, 512).label("error"),
        run.result_summary,
        run.usage_summary,
    ).where(
        run.protocol_id == scope.protocol_id,
        run.protocol_revision_id == scope.protocol_revision_id,
        run.design_revision_id == scope.design_revision_id,
        run.is_test_run.is_(False),
        run.target_node_id.is_(None),
        match,
    )
    if position is not None:
        from datetime import datetime

        if len(position) != 2:
            raise ValueError("invalid_cursor")
        time, identifier = datetime.fromisoformat(position[0]), uuid.UUID(position[1])
        query = query.where(or_(run.created_at < time, and_(run.created_at == time, run.id < identifier)))
    rows = (
        (await db.execute(query.order_by(run.created_at.desc(), run.id.desc()).limit(page_size(limit) + 1)))
        .mappings()
        .all()
    )
    legacy = None
    if slot["run_id"] is None and slot["status"] == "completed" and scope.unit == "replicate":
        record = (
            (
                await db.execute(
                    select(FactorialReplicateResult.metric_values, FactorialReplicateResult.artifacts)
                    .join(FactorialCell)
                    .where(
                        FactorialReplicateResult.id == slot_id,
                        FactorialCell.experiment_id == scope.experiment_id,
                        FactorialCell.design_revision_id == scope.design_revision_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        legacy = dict(record) if record else None
    return {
        "slot": execution_payload(dict(slot)),
        "legacy_result": legacy,
        "items": [
            {
                "run_id": str(row["id"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
                "error": row["error"],
                "current": row["id"] == slot["run_id"],
                "result": row["result_summary"],
                "usage": row["usage_summary"],
            }
            for row in rows[:limit]
        ],
        "next_cursor": encode_cursor(
            scope, "attempts", [rows[limit - 1]["created_at"].isoformat(), str(rows[limit - 1]["id"])], filters
        )
        if len(rows) > limit
        else None,
    }


async def lookup_slot(db: AsyncSession, scope: ResultScope, replicate_label: str) -> dict[str, Any]:
    slots = slot_query(scope).subquery("slots")
    rows = (await db.execute(select(slots).where(slots.c.replicate_label == replicate_label).limit(2))).mappings().all()
    if not rows:
        raise RunResultsProjectionError("slot_not_found")
    if len(rows) != 1:
        raise ValueError("ambiguous_replicate_label_use_slot_id")
    return execution_payload(dict(rows[0]))


async def read_cell(
    db: AsyncSession, scope: ResultScope, cell_id: uuid.UUID | None, cell_label: str | None
) -> dict[str, Any]:
    query = (
        select(FactorialCell.id, FactorialCell.cell_label, FactorialCell.factor_values, ResultCellRollup.data)
        .outerjoin(
            ResultCellRollup,
            and_(ResultCellRollup.cell_id == FactorialCell.id, ResultCellRollup.scope_key == scope.key),
        )
        .where(
            FactorialCell.experiment_id == scope.experiment_id,
            FactorialCell.design_revision_id == scope.design_revision_id,
            *cell_predicates(scope),
        )
    )
    query = query.where(FactorialCell.id == cell_id) if cell_id else query.where(FactorialCell.cell_label == cell_label)
    row = (await db.execute(query)).mappings().one_or_none()
    if row is None:
        raise RunResultsProjectionError("cell_not_found")
    return {
        "cell_id": str(row["id"]),
        "cell_label": row["cell_label"],
        "factor_values": row["factor_values"] or {},
        **(row["data"] or {}),
    }


async def read_attempt_detail(db: AsyncSession, scope: ResultScope, run_id: uuid.UUID) -> dict[str, Any]:
    # This is the only interactive operation that selects evidence for a run.
    # Ownership and publication/design scope are checked before it can be returned.
    query = select(
        ProtocolRun.id,
        ProtocolRun.status,
        func.left(ProtocolRun.error, 2048).label("error"),
        ProtocolRun.started_at,
        ProtocolRun.completed_at,
        ProtocolRun.protocol_revision_id,
        ProtocolRun.dataset_row,
        ProtocolRun.usage_summary,
        func.asaree_attempt_preview(ProtocolRun.attempt_result).label("attempt_result"),
        func.asaree_node_preview(ProtocolRun.node_runs).label("node_runs"),
    ).where(
        ProtocolRun.id == run_id,
        ProtocolRun.protocol_id == scope.protocol_id,
        ProtocolRun.protocol_revision_id == scope.protocol_revision_id,
        or_(
            ProtocolRun.design_revision_id == scope.design_revision_id,
            and_(legacy_flag(scope), ProtocolRun.design_revision_id.is_(None)),
        ),
        ProtocolRun.is_test_run.is_(False),
        ProtocolRun.target_node_id.is_(None),
    )
    run = (await db.execute(query)).mappings().one_or_none()
    if run is None:
        raise RunResultsProjectionError("attempt_not_found")
    return {
        **dict(run),
        "run_id": str(run["id"]),
        "node_labels": _node_labels(scope.graph),
        "usage": run["usage_summary"],
        "protocol_revision_id": str(run["protocol_revision_id"]) if run["protocol_revision_id"] else None,
    }


async def read_evidence(
    db: AsyncSession, scope: ResultScope, run_id: uuid.UUID, *, document: str, offset: int = 0, limit: int = 16384
) -> dict[str, Any]:
    if (
        document not in {"attempt_result", "node_runs", "conversation", "dataset_row"}
        or offset < 0
        or not 1 <= limit <= 16384
    ):
        raise ValueError("invalid_evidence_selection")
    value = cast(getattr(ProtocolRun, document), Text)
    row = (
        (
            await db.execute(
                select(func.substr(value, offset + 1, limit).label("text"), func.length(value).label("length")).where(
                    ProtocolRun.id == run_id,
                    ProtocolRun.protocol_id == scope.protocol_id,
                    ProtocolRun.protocol_revision_id == scope.protocol_revision_id,
                    or_(
                        ProtocolRun.design_revision_id == scope.design_revision_id,
                        and_(legacy_flag(scope), ProtocolRun.design_revision_id.is_(None)),
                    ),
                    ProtocolRun.is_test_run.is_(False),
                    ProtocolRun.target_node_id.is_(None),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RunResultsProjectionError("attempt_not_found")
    return {
        "text": row["text"] or "null",
        "next_offset": offset + limit if (row["length"] or 0) > offset + limit else None,
    }


async def read_conversation(
    db: AsyncSession,
    scope: ResultScope,
    run_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int = PAGE_SIZE,
) -> dict[str, Any]:
    if offset < 0:
        raise ValueError("invalid_offset")
    page_size(limit)
    # Slice JSON in PostgreSQL. A long transcript never enters API memory whole.
    messages = ProtocolRun.conversation["messages"]
    elements = (
        func.jsonb_array_elements(
            case((func.jsonb_typeof(messages) == "array", messages), else_=cast(literal("[]"), JSONB))
        )
        .table_valued("value", with_ordinality="ordinal")
        .render_derived()
    )
    items = (
        select(elements.c.value)
        .where(elements.c.ordinal > offset)
        .order_by(elements.c.ordinal)
        .limit(limit + 1)
        .lateral()
    )
    query = (
        select(
            case(
                (
                    func.length(cast(items.c.value, Text)) > 4096,
                    func.jsonb_build_object("preview", func.left(cast(items.c.value, Text), 4096), "truncated", True),
                ),
                else_=cast(items.c.value, JSONB),
            )
        )
        .select_from(ProtocolRun)
        .join(items, true())
        .where(
            ProtocolRun.id == run_id,
            ProtocolRun.protocol_id == scope.protocol_id,
            ProtocolRun.protocol_revision_id == scope.protocol_revision_id,
            ProtocolRun.design_revision_id == scope.design_revision_id,
            ProtocolRun.is_test_run.is_(False),
            ProtocolRun.target_node_id.is_(None),
        )
    )
    rows = list((await db.execute(query)).scalars().all())
    return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}
