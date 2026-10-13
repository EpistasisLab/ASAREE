"""Selected node evidence: owner verification precedes access to core storage."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from asaree.api.result_reads import Scope
from asaree.deps import CurrentUser, DbSession
from asaree.models.protocol_run import ProtocolRun
from asaree.services import motoro_telemetry

router = APIRouter(
    prefix="/experiments/{experiment_id}/execution-results/attempts/{run_id}/nodes/{node_id}",
    tags=["execution-results"],
)
Offset = Annotated[int, Query(ge=0)]
Length = Annotated[int, Query(ge=1, le=16384)]


async def verified_node(
    db: DbSession, scope: Scope, user: CurrentUser, run_id: uuid.UUID, node_id: str
) -> uuid.UUID | None:
    pointer = (
        await db.execute(
            select(
                ProtocolRun.node_runs[node_id]["run_id"].astext, func.jsonb_typeof(ProtocolRun.node_runs[node_id])
            ).where(
                ProtocolRun.id == run_id,
                ProtocolRun.owner_id == user.id,
                ProtocolRun.protocol_id == scope.protocol_id,
                ProtocolRun.protocol_revision_id == scope.protocol_revision_id,
                ProtocolRun.design_revision_id == scope.design_revision_id,
                ProtocolRun.is_test_run.is_(False),
                ProtocolRun.target_node_id.is_(None),
            )
        )
    ).one_or_none()
    if pointer is None or pointer[1] != "object":
        raise HTTPException(status_code=404, detail="node_not_found")
    return uuid.UUID(pointer[0]) if pointer[0] else None


@router.get("/text")
async def text_page(
    run_id: uuid.UUID,
    node_id: str,
    db: DbSession,
    user: CurrentUser,
    scope: Scope,
    field: Annotated[str, Query(pattern="^(prompt|output)$")],
    offset: Offset = 0,
    limit: Length = 16384,
) -> dict[str, Any]:
    pointer = await verified_node(db, scope, user, run_id, node_id)
    if field == "prompt":
        if pointer is None:
            raise HTTPException(status_code=404, detail="prompt_unavailable")
        return await motoro_telemetry.read_prompt(user.id, pointer, offset, limit)
    output = ProtocolRun.node_runs[node_id]["output_text"].astext
    row = (
        (
            await db.execute(
                select(func.substr(output, offset + 1, limit).label("text"), func.length(output).label("length")).where(
                    ProtocolRun.id == run_id
                )
            )
        )
        .mappings()
        .one()
    )
    return {"text": row["text"] or "", "next_offset": offset + limit if (row["length"] or 0) > offset + limit else None}


@router.get("/trace")
async def trace(
    run_id: uuid.UUID,
    node_id: str,
    db: DbSession,
    user: CurrentUser,
    scope: Scope,
    after: Annotated[int, Query(ge=-1)] = -1,
) -> dict[str, Any]:
    pointer = await verified_node(db, scope, user, run_id, node_id)
    if pointer is None:
        return {"items": [], "next_after": None}
    return await motoro_telemetry.read_trace(user.id, pointer, after)


@router.get("/trace/{step_id}")
async def step(
    run_id: uuid.UUID,
    node_id: str,
    step_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    scope: Scope,
    offset: Offset = 0,
    limit: Length = 16384,
) -> dict[str, Any]:
    pointer = await verified_node(db, scope, user, run_id, node_id)
    if pointer is None:
        raise HTTPException(status_code=404, detail="step_unavailable")
    return await motoro_telemetry.read_step(user.id, pointer, step_id, offset, limit)
