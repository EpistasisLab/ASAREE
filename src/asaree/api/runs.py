"""Runs — a thin layer over motoro.runner, executed by the worker.

``POST /runs`` creates the run and hands it to asaree.worker (arq) via
``enqueue_run`` — it does not execute inline, and returns as soon as the run
is queued (status ``PENDING``), not once it's terminal. The API shape did
not need to change for this: the SDK's ``start`` then ``wait``/poll shape
(``asaree_client.resources.runs``) was already the right one, since ``wait``
now actually polls instead of being a same-request no-op.

Credential resolution needs no wiring here at all: ``owner_id=user.id`` on
``create_run`` is what ``execute_run`` (called from the worker, not here)
uses as ``principal_id`` when nothing overrides it, which is exactly what
the installed resolver (``asaree.services.credential_resolver``) keys its
lookup on.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from motoro.models.run import RunStatus
from motoro.runner import create_run, get_agent, get_run, get_run_steps, list_runs
from motoro.schemas.output import parse_envelope
from pydantic import BaseModel

from asaree.deps import CurrentUser
from asaree.worker.enqueue import enqueue_run

router = APIRouter(prefix="/runs", tags=["runs"])


class CreateRunRequest(BaseModel):
    agent_id: uuid.UUID
    user_input: str
    pattern_overrides: dict[str, Any] | None = None
    # e.g. {"workspace_id": "..."} — the orchestrator lifts workspace_id out of
    # this into every MCP tool call's ambient _meta (Motoro runner.py
    # docstring). Anything else here is just carried, not interpreted.
    metadata: dict[str, Any] | None = None
    # Shallow-merged onto the agent's own model_config_data at execute time —
    # e.g. {"model": "claude-opus-5", "effort": "xhigh"} to vary the model/
    # effort per run (a factorial design's per-cell treatment) without
    # touching the agent's stored configuration.
    model_config_override: dict[str, Any] | None = None


class RunResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    # A plain string, not motoro.models.run.RunStatus — that's a models
    # import, off-limits the same way UserLLMSetting.provider avoids
    # LLMProvider. .value below turns the enum into exactly this before it
    # ever reaches pydantic.
    status: str
    input: str
    output: str | None
    # Derived from `output`: the human-readable text (unwrapped from the
    # output-contract envelope, if any) and the structured payload the
    # envelope carries when the agent declares an output_contract. Both
    # None/output verbatim when the run predates envelopes or produced none.
    output_text: str
    payload: dict[str, Any] | None
    error: str | None
    token_usage: dict[str, Any] | None
    cost_estimate: float | None
    run_metadata: dict[str, Any] | None
    pattern_overrides: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None


def _to_response(run: Any) -> RunResponse:
    envelope = parse_envelope(run.output)
    return RunResponse(
        id=run.id,
        agent_id=run.agent_id,
        status=run.status.value,
        input=run.input,
        output=run.output,
        output_text=envelope.result if envelope is not None else (run.output or ""),
        payload=envelope.payload if envelope is not None else None,
        error=run.error,
        token_usage=run.token_usage,
        cost_estimate=float(run.cost_estimate) if run.cost_estimate is not None else None,
        run_metadata=run.run_metadata,
        pattern_overrides=run.pattern_overrides,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.post("", response_model=RunResponse, status_code=201)
async def create_run_endpoint(body: CreateRunRequest, user: CurrentUser) -> RunResponse:
    agent = await get_agent(body.agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such agent")

    run = await create_run(
        agent_id=body.agent_id,
        user_input=body.user_input,
        pattern_overrides=body.pattern_overrides,
        owner_id=user.id,
        metadata=body.metadata,
        model_config_overrides=body.model_config_override,
    )
    await enqueue_run(run.id)
    return _to_response(run)


@router.get("", response_model=list[RunResponse])
async def list_runs_endpoint(
    user: CurrentUser,
    agent_id: uuid.UUID | None = None,
    status: str | None = None,
    metadata: str | None = None,
    limit: int | None = None,
) -> list[RunResponse]:
    """``metadata``, if given, is a JSON object string (e.g. a use case's own
    experiment_id/treatment_group/replicate) -- every key/value pair must
    match ``run_metadata`` exactly. Filtering happens in list_runs's own SQL,
    not by pulling every run for ``agent_id`` and checking client-side: that
    approach silently truncates once an agent has more total runs than
    ``limit`` (a caller filtering on its own metadata has no way to know a
    match was dropped, since nothing errors)."""
    try:
        metadata_filter = json.loads(metadata) if metadata else None
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"metadata is not valid JSON: {e}") from e
    if metadata_filter is not None and not isinstance(metadata_filter, dict):
        raise HTTPException(status_code=422, detail="metadata must be a JSON object")
    try:
        status_filter = RunStatus(status) if status else None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid status: {status}") from e
    kwargs: dict[str, Any] = {"agent_id": agent_id, "owner_id": user.id, "status": status_filter}
    if metadata_filter is not None:
        kwargs["metadata"] = metadata_filter
    if limit is not None:
        kwargs["limit"] = limit
    runs = await list_runs(**kwargs)
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
            "iteration": s.iteration,
            "phase": s.phase.value,
            "input": s.input,
            "output": s.output,
            "llm_call": s.llm_call,
            "tool_call": s.tool_call,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
        }
        for s in steps
    ]
