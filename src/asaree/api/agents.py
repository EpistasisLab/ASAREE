"""Agents — a thin layer over agentic_core.runner.

Request schema is ASAREE's own, scoped to exactly what
``runner.create_agent``/``update_agent`` accept today: name, goal,
description, system_prompt, model/pattern/tool/memory config,
output_contract, budget_limit_usd, max_run_duration_seconds.
``agentic_core.schemas.agent.AgentCreate`` also advertises ``auto_eval_enabled``
etc., which the runner still doesn't take — reusing it here would silently
drop those. The *response* schema is core's own ``AgentResponse`` — safe to
reuse since it just reflects whatever's on the row, defaults included.
"""

from __future__ import annotations

import uuid
from typing import Any

from agentic_core.engine.patterns.catalog import PatternConfigError
from agentic_core.runner import create_agent, get_agent, get_agent_by_name, list_agents, update_agent
from agentic_core.schemas.agent import AgentResponse, MemoryConfig, ModelConfig
from agentic_core.schemas.pattern import PatternConfig
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from asaree.deps import CurrentUser

router = APIRouter(prefix="/agents", tags=["agents"])


class CreateAgentRequest(BaseModel):
    name: str
    goal: str
    description: str = ""
    system_prompt: str = ""
    model_config_data: ModelConfig = ModelConfig()
    pattern_config: PatternConfig | None = None
    tool_config: dict[str, object] | None = None
    memory_config: MemoryConfig | None = None
    # Given, execute_run runs one extraction pass per completed run coercing
    # its free-text output into these fields (agentic_core.services.output_contract),
    # exposed as the run output envelope's payload.
    output_contract: dict[str, Any] | None = None
    budget_limit_usd: float | None = None
    max_run_duration_seconds: int | None = None


class UpdateAgentRequest(BaseModel):
    """All fields optional; ``None`` means "leave unchanged" — same convention
    ``agentic_core.runner.update_agent`` itself uses."""

    name: str | None = None
    goal: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model_config_data: ModelConfig | None = None
    pattern_config: PatternConfig | None = None
    tool_config: dict[str, object] | None = None
    memory_config: MemoryConfig | None = None
    output_contract: dict[str, Any] | None = None
    budget_limit_usd: float | None = None
    max_run_duration_seconds: int | None = None


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent_endpoint(body: CreateAgentRequest, user: CurrentUser) -> AgentResponse:
    agent = await create_agent(
        name=body.name,
        goal=body.goal,
        description=body.description,
        system_prompt=body.system_prompt,
        model_config=body.model_config_data,
        pattern_config=body.pattern_config.model_dump() if body.pattern_config else None,
        tool_config=body.tool_config,
        memory_config=body.memory_config.model_dump() if body.memory_config else None,
        output_contract=body.output_contract,
        budget_limit_usd=body.budget_limit_usd,
        max_run_duration_seconds=body.max_run_duration_seconds,
        owner_id=user.id,
    )
    return AgentResponse.model_validate(agent)


@router.get("", response_model=list[AgentResponse])
async def list_agents_endpoint(user: CurrentUser) -> list[AgentResponse]:
    agents = await list_agents(owner_id=user.id)
    return [AgentResponse.model_validate(a) for a in agents]


@router.get("/by-name/{name}", response_model=AgentResponse)
async def get_agent_by_name_endpoint(name: str, user: CurrentUser) -> AgentResponse:
    agent = await get_agent_by_name(name)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such agent")
    return AgentResponse.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent_endpoint(agent_id: uuid.UUID, user: CurrentUser) -> AgentResponse:
    agent = await get_agent(agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such agent")
    return AgentResponse.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent_endpoint(agent_id: uuid.UUID, body: UpdateAgentRequest, user: CurrentUser) -> AgentResponse:
    existing = await get_agent(agent_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such agent")
    try:
        agent = await update_agent(
            agent_id,
            name=body.name,
            goal=body.goal,
            description=body.description,
            system_prompt=body.system_prompt,
            model_config=body.model_config_data,
            pattern_config=body.pattern_config.model_dump() if body.pattern_config else None,
            tool_config=body.tool_config,
            memory_config=body.memory_config.model_dump() if body.memory_config else None,
            output_contract=body.output_contract,
            budget_limit_usd=body.budget_limit_usd,
            max_run_duration_seconds=body.max_run_duration_seconds,
        )
    except PatternConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert agent is not None  # existence already checked above
    return AgentResponse.model_validate(agent)
