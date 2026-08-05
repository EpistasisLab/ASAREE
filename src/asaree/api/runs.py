"""Runs — a thin layer over agentic_core.runner, executed inline.

``POST /runs`` both creates and executes in the same request — no worker for
v1 (design doc §5). This still matches the SDK-facing shape a driver script
expects (``start`` then ``wait``/poll): by the time ``start`` returns, the run
is already terminal, so a subsequent poll just sees that immediately. Nothing
about the API shape has to change if a worker gets added later; only what
happens between create and execute does.

Credential resolution needs no wiring here at all: ``owner_id=user.id`` on
``create_run`` is what ``execute_run`` uses as ``principal_id`` when nothing
overrides it, which is exactly what the installed resolver
(``asaree.services.credential_resolver``) keys its lookup on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from agentic_core.runner import create_run, execute_run, get_agent, get_run, get_run_steps, list_runs
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from asaree.deps import CurrentUser

router = APIRouter(prefix="/runs", tags=["runs"])


class CreateRunRequest(BaseModel):
    agent_id: uuid.UUID
    user_input: str
    pattern_overrides: dict[str, Any] | None = None


class RunResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    # A plain string, not agentic_core.models.run.RunStatus — that's a models
    # import, off-limits the same way UserLLMSetting.provider avoids
    # LLMProvider. .value below turns the enum into exactly this before it
    # ever reaches pydantic.
    status: str
    input: str
    output: str | None
    error: str | None
    token_usage: dict[str, Any] | None
    cost_estimate: float | None
    created_at: datetime
    completed_at: datetime | None


def _to_response(run: Any) -> RunResponse:
    return RunResponse(
        id=run.id,
        agent_id=run.agent_id,
        status=run.status.value,
        input=run.input,
        output=run.output,
        error=run.error,
        token_usage=run.token_usage,
        cost_estimate=float(run.cost_estimate) if run.cost_estimate is not None else None,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.post("", response_model=RunResponse, status_code=201)
async def create_and_execute_run_endpoint(body: CreateRunRequest, user: CurrentUser) -> RunResponse:
    agent = await get_agent(body.agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such agent")

    run = await create_run(
        agent_id=body.agent_id,
        user_input=body.user_input,
        pattern_overrides=body.pattern_overrides,
        owner_id=user.id,
    )
    await execute_run(run_id=run.id)
    finished = await get_run(run.id)
    assert finished is not None  # just created and executed above
    return _to_response(finished)


@router.get("", response_model=list[RunResponse])
async def list_runs_endpoint(user: CurrentUser, agent_id: uuid.UUID | None = None) -> list[RunResponse]:
    runs = await list_runs(agent_id=agent_id, owner_id=user.id)
    return [_to_response(r) for r in runs]


@router.get("/{run_id}", response_model=RunResponse)
async def get_run_endpoint(run_id: uuid.UUID, user: CurrentUser) -> RunResponse:
    run = await get_run(run_id)
    if run is None or run.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such run")
    return _to_response(run)


@router.get("/{run_id}/steps")
async def get_run_steps_endpoint(run_id: uuid.UUID, user: CurrentUser) -> list[dict[str, Any]]:
    run = await get_run(run_id)
    if run is None or run.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such run")
    steps = await get_run_steps(run_id)
    return [
        {
            "id": str(s.id),
            "sequence": s.sequence,
            "phase": s.phase.value,
            "input": s.input,
            "output": s.output,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
        }
        for s in steps
    ]
