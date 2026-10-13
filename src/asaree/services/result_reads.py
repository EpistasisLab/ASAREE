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

from sqlalchemy import and_, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.result_read_model import ResultCellMetric, ResultCellRollup, ResultRefresh
from asaree.services.dataset_row_inputs import resolve_dataset_row_plan
from asaree.services.experiment_run_results import (
    RunResultsProjectionError,
    _declared_metric_aggregations,
    _declared_metric_directions,
    _declared_metric_types,
    _node_labels,
    _primary_metric,
)

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

    @property
    def key(self) -> str:
        return f"{self.protocol_id}:{self.protocol_revision_id or 'legacy'}:{self.design_revision_id or 'none'}"

    @property
    def unit(self) -> str:
        return "row" if self.row_plan is not None else "replicate"


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
    )


def slot_query(scope: ResultScope) -> Any:
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
        match = and_(run.row_result_id == row.id, run.id == row.run_id)
    else:
        base = base.add_columns(
            parent.id.label("slot_id"),
            literal(None).label("row_result_id"),
            literal(None).label("row_index"),
            literal(None).label("dataset_id"),
            literal(None).label("raw_sha256"),
        )
        match = and_(
            run.row_result_id.is_(None),
            or_(
                run.replicate_result_id == parent.id,
                and_(
                    run.replicate_result_id.is_(None),
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
        )
        .where(
            run.protocol_id == scope.protocol_id,
            run.design_revision_id == scope.design_revision_id,
            run.protocol_revision_id == scope.protocol_revision_id,
            match,
            run.is_test_run.is_(False),
            run.target_node_id.is_(None),
        )
        .order_by(run.created_at.desc(), run.id.desc())
        .limit(1)
        .correlate(cell, parent, row)
        .lateral("latest")
    )
    imported = and_(
        literal(not scope.versioned and scope.unit == "replicate"),
        parent.metric_values.is_not(None),
        parent.metric_values != cast(literal("{}"), JSONB),
    )
    status = func.coalesce(latest.c.status, case((imported, "completed"), else_="not_started"))
    return base.outerjoin(latest, literal(True)).add_columns(
        latest.c.run_id,
        status.label("status"),
        latest.c.created_at,
        latest.c.updated_at,
        latest.c.started_at,
        latest.c.completed_at,
        latest.c.error,
        func.coalesce(latest.c.result_summary, cast(literal("{}"), JSONB)).label("result"),
        latest.c.usage_summary.label("usage"),
    )


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
        "metric_types": _declared_metric_types(scope.design_spec),
        "metric_aggregations": _declared_metric_aggregations(scope.design_spec),
        "metric_directions": _declared_metric_directions(scope.design_spec),
        "primary_metric": _primary_metric(scope.design_spec)[0] if scope.unit == "replicate" else None,
        "primary_metric_direction": _primary_metric(scope.design_spec)[1],
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
) -> dict[str, Any]:
    filters = {
        "cell_id": str(cell_id) if cell_id else None,
        "replicate_id": str(replicate_id) if replicate_id else None,
        "status": status,
    }
    position = decode_cursor(cursor, scope, "executions", filters)
    query = slot_query(scope)
    if cell_id is not None:
        query = query.where(FactorialCell.id == cell_id)
    if replicate_id is not None:
        query = query.where(FactorialReplicateResult.id == replicate_id)
    slots = query.subquery("slots")
    statement = select(slots)
    if status is not None:
        statement = statement.where(slots.c.status == status)
    if position is not None:
        if len(position) != 1:
            raise ValueError("invalid_cursor")
        statement = statement.where(slots.c.slot_id > uuid.UUID(position[0]))
    rows = (await db.execute(statement.order_by(slots.c.slot_id).limit(page_size(limit) + 1))).mappings().all()
    items = [execution_payload(dict(row)) for row in rows[:limit]]
    return {
        "items": items,
        "next_cursor": encode_cursor(scope, "executions", [str(rows[limit - 1]["slot_id"])], filters)
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
) -> dict[str, Any]:
    if metric is not None and (scope.unit == "row" or metric not in _declared_metric_types(scope.design_spec)):
        raise ValueError("invalid_sort_metric")
    if direction not in {"minimize", "maximize"}:
        raise ValueError("invalid_sort_direction")
    filters = {"metric": metric, "direction": direction}
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
        )
    )
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
        if position is not None:
            if len(position) != 1:
                raise ValueError("invalid_cursor")
            statement = statement.where(FactorialCell.id > uuid.UUID(position[0]))
        rows = list((await db.execute(statement.order_by(FactorialCell.id).limit(page_size(limit) + 1))).mappings())
    items = [
        {
            "cell_id": str(row["id"]),
            "cell_label": row["cell_label"],
            "factor_values": row["factor_values"] or {},
            **(row["data"] or {}),
        }
        for row in rows[:limit]
    ]
    next_position = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_position = (
            [last["missing"], last["order_value"], str(last["id"]), generation] if metric else [str(last["id"])]
        )
    return {
        "items": items,
        "next_cursor": encode_cursor(scope, "cells", next_position, filters) if next_position else None,
    }


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
    return {
        "slot": execution_payload(dict(slot)),
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
    run = await db.scalar(
        select(ProtocolRun)
        .options(defer(ProtocolRun.conversation))
        .where(
            ProtocolRun.id == run_id,
            ProtocolRun.protocol_id == scope.protocol_id,
            ProtocolRun.protocol_revision_id == scope.protocol_revision_id,
            ProtocolRun.design_revision_id == scope.design_revision_id,
            ProtocolRun.is_test_run.is_(False),
            ProtocolRun.target_node_id.is_(None),
        )
    )
    if run is None:
        raise RunResultsProjectionError("attempt_not_found")
    return {
        "run_id": str(run.id),
        "status": run.status,
        "error": run.error,
        "attempt_result": run.attempt_result,
        "node_runs": run.node_runs,
        "node_labels": _node_labels(scope.graph),
        "usage": run.usage_summary,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "protocol_revision_id": str(run.protocol_revision_id) if run.protocol_revision_id else None,
        "dataset_row": run.dataset_row,
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
    elements = func.jsonb_array_elements(ProtocolRun.conversation["messages"]).table_valued(
        "value", with_ordinality="ordinal"
    )
    items = (
        select(elements.c.value)
        .where(elements.c.ordinal > offset)
        .order_by(elements.c.ordinal)
        .limit(limit + 1)
        .lateral()
    )
    query = (
        select(items.c.value)
        .select_from(ProtocolRun)
        .join(items, literal(True))
        .where(
            ProtocolRun.id == run_id,
            ProtocolRun.protocol_id == scope.protocol_id,
            ProtocolRun.protocol_revision_id == scope.protocol_revision_id,
            ProtocolRun.design_revision_id == scope.design_revision_id,
        )
    )
    rows = list((await db.execute(query)).scalars().all())
    return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}
