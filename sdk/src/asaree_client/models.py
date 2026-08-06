"""Response models — one per resource, matching exactly what ASAREE's API returns.

``model_config`` can't be a field name on a pydantic ``BaseModel`` (it's the
reserved settings attribute), so ``Agent`` follows the same workaround
``agentic_core.schemas.agent.AgentResponse`` uses on the server side: the
field is named ``model_config_data`` and populated from the wire key
``model_config`` via an alias.
"""

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
    output_contract: dict[str, Any] | None = None
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
    # Derived from `output` server-side: the human-readable text (unwrapped
    # from the output-contract envelope, if any) and the structured payload
    # the envelope carries when the agent declares an output_contract.
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


class Experiment(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    design_type: str
    task_brief: dict[str, Any] | None
    design_spec: dict[str, Any] | None
    created_at: datetime


class Cell(BaseModel):
    id: uuid.UUID
    cell_label: str
    run_id: uuid.UUID | None
    workspace_id: str | None
    factor_values: dict[str, Any] | None
    metric_values: dict[str, Any] | None
    artifacts: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class RegisteredDataset(BaseModel):
    id: uuid.UUID
    name: str
    train_path: str
    test_path: str
    train_sha256: str
    test_sha256: str
    target_column: str | None
    dictionary_json: str | None = None


class WorkspaceEvent(BaseModel):
    id: uuid.UUID
    workspace_id: str
    stage: str
    event_type: str
    sha256_train: str | None
    sha256_test: str | None
    created_at: datetime


class MCPServer(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    command: str | None
    url: str | None
    status: str
    error_message: str | None
    capabilities: dict[str, Any] | None
    created_at: datetime


class ToolCallResult(BaseModel):
    is_error: bool
    content: str
