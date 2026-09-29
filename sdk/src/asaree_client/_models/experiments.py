from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Experiment(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    hypothesis: str | None = None
    design_type: str
    task_brief: dict[str, Any] | None
    design_spec: dict[str, Any] | None
    measurement_plan: dict[str, Any] | None = None
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
    design_revision_id: uuid.UUID
    run_id: uuid.UUID | None
    workspace_id: str | None
    factor_values: dict[str, Any] | None
    metric_values: dict[str, Any] | None
    artifacts: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class DesignRevision(BaseModel):
    id: uuid.UUID
    revision: int
    superseded_at: datetime | None
    design_spec: dict[str, Any] | None
    cell_count: int
    replicate_count: int
    scored_replicate_count: int
    created_at: datetime


class ExperimentArtifact(BaseModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    name: str
    kind: str
    content: str
    created_at: datetime
    updated_at: datetime


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
