"""Agents — a thin layer over agentic_core.runner.

Request schema is ASAREE's own, scoped to exactly what
``runner.create_agent`` accepts today; ``agentic_core.schemas.agent.AgentCreate``
advertises fields (budget_limit_usd, auto_eval_enabled, ...) the runner
doesn't take yet, so reusing it here would silently drop them. The *response*
schema is core's own ``AgentResponse`` — safe to reuse since it just reflects
whatever's on the row, defaults included.
"""

from __future__ import annotations

import uuid

from agentic_core.runner import create_agent, get_agent, get_agent_by_name, list_agents
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
