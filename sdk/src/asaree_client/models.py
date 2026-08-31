"""Response models — one per resource, matching exactly what ASAREE's API returns.

``model_config`` can't be a field name on a pydantic ``BaseModel`` (it's the
reserved settings attribute), so ``Agent`` follows the same workaround
``motoro.schemas.agent.AgentResponse`` uses on the server side: the
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
    hypothesis: str | None = None
    design_type: str
    task_brief: dict[str, Any] | None
    design_spec: dict[str, Any] | None
    # Every dataset attached to this experiment, in canvas wiring order --
    # an experiment can run against several since the Dataset connector was
    # uncapped. ``dataset_id`` is a read-only view of the first one, kept so
    # code written before that keeps working; it is no longer a stored column.
    dataset_ids: list[uuid.UUID] = []
    dataset_id: uuid.UUID | None = None
    archived_at: datetime | None = None
    created_at: datetime


class Cell(BaseModel):
    id: uuid.UUID
    cell_label: str
    # Which generation of the experiment's design this observation was made
    # under. Cells from a superseded design stay in the database as history,
    # so a cell is only part of the live design if this matches the
    # experiment's current DesignRevision.
    design_revision_id: uuid.UUID
    run_id: uuid.UUID | None
    workspace_id: str | None
    factor_values: dict[str, Any] | None
    metric_values: dict[str, Any] | None
    artifacts: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class DesignRevision(BaseModel):
    """One generation of an experiment's factorial design.

    Regenerating a design that no longer produces the same set of cells
    supersedes the current revision and opens a new one; the old cells (and
    whatever was scored in them) are kept as history rather than deleted.
    ``superseded_at is None`` marks the one revision that is current.
    """

    id: uuid.UUID
    revision: int
    superseded_at: datetime | None
    design_spec: dict[str, Any] | None
    cell_count: int
    scored_count: int
    created_at: datetime


class ExperimentArtifact(BaseModel):
    """A durable, experiment-level (not per-cell) record -- an ``analyze``
    snapshot, a CSV export, or anything else a use case wants to keep past
    one run. ``content`` is opaque to ASAREE -- a CSV export and a
    JSON-encoded analyze result serialize completely differently, and
    neither is interpreted server-side."""

    id: uuid.UUID
    experiment_id: uuid.UUID
    name: str
    kind: str
    content: str
    created_at: datetime
    updated_at: datetime


class Protocol(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    experiment_id: uuid.UUID | None
    graph: dict[str, Any]
    published_revision_id: uuid.UUID | None = None
    published_revision: int | None = None
    has_unpublished_changes: bool = False
    created_at: datetime
    updated_at: datetime


class ProtocolRun(BaseModel):
    id: uuid.UUID
    protocol_id: uuid.UUID
    status: str
    node_runs: dict[str, Any]
    error: str | None
    cell_label: str | None = None
    factor_values: dict[str, Any] | None = None
    design_revision_id: uuid.UUID | None = None
    protocol_revision_id: uuid.UUID | None = None
    target_node_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ProtocolRevision(BaseModel):
    id: uuid.UUID
    protocol_id: uuid.UUID
    revision: int
    graph: dict[str, Any]
    published_at: datetime


class CellRunBatch(BaseModel):
    """"Run all cells" fanout result -- one ProtocolRun id per not-yet-scored
    cell; ``skipped`` is how many cells already had metric_values and were
    left alone (resume semantics)."""

    protocol_run_ids: list[uuid.UUID]
    cell_labels: list[str]
    skipped: int
    protocol_revision_id: uuid.UUID | None = None
    protocol_revision: int | None = None


class RegisteredDataset(BaseModel):
    id: uuid.UUID
    name: str
    raw_path: str | None = None
    raw_sha256: str | None = None
    # Null until a split is actually produced (Datasets.quick_split/
    # register_manual_split) -- registration itself only stores the raw
    # file, it never splits (see RegisteredDataset's own comment in the
    # backend model).
    train_path: str | None = None
    test_path: str | None = None
    train_sha256: str | None = None
    test_sha256: str | None = None
    target_column: str | None
    description: str | None = None
    dictionary_json: str | None = None
    created_at: datetime | None = None


class LLMSetting(BaseModel):
    provider: str
    api_base: str | None


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
