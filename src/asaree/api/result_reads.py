"""Version-scoped, bounded operational result reads."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from asaree.api.experiments import _get_owned_experiment
from asaree.deps import CurrentUser, DbSession
from asaree.services import result_reads
from asaree.services.experiment_run_results import RunResultsProjectionError

router = APIRouter(prefix="/experiments/{experiment_id}/execution-results", tags=["execution-results"])
Limit = Annotated[int, Query(ge=1, le=result_reads.MAX_PAGE_SIZE)]


async def selected_scope(
    experiment_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    protocol_id: uuid.UUID | None = None,
    protocol_revision_id: uuid.UUID | None = None,
    design_revision_id: uuid.UUID | None = None,
) -> result_reads.ResultScope:
    await _get_owned_experiment(db, experiment_id, user)
    # Bound database work and release connections before an upstream gateway deadline.
    await db.execute(text("SET LOCAL statement_timeout = '5s'"))
    try:
        return await result_reads.resolve_result_scope(
            db,
            experiment_id=experiment_id,
            protocol_id=protocol_id,
            protocol_revision_id=protocol_revision_id,
            design_revision_id=design_revision_id,
        )
    except RunResultsProjectionError as exc:
        raise HTTPException(status_code=422 if str(exc) == "ambiguous_protocol" else 404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


Scope = Annotated[result_reads.ResultScope, Depends(selected_scope)]


def factor_selection(factors: str | None) -> dict[str, Any]:
    try:
        value = json.loads(factors or '{}')
        if not isinstance(value, dict) or len(value) > 50:
            raise ValueError
        return value
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail='Invalid factor filters.') from exc


async def _read(operation: Callable[..., Awaitable[dict[str, Any]]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return await operation(*args, **kwargs)
    except RunResultsProjectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (DBAPIError, DatabaseTimeout) as exc:
        raise HTTPException(status_code=503, detail="Results temporarily unavailable; retry shortly.") from exc


@router.get("/summary", response_model=None)
async def summary(
    db: DbSession,
    scope: Scope,
    response: Response,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Any:
    result = await _read(result_reads.read_summary, db, scope)
    etag = f'"{scope.key}:{result["change_id"]}:{result["generation"]}"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"})
    return result


@router.get("/cells")
async def cells(
    db: DbSession,
    scope: Scope,
    limit: Limit = result_reads.PAGE_SIZE,
    cursor: str | None = None,
    search: Annotated[str, Query(max_length=200)] = '',
    status: Annotated[str | None, Query(pattern=(
        "^(not_started|pending|running|finalizing|completed|failed|cancelled|limit_reached|scoring_failed)$"
    ))] = None,
    factors: Annotated[str | None, Query(max_length=10000)] = None,
    result_status: Annotated[str | None, Query(pattern="^(ready|needs_attention|awaiting_results)$")] = None,
) -> dict[str, Any]:
    return await _read(
        result_reads.read_cells, db, scope, limit=limit, cursor=cursor,
        search=search, status=status, result_status=result_status, factors=factor_selection(factors),
    )


@router.get("/executions")
async def executions(
    db: DbSession,
    scope: Scope,
    limit: Limit = result_reads.PAGE_SIZE,
    cursor: str | None = None,
    cell_id: uuid.UUID | None = None,
    replicate_id: uuid.UUID | None = None,
    status: Annotated[
        str | None,
        Query(pattern=(
            "^(not_started|pending|running|finalizing|completed|failed|cancelled|limit_reached|scoring_failed)$"
        )),
    ] = None,
    search: Annotated[str, Query(max_length=200)] = '',
    factors: Annotated[str | None, Query(max_length=10000)] = None,
) -> dict[str, Any]:
    return await _read(
        result_reads.read_executions,
        db,
        scope,
        limit=limit,
        cursor=cursor,
        cell_id=cell_id,
        replicate_id=replicate_id,
        status=status,
        search=search, factors=factor_selection(factors),
    )


@router.get('/factor-options')
async def factor_options(db: DbSession, scope: Scope) -> dict[str, Any]:
    return await _read(result_reads.read_factor_options, db, scope)


@router.get("/slots/{slot_id}/neighbors")
async def neighbors(slot_id: uuid.UUID, db: DbSession, scope: Scope) -> dict[str, Any]:
    return await _read(result_reads.read_execution_neighbors, db, scope, slot_id=slot_id)


@router.get("/slots/{slot_id}/attempts")
async def attempts(
    slot_id: uuid.UUID,
    db: DbSession,
    scope: Scope,
    limit: Limit = result_reads.PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, Any]:
    return await _read(result_reads.read_attempts, db, scope, slot_id=slot_id, limit=limit, cursor=cursor)


@router.get("/lookup")
async def lookup(db: DbSession, scope: Scope, replicate_label: str) -> dict[str, Any]:
    return await _read(result_reads.lookup_slot, db, scope, replicate_label)


@router.get("/cell")
async def cell(
    db: DbSession, scope: Scope, cell_id: uuid.UUID | None = None, cell_label: str | None = None
) -> dict[str, Any]:
    if cell_id is None and cell_label is None:
        raise HTTPException(status_code=422, detail="cell_selection_required")
    return await _read(result_reads.read_cell, db, scope, cell_id, cell_label)


@router.get("/attempts/{run_id}")
async def attempt(run_id: uuid.UUID, db: DbSession, scope: Scope) -> dict[str, Any]:
    return await _read(result_reads.read_attempt_detail, db, scope, run_id)


@router.get("/attempts/{run_id}/conversation")
async def conversation(
    run_id: uuid.UUID,
    db: DbSession,
    scope: Scope,
    limit: Limit = result_reads.PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    return await _read(result_reads.read_conversation, db, scope, run_id, limit=limit, offset=offset)


@router.get("/attempts/{run_id}/evidence")
async def evidence(
    run_id: uuid.UUID,
    db: DbSession,
    scope: Scope,
    document: Annotated[str, Query(pattern="^(attempt_result|node_runs|conversation|dataset_row)$")],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=16384)] = 16384,
) -> dict[str, Any]:
    return await _read(result_reads.read_evidence, db, scope, run_id, document=document, offset=offset, limit=limit)
