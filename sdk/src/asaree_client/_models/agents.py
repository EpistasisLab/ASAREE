from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Agent(BaseModel):
    id: uuid.UUID
    name: str
    goal: str
    description: str
    system_prompt: str
    model_config_data: dict[str, Any] = Field(alias="model_config")
    tool_config_data: dict[str, Any] = Field(alias="tool_config")
    memory_config_data: dict[str, Any] = Field(alias="memory_config")
    pattern_config: dict[str, Any] | None = None
    skill_config: dict[str, Any] | None = None
    output_contract: dict[str, Any] | None = None
    is_system: bool = False
    auto_eval_enabled: bool = True
    auto_eval_model: str | None = None
    source_plan_id: uuid.UUID | None = None
    source_plan_title: str | None = None
    budget_limit_usd: float | None = None
    max_run_duration_seconds: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(populate_by_name=True)


class Run(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    status: str
    input: str
    output: str | None
    output_text: str
    payload: dict[str, Any] | None = None
    error: str | None
    token_usage: dict[str, Any] | None
    cost_estimate: float | None
    run_metadata: dict[str, Any] | None = None
    pattern_overrides: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None


class RunStep(BaseModel):
    id: uuid.UUID
    sequence: int
    iteration: int | None
    phase: str
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    llm_call: dict[str, Any] | None
    tool_call: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None
