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
    measurement_plan: dict[str, Any] | None = None
    # Every dataset attached to this experiment, in canvas wiring order --
    # an experiment can run against several since the Dataset connector was
    # uncapped. ``dataset_id`` is a read-only view of the first one, kept so
    # code written before that keeps working; it is no longer a stored column.
    dataset_ids: list[uuid.UUID] = Field(default_factory=list)
    dataset_id: uuid.UUID | None = None
    archived_at: datetime | None = None
    metric_recommendations: dict[str, Any] | None = None
    metric_recommendation_set_version: int = 1
    latest_test_run_id: uuid.UUID | None = None
    locked_at: datetime | None = None
    locked_protocol_revision_id: uuid.UUID | None = None
    locked_design_spec: dict[str, Any] | None = None
    locked_measurement_plan: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class Replicate(BaseModel):
    id: uuid.UUID
    cell_id: uuid.UUID
    cell_label: str
    replicate_label: str
    replicate_number: int
    # Which generation of the experiment's design this observation was made
    # under. Replicates from a superseded design stay in the database as history,
    # so a replicate is only part of the live design if this matches the
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
    replicate_count: int
    scored_replicate_count: int
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
    replicate_label: str | None = None
    replicate_result_id: uuid.UUID | None = None
    factor_values: dict[str, Any] | None = None
    design_revision_id: uuid.UUID | None = None
    protocol_revision_id: uuid.UUID | None = None
    target_node_id: str | None = None
    conversation: dict[str, Any] | None = None
    cancel_requested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    observations: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ProtocolRevision(BaseModel):
    id: uuid.UUID
    protocol_id: uuid.UUID
    revision: int
    graph: dict[str, Any]
    published_at: datetime


class CellRunBatch(BaseModel):
    """ "Run all cells" fanout result -- one ProtocolRun id per not-yet-completed
    replicate; ``skipped`` is how many replicates already had metrics or a completed
    run and were left alone (resume semantics)."""

    protocol_run_ids: list[uuid.UUID]
    replicate_labels: list[str]
    skipped: int
    protocol_revision_id: uuid.UUID
    protocol_revision: int


class PromptPreview(BaseModel):
    text: str


class ResourceUsage(BaseModel):
    duration_seconds: float | None
    cost_usd: float | None


class TestRunResources(BaseModel):
    task: ResourceUsage
    evaluation: ResourceUsage
    total: ResourceUsage


class TestRunFreshness(BaseModel):
    out_of_date: bool
    reasons: list[str]


class TestRunExecutionSummary(BaseModel):
    node_runs: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    cancel_requested_at: datetime | None


class TestedPublishedRevision(BaseModel):
    id: uuid.UUID
    number: int
    published_at: datetime


class TestRun(BaseModel):
    id: uuid.UUID
    protocol_id: uuid.UUID
    status: str
    error: str | None
    protocol_revision_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    observations: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    conversation: dict[str, Any] | None
    tested_published_revision: TestedPublishedRevision | None
    freshness: TestRunFreshness
    resources: TestRunResources
    execution_summary: TestRunExecutionSummary


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
    split_method: str | None = None
    split_group_column: str | None = None
    split_test_size: float | None = None
    split_seed: int | None = None
    target_column: str | None
    description: str | None = None
    dictionary_json: str | None = None
    created_at: datetime | None = None


class LLMSetting(BaseModel):
    provider: str
    api_base: str | None
    azure_project_endpoint: str | None = None


class LLMModelInfo(BaseModel):
    id: str
    label: str | None
    supports_temperature: bool
    supports_effort: bool
    effort_levels: list[str]
    supports_tool_calling: bool | None


class LLMModels(BaseModel):
    models: list[LLMModelInfo]
    source: str
    note: str | None


class LLMConnectionCheck(BaseModel):
    provider: str
    status: str
    detail: str
    endpoint: str | None


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


class Trial(BaseModel):
    replicate_label: str
    factor_values: dict[str, Any]
    metric_values: dict[str, Any]
    status: str
    run_id: uuid.UUID | None
    obsolete: bool
    truncated: bool
    error: str | None
    updated_at: datetime


class ExperimentResults(BaseModel):
    available: bool
    reason: str | None
    analysis: dict[str, Any] | None
    best_condition: dict[str, Any] | None


class ExperimentRunResults(BaseModel):
    overview: dict[str, Any]
    metric_keys: list[str]
    metric_types: dict[str, str]
    metric_aggregations: dict[str, str]
    metric_directions: dict[str, str]
    primary_metric: str | None
    primary_metric_direction: str | None
    cells: list[dict[str, Any]]
    replicates: list[dict[str, Any]]


class DesignImpact(BaseModel):
    has_generated_design: bool
    regeneration_required: bool
    current_cell_count: int
    proposed_cell_count: int
    added_cell_count: int
    retained_cell_count: int
    removed_cell_count: int
    current_replicate_count: int
    proposed_replicate_count: int
    added_replicate_count: int
    retained_replicate_count: int
    removed_replicate_count: int
    regeneration_reasons: list[str] = Field(default_factory=list)


class MeasurementPlanValidationIssue(BaseModel):
    code: str
    message: str
    path: str
    blocking: bool = True


class MeasurementPlanValidation(BaseModel):
    valid: bool
    issues: list[MeasurementPlanValidationIssue]


class MeasurementCapabilities(BaseModel):
    outputs: dict[str, list[str]]


class Skill(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    body: str
    frontmatter: dict[str, Any]
    is_system: bool
    source_filename: str | None
    files: list[str]
    created_at: datetime
    updated_at: datetime


class DiscoveredSkill(BaseModel):
    subdirectory: str
    name: str
    description: str
    file_count: int


class SkillUrlPreview(BaseModel):
    source: str
    ref: str
    skills: list[DiscoveredSkill]


class DirectoryEntry(BaseModel):
    name: str
    path: str
    is_bundle: bool


class DirectoryListing(BaseModel):
    path: str
    absolute_path: str
    parent: str | None
    entries: list[DirectoryEntry]


class OKFBundle(BaseModel):
    id: uuid.UUID
    name: str
    path: str | None
    uploaded: bool
    status: str
    error_message: str | None
    tool_names: list[str]
    created_at: datetime


class OKFDocument(BaseModel):
    id: uuid.UUID
    name: str
    title: str | None
    description: str | None
    concept_type: str | None
    tags: list[str]
    path: str | None
    status: str
    error_message: str | None
    tool_names: list[str]
    created_at: datetime


class UserAccount(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    is_admin: bool
    created_at: datetime


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    user: UserAccount


class Message(BaseModel):
    message: str


class ApiToken(BaseModel):
    id: uuid.UUID
    name: str
    token: str
    token_prefix: str | None
    expires_at: datetime | None
    created_at: datetime


class ApiTokenSummary(BaseModel):
    id: uuid.UUID
    name: str
    token_prefix: str | None
    last_used_at: datetime | None
    expires_at: datetime | None
    is_revoked: bool
    created_at: datetime


class ApiTokenPage(BaseModel):
    items: list[ApiTokenSummary]
    total: int
    offset: int
    limit: int


class BootstrapUser(BaseModel):
    id: uuid.UUID
    email: str


class BootstrapApiToken(BaseModel):
    id: uuid.UUID
    name: str
    token: str
