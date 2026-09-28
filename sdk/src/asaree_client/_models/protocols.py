from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
