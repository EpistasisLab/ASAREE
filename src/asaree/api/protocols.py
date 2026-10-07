"""Protocols -- the executable agent/tool graph a visual canvas edits.

The graph itself is freely editable JSON the canvas reads and writes whole.
``POST /{id}/runs`` compiles it (topological order, rejecting an empty graph
before anything is created, and a cycle unless the experiment coordinates by
conversation -- see ``is_conversation_strategy``) and hands the walk to the
worker -- mirroring ``POST /runs``'s own create-then-enqueue shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH

from asaree.deps import CurrentUser, DbSession
from asaree.models.dataset import RegisteredDataset
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.dataset_row_csv import DatasetRowCsvError, project_row, read_row_source
from asaree.services.dataset_row_inputs import DatasetRowInputError, resolve_dataset_row_plan
from asaree.services.experiment_measurements import (
    blocking_measurement_plan_issues,
    validate_experiment_measurement_plan,
)
from asaree.services.experiment_versions import publication_matches_experiment, version_design_spec
from asaree.services.experiments import get_experiment
from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    is_conversation_strategy,
    plan_cell_runs,
    plan_single_replicate_run,
    preview_node_prompt,
    topological_order,
    validate_coordination_strategy,
    validate_prompt_references,
    validate_single_node_runnable,
    validate_stage_plan,
)
from asaree.services.protocol_graph_schema import normalize_protocol_graph
from asaree.services.protocol_revisions import (
    get_published_revision,
    get_revision,
    is_draft_published,
    publish_protocol,
)
from asaree.services.protocol_runs import (
    create_protocol_run,
    create_test_run,
    fail_protocol_run,
    get_protocol_run,
    list_protocol_runs,
    request_protocol_run_cancellation,
)
from asaree.services.protocols import (
    create_protocol,
    delete_protocol,
    get_protocol,
    get_protocol_by_name,
    list_protocols,
    update_protocol,
)
from asaree.services.skill_factors import test_skill_factor_values
from asaree.services.test_run_results import FreshnessReason, project_test_run_result
from asaree.worker.enqueue import enqueue_protocol_run

router = APIRouter(prefix="/protocols", tags=["protocols"])


class CreateProtocolRequest(BaseModel):
    name: str
    description: str | None = None
    experiment_id: uuid.UUID | None = None
    graph: dict[str, Any] | None = None


class UpdateProtocolRequest(BaseModel):
    """All fields optional; only the ones actually set are written -- same
    "unset vs. null" convention used by the experiment update APIs
    use. ``graph`` is a full replacement, not a merge (see
    ``services.protocols.update_protocol``)."""

    name: str | None = None
    description: str | None = None
    experiment_id: uuid.UUID | None = None
    graph: dict[str, Any] | None = None


class ProtocolResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    experiment_id: uuid.UUID | None
    graph: dict[str, Any]
    published_revision_id: uuid.UUID | None
    published_revision: int | None
    has_unpublished_changes: bool
    created_at: datetime
    updated_at: datetime


class ProtocolRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    protocol_id: uuid.UUID
    status: str
    node_runs: dict[str, Any]
    error: str | None
    replicate_label: str | None
    replicate_result_id: uuid.UUID | None
    row_result_id: uuid.UUID | None = None
    dataset_row: dict[str, Any] | None = None
    factor_values: dict[str, Any] | None
    design_revision_id: uuid.UUID | None
    protocol_revision_id: uuid.UUID | None
    target_node_id: str | None
    # Null for every pipeline run. Populated once a conversation-mode run's
    # agents start talking -- see models/protocol_run.py for the shape.
    conversation: dict[str, Any] | None = None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    observations: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


def _protocol_run_response(run: Any) -> ProtocolRunResponse:
    response = ProtocolRunResponse.model_validate(run)
    measurement = (run.attempt_result or {}).get("measurement") if isinstance(run.attempt_result, dict) else None
    return response.model_copy(
        update={
            "row_result_id": run.row_result_id,
            "dataset_row": run.dataset_row,
            "observations": list(measurement.get("observations") or []) if isinstance(measurement, dict) else [],
            "artifacts": list(measurement.get("artifacts") or []) if isinstance(measurement, dict) else [],
        }
    )


class TestedPublishedRevisionResponse(BaseModel):
    id: uuid.UUID
    number: int
    published_at: datetime


class ResourceUsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    duration_seconds: float | None
    cost_usd: float | None


class TestRunResourcesResponse(BaseModel):
    task: ResourceUsageResponse
    evaluation: ResourceUsageResponse
    total: ResourceUsageResponse


class TestRunFreshnessResponse(BaseModel):
    out_of_date: bool
    reasons: list[FreshnessReason]


class TestRunExecutionSummaryResponse(BaseModel):
    node_runs: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    cancel_requested_at: datetime | None


class TestRunResponse(BaseModel):
    """The non-factorial result of validating a canvas once."""

    id: uuid.UUID
    protocol_id: uuid.UUID
    status: str
    error: str | None
    protocol_revision_id: uuid.UUID | None
    dataset_row: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    observations: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    conversation: dict[str, Any] | None
    tested_published_revision: TestedPublishedRevisionResponse | None
    freshness: TestRunFreshnessResponse
    resources: TestRunResourcesResponse
    execution_summary: TestRunExecutionSummaryResponse


async def _test_run_response(db: DbSession, run: Any, protocol: Any, experiment: Any) -> TestRunResponse:
    result = await project_test_run_result(db, run=run, protocol=protocol, experiment=experiment)
    tested_revision = result.tested_published_revision
    return TestRunResponse(
        id=run.id,
        protocol_id=run.protocol_id,
        status=run.status,
        error=run.error,
        protocol_revision_id=result.protocol_revision_id,
        dataset_row=result.dataset_row,
        created_at=run.created_at,
        updated_at=run.updated_at,
        observations=result.observations,
        artifacts=result.artifacts,
        conversation=run.conversation,
        tested_published_revision=(
            {
                "id": tested_revision.id,
                "number": tested_revision.revision,
                "published_at": tested_revision.published_at,
            }
            if tested_revision is not None
            else None
        ),
        freshness={"out_of_date": bool(result.freshness_reasons), "reasons": result.freshness_reasons},
        resources={
            "task": result.resources.task,
            "evaluation": result.resources.evaluation,
            "total": result.resources.total,
        },
        execution_summary={
            "node_runs": run.node_runs or {},
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "cancel_requested_at": run.cancel_requested_at,
        },
    )


class VersionAnnotationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("name", "note")
    @classmethod
    def trim_annotation(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ProtocolRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    protocol_id: uuid.UUID
    revision: int
    name: str | None = None
    note: str | None = None
    run_count: int = 0
    result_count: int = 0
    graph: dict[str, Any]
    published_at: datetime
    experiment_snapshot: dict[str, Any] | None = None
    design_revision_id: uuid.UUID | None = None


class CreateProtocolRunRequest(BaseModel):
    # Omitted/null -- today's ad-hoc, un-substituted whole-graph run. Set --
    # runs that one already-generated replicate for real, its cell's factor_values
    # substituted in (see services.protocol_execution.plan_single_replicate_run),
    # the same as one entry of "Run all cells" but picked by name instead of
    # running every not-yet-completed replicate at once.
    replicate_label: str | None = None
    row_index: StrictInt | None = Field(default=None, ge=0)


class TestRunRequest(BaseModel):
    """Optional original Dataset row selection for a row-mode Test Run."""

    row_index: StrictInt | None = Field(default=None, ge=0)
    skill_selections: dict[str, str] | None = None


class NodePlayRequest(BaseModel):
    """Optional original Dataset row selection for an eligible node Play."""

    row_index: StrictInt | None = Field(default=None, ge=0)
    skill_selections: dict[str, str] | None = None


async def _node_test_skill_values(
    graph: dict[str, Any], body: NodePlayRequest | None, owner_id: uuid.UUID, node_id: str
) -> dict[str, Any]:
    try:
        return await test_skill_factor_values(graph, body.skill_selections if body else None, owner_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class CellRunBatchRequest(BaseModel):
    """Options for a cell batch or an explicit row-slot retry.

    Omit this body for the normal resume behavior: every never-completed
    replicate runs, while completed ones remain skipped.
    """

    # When omitted, this is the whole current design. Supplying labels scopes
    # a run from one cell to just that cell's replicates.
    replicate_labels: list[str] | None = None
    rerun_replicate_labels: list[str] = []
    retry_row_result_ids: list[uuid.UUID] | None = None
    # Zero-based source rows, scoped to exactly one selected replicate.
    row_indices: list[StrictInt] | None = None


class CellRunBatchResponse(BaseModel):
    """One "run all cells" trigger fans out into these -- one ProtocolRun per
    not-yet-completed replicate. ``skipped`` is how many replicates already
    completed and were left alone (resume semantics)."""

    protocol_run_ids: list[uuid.UUID]
    replicate_labels: list[str]
    skipped: int
    protocol_revision_id: uuid.UUID
    protocol_revision: int
    consumption_mode: str = "whole_dataset"
    row_result_ids: list[uuid.UUID] = Field(default_factory=list)


class PromptPreviewRequest(BaseModel):
    # The canvas to assemble against. Sent because the inspector previews what
    # is on screen, including edits autosave has not flushed yet; omitted, the
    # stored draft stands in. Nothing is written either way.
    graph: dict[str, Any] | None = None
    row_index: StrictInt | None = None


class PromptPreviewResponse(BaseModel):
    text: str
    dataset_row: dict[str, Any] | None = None


async def _get_owned_protocol(db: DbSession, protocol_id: uuid.UUID, user: CurrentUser) -> Any:
    protocol = await get_protocol(db, protocol_id)
    if protocol is None or protocol.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such protocol")
    return protocol


async def _protocol_response(db: DbSession, protocol: Any) -> ProtocolResponse:
    published = await get_published_revision(db, protocol)
    return ProtocolResponse(
        id=protocol.id,
        name=protocol.name,
        description=protocol.description,
        experiment_id=protocol.experiment_id,
        graph=normalize_protocol_graph(protocol.graph),
        published_revision_id=published.id if published else None,
        published_revision=published.revision if published else None,
        has_unpublished_changes=not (
            is_draft_published(protocol, published) and await publication_matches_experiment(db, protocol, published)
        ),
        created_at=protocol.created_at,
        updated_at=protocol.updated_at,
    )


async def _require_published_revision(db: DbSession, protocol: Any) -> Any:
    revision = await get_published_revision(db, protocol)
    if revision is None:
        raise HTTPException(status_code=409, detail="Publish this protocol before running it.")
    return revision


async def _validated_experiment_id(
    experiment_id: uuid.UUID | None, db: DbSession, user: CurrentUser
) -> uuid.UUID | None:
    if experiment_id is None:
        return None
    experiment = await get_experiment(db, experiment_id)
    if experiment is None or experiment.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such experiment")
    return experiment_id


@router.post("", response_model=ProtocolResponse, status_code=201)
async def create_protocol_endpoint(body: CreateProtocolRequest, user: CurrentUser, db: DbSession) -> ProtocolResponse:
    if await get_protocol_by_name(db, body.name, owner_id=user.id) is not None:
        raise HTTPException(status_code=409, detail="A protocol with this name already exists")
    experiment_id = await _validated_experiment_id(body.experiment_id, db, user)
    protocol = await create_protocol(
        db,
        name=body.name,
        owner_id=user.id,
        description=body.description,
        experiment_id=experiment_id,
        graph=body.graph,
    )
    return await _protocol_response(db, protocol)


@router.get("", response_model=list[ProtocolResponse])
async def list_protocols_endpoint(
    user: CurrentUser, db: DbSession, experiment_id: uuid.UUID | None = None
) -> list[ProtocolResponse]:
    protocols = await list_protocols(db, owner_id=user.id, experiment_id=experiment_id)
    return [await _protocol_response(db, p) for p in protocols]


@router.get("/{protocol_id}", response_model=ProtocolResponse)
async def get_protocol_endpoint(protocol_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProtocolResponse:
    protocol = await _get_owned_protocol(db, protocol_id, user)
    return await _protocol_response(db, protocol)


@router.patch("/{protocol_id}", response_model=ProtocolResponse)
async def update_protocol_endpoint(
    protocol_id: uuid.UUID, body: UpdateProtocolRequest, user: CurrentUser, db: DbSession
) -> ProtocolResponse:
    existing_protocol = await _get_owned_protocol(db, protocol_id, user)
    fields = body.model_dump(exclude_unset=True)
    if existing_protocol.experiment_id and {"graph", "experiment_id"}.intersection(fields):
        experiment = await get_experiment(db, existing_protocol.experiment_id)
        if experiment is not None and experiment.locked_at is not None:
            raise HTTPException(status_code=409, detail="Experiment is locked. Unlock it before changing the canvas.")
    if "name" in fields and fields["name"] is not None:
        existing = await get_protocol_by_name(db, fields["name"], owner_id=user.id)
        if existing is not None and existing.id != protocol_id:
            raise HTTPException(status_code=409, detail="A protocol with this name already exists")
    if "experiment_id" in fields:
        fields["experiment_id"] = await _validated_experiment_id(fields["experiment_id"], db, user)
    protocol = await update_protocol(db, protocol_id, fields=fields)
    assert protocol is not None  # existence already checked above
    return await _protocol_response(db, protocol)


@router.post("/{protocol_id}/publish", response_model=ProtocolResponse)
async def publish_protocol_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession,
    body: VersionAnnotationsRequest | None = None,
) -> ProtocolResponse:
    """Make the current autosaved canvas the immutable version future runs use."""
    protocol = await _get_owned_protocol(db, protocol_id, user)
    published = await get_published_revision(db, protocol)
    try:
        # Strategy before shape: the acyclic requirement is a pipeline
        # requirement, and a peer_collaboration canvas isn't run as one -- see
        # is_conversation_strategy.
        experiment = await get_experiment(db, protocol.experiment_id) if protocol.experiment_id else None
        design_spec = experiment.design_spec if experiment is not None else None
        validate_coordination_strategy(design_spec, graph=protocol.graph)
        validate_stage_plan(design_spec)
        validate_prompt_references(graph=protocol.graph)
        topological_order(protocol.graph, require_acyclic=not is_conversation_strategy(design_spec))
        # An incomplete skill comparison can still be published for a selected
        # test. Generation and production planning enforce complete choices.
        validate_factor_bindings(design_spec, protocol.graph, complete_skills=False)
        if experiment is not None:
            report = await validate_experiment_measurement_plan(
                db,
                document=experiment.measurement_plan,
                metrics=(experiment.design_spec or {}).get("metrics"),
                graph=protocol.graph,
                experiment_id=experiment.id,
                owner_id=experiment.owner_id,
            )
            if blocking_issues := blocking_measurement_plan_issues(report):
                raise ProtocolValidationError("; ".join(issue.message for issue in blocking_issues))
    except (ProtocolValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if (
        published is not None and is_draft_published(protocol, published)
        and await publication_matches_experiment(db, protocol, published)
    ):
        try:
            await publish_protocol(db, protocol, owner_id=user.id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await _protocol_response(db, protocol)
    if protocol.experiment_id:
        experiment = await get_experiment(db, protocol.experiment_id)
        if experiment is not None and experiment.locked_at is not None:
            raise HTTPException(
                status_code=409,
                detail="Experiment is locked. Unlock it before publishing a changed canvas.",
            )
    try:
        await publish_protocol(db, protocol, owner_id=user.id, **(body.model_dump() if body else {}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _protocol_response(db, protocol)


@router.get("/{protocol_id}/revisions", response_model=list[ProtocolRevisionResponse])
async def list_protocol_revisions_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession,
) -> list[ProtocolRevisionResponse]:
    await _get_owned_protocol(db, protocol_id, user)
    revisions = await db.scalars(
        select(ProtocolRevision).where(ProtocolRevision.protocol_id == protocol_id)
        .order_by(ProtocolRevision.revision.desc())
    )
    counts = await _version_run_counts(db, protocol_id, user.id)
    return [_version_response(revision, counts.get(revision.id, (0, 0))) for revision in revisions]


async def _version_run_counts(
    db: DbSession, protocol_id: uuid.UUID, owner_id: uuid.UUID,
) -> dict[uuid.UUID, tuple[int, int]]:
    # Count execution attempts with recorded scores/observations, never a
    # design's mutable latest-results projection shared by several versions.
    has_result = or_(
        (func.jsonb_typeof(ProtocolRun.attempt_result["metric_values"]) == "object")
        & (ProtocolRun.attempt_result["metric_values"] != {}),
        func.jsonb_path_exists(
            ProtocolRun.attempt_result,
            cast('$.measurement.observations[*] ? (@.status == "measured" && @.value != null)', JSONPATH),
        ),
    )
    rows = await db.execute(
        select(
            ProtocolRun.protocol_revision_id,
            func.count(ProtocolRun.id),
            func.count(ProtocolRun.id).filter(has_result),
        ).where(
            ProtocolRun.protocol_id == protocol_id, ProtocolRun.owner_id == owner_id,
            ProtocolRun.protocol_revision_id.is_not(None),
            ProtocolRun.is_test_run.is_(False), ProtocolRun.target_node_id.is_(None),
        ).group_by(ProtocolRun.protocol_revision_id)
    )
    return {revision_id: (run_count, result_count) for revision_id, run_count, result_count in rows}


def _version_response(revision: ProtocolRevision, counts: tuple[int, int]) -> ProtocolRevisionResponse:
    return ProtocolRevisionResponse.model_validate(revision).model_copy(update={
        "graph": normalize_protocol_graph(revision.graph),
        "run_count": counts[0], "result_count": counts[1],
    })


@router.get("/{protocol_id}/revisions/{revision_id}", response_model=ProtocolRevisionResponse)
async def get_protocol_revision_endpoint(
    protocol_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProtocolRevisionResponse:
    await _get_owned_protocol(db, protocol_id, user)
    revision = await get_revision(db, revision_id)
    if revision is None or revision.protocol_id != protocol_id:
        raise HTTPException(status_code=404, detail="No such protocol revision")
    counts = await _version_run_counts(db, protocol_id, user.id)
    return _version_response(revision, counts.get(revision.id, (0, 0)))


@router.patch("/{protocol_id}/revisions/{revision_id}", response_model=ProtocolRevisionResponse)
async def update_protocol_revision_endpoint(
    protocol_id: uuid.UUID, revision_id: uuid.UUID, body: VersionAnnotationsRequest,
    user: CurrentUser, db: DbSession,
) -> ProtocolRevisionResponse:
    await _get_owned_protocol(db, protocol_id, user)
    revision = await get_revision(db, revision_id)
    if revision is None or revision.protocol_id != protocol_id:
        raise HTTPException(status_code=404, detail="No such protocol revision")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(revision, key, value)
    await db.flush()
    counts = await _version_run_counts(db, protocol_id, user.id)
    return _version_response(revision, counts.get(revision.id, (0, 0)))


@router.delete("/{protocol_id}", status_code=204)
async def delete_protocol_endpoint(protocol_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    protocol = await _get_owned_protocol(db, protocol_id, user)
    if protocol.experiment_id:
        experiment = await get_experiment(db, protocol.experiment_id)
        if experiment is not None and experiment.locked_at is not None:
            raise HTTPException(status_code=409, detail="Experiment is locked. Unlock it before deleting the canvas.")
    await delete_protocol(db, protocol_id)


@router.post("/{protocol_id}/runs", response_model=ProtocolRunResponse, status_code=201)
async def create_protocol_run_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession, body: CreateProtocolRunRequest | None = None
) -> ProtocolRunResponse:
    protocol = await _get_owned_protocol(db, protocol_id, user)
    revision = await _require_published_revision(db, protocol)
    replicate_label = body.replicate_label if body else None
    row_index = body.row_index if body else None
    try:
        row_mode = resolve_dataset_row_plan(revision.graph) is not None
    except DatasetRowInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row_index is not None and not row_mode:
        raise HTTPException(status_code=422, detail="no_row_driver")
    try:
        if replicate_label or row_mode:
            run = await plan_single_replicate_run(
                db,
                protocol_id=protocol_id,
                experiment_id=protocol.experiment_id,
                owner_id=user.id,
                graph=revision.graph,
                replicate_label=replicate_label,
                row_index=row_index,
                snapshot_only=replicate_label is None,
                protocol_revision_id=revision.id,
            )
        else:
            experiment = await get_experiment(db, protocol.experiment_id) if protocol.experiment_id else None
            design_spec = experiment.design_spec if experiment is not None else None
            validate_coordination_strategy(design_spec, graph=revision.graph)
            validate_stage_plan(design_spec)
            validate_prompt_references(graph=revision.graph)
            topological_order(revision.graph, require_acyclic=not is_conversation_strategy(design_spec))
            run = await create_protocol_run(
                db, protocol_id=protocol_id, owner_id=user.id, protocol_revision_id=revision.id
            )
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row_mode:
        await db.commit()
        await db.refresh(run)
    await enqueue_protocol_run(run.id)
    return _protocol_run_response(run)


@router.post("/{protocol_id}/test-runs", response_model=TestRunResponse, status_code=201)
async def create_test_run_endpoint(
    protocol_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    body: TestRunRequest | None = None,
) -> TestRunResponse:
    """Start the experiment's single current canvas Test Run."""
    protocol = await _get_owned_protocol(db, protocol_id, user)
    revision = await _require_published_revision(db, protocol)
    try:
        experiment = await get_experiment(db, protocol.experiment_id) if protocol.experiment_id else None
        published_design = version_design_spec(revision, experiment.design_spec if experiment is not None else None)
        validate_coordination_strategy(published_design, graph=revision.graph)
        validate_stage_plan(published_design)
        validate_prompt_references(graph=revision.graph)
        topological_order(
            revision.graph,
            require_acyclic=not is_conversation_strategy(published_design),
        )
        dataset_row = None
        try:
            row_plan = resolve_dataset_row_plan(revision.graph, published_design)
        except DatasetRowInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        row_index = body.row_index if body is not None else None
        if row_plan is None and row_index is not None:
            raise HTTPException(status_code=422, detail="no_row_driver")
        if row_plan is not None:
            registration = (
                await db.execute(
                    select(RegisteredDataset).where(
                        RegisteredDataset.id == uuid.UUID(row_plan["driver_dataset_id"]),
                        RegisteredDataset.owner_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if registration is None:
                raise HTTPException(status_code=422, detail="Registered row source is unavailable.")
            try:
                source = read_row_source(
                    dataset_id=str(registration.id),
                    raw_path=registration.raw_path,
                    raw_sha256=registration.raw_sha256,
                )
                columns = list(
                    dict.fromkeys(column for binding in row_plan["bindings"] for column in binding["columns"])
                )
                selected_index = 0 if row_index is None else row_index
                dataset_row = project_row(source, row_index=selected_index, columns=columns)
            except DatasetRowCsvError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        run = await create_test_run(
            db,
            protocol_id=protocol.id,
            owner_id=user.id,
            protocol_revision_id=revision.id,
            dataset_row=dataset_row,
            factor_values=await test_skill_factor_values(
                revision.graph, body.skill_selections if body else None, user.id
            ),
        )
    except (ProtocolValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if run.dataset_row is not None:
        await db.commit()
    await enqueue_protocol_run(run.id)
    return await _test_run_response(db, run, protocol, experiment)


@router.get("/{protocol_id}/test-runs/latest", response_model=TestRunResponse)
async def get_latest_test_run_endpoint(protocol_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TestRunResponse:
    protocol = await _get_owned_protocol(db, protocol_id, user)
    experiment = await get_experiment(db, protocol.experiment_id) if protocol.experiment_id else None
    run = (
        await get_protocol_run(db, experiment.latest_test_run_id)
        if experiment and experiment.latest_test_run_id
        else None
    )
    if run is None or not run.is_test_run or run.protocol_id != protocol_id:
        raise HTTPException(status_code=404, detail="No Test Run for this canvas")
    # Source-ready metric producers persist checkpoints in their own session.
    # Reload before projecting so a long-lived caller does not serve the
    # identity map's pre-checkpoint attempt_result.
    await db.refresh(run)
    return await _test_run_response(db, run, protocol, experiment)


@router.post("/{protocol_id}/nodes/{node_id}/run", response_model=ProtocolRunResponse, status_code=201)
async def run_single_node_endpoint(
    protocol_id: uuid.UUID,
    node_id: str,
    user: CurrentUser,
    db: DbSession,
    body: NodePlayRequest | None = None,
) -> ProtocolRunResponse:
    """The canvas's per-node Play icon -- runs one Agent node in isolation.
    Scoped to a node with no upstream input (validated up front, same
    fail-before-creating-anything shape as the plain-run endpoint above):
    running a node mid-pipeline against real upstream output needs a
    bounded/partial-run entrypoint this executor doesn't have yet."""
    protocol = await _get_owned_protocol(db, protocol_id, user)
    revision = await _require_published_revision(db, protocol)
    try:
        validate_single_node_runnable(revision.graph, node_id)
        row_plan = resolve_dataset_row_plan(revision.graph)
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DatasetRowInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row_index = body.row_index if body is not None else None
    row_binding = next(
        (binding for binding in (row_plan or {}).get("bindings", []) if binding["agent_node_id"] == node_id),
        None,
    )
    if row_index is not None and row_binding is None:
        raise HTTPException(status_code=422, detail="no_row_driver")
    dataset_row = None
    if row_binding is not None:
        registration = (
            await db.execute(
                select(RegisteredDataset).where(
                    RegisteredDataset.id == uuid.UUID(row_plan["driver_dataset_id"]),
                    RegisteredDataset.owner_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if registration is None:
            raise HTTPException(status_code=422, detail="Registered row source is unavailable.")
        try:
            source = read_row_source(
                dataset_id=str(registration.id),
                raw_path=registration.raw_path,
                raw_sha256=registration.raw_sha256,
            )
            columns = row_binding["columns"]
            if not columns or set(columns) - set(source.columns):
                raise DatasetRowCsvError(
                    "invalid_columns", "row input columns must exist in the registered CSV header"
                )
            dataset_row = project_row(source, row_index=0 if row_index is None else row_index, columns=columns)
        except DatasetRowCsvError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    run = await create_protocol_run(
        db,
        protocol_id=protocol_id,
        owner_id=user.id,
        target_node_id=node_id,
        protocol_revision_id=revision.id,
        dataset_row=dataset_row,
        factor_values=await _node_test_skill_values(revision.graph, body, user.id, node_id),
    )
    await db.commit()
    await enqueue_protocol_run(run.id)
    return _protocol_run_response(run)


@router.post("/{protocol_id}/nodes/{node_id}/prompt-preview", response_model=PromptPreviewResponse)
async def preview_node_prompt_endpoint(
    protocol_id: uuid.UUID, node_id: str, body: PromptPreviewRequest, user: CurrentUser, db: DbSession
) -> PromptPreviewResponse:
    """What this agent's prompt would look like, without running anything.

    Read-only despite being a POST: the canvas being previewed is the one on
    screen, which the client sends rather than the server reading back a draft
    autosave that may be a beat behind what was just typed. Creates no run of
    any kind (see ``preview_node_prompt``); the 200 is a rendering, not a
    resource.
    """
    protocol = await _get_owned_protocol(db, protocol_id, user)
    dataset_rows: list[dict[str, Any]] = []
    try:
        text = await preview_node_prompt(
            normalize_protocol_graph(body.graph) if body.graph is not None else protocol.graph,
            node_id,
            owner_id=user.id,
            experiment_id=protocol.experiment_id,
            row_index=body.row_index,
            dataset_row_out=dataset_rows,
        )
    except (ProtocolValidationError, DatasetRowInputError, DatasetRowCsvError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PromptPreviewResponse(text=text, dataset_row=dataset_rows[0] if dataset_rows else None)


@router.post("/{protocol_id}/cell-runs", response_model=CellRunBatchResponse, status_code=201)
async def create_cell_runs_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession, body: CellRunBatchRequest | None = None
) -> CellRunBatchResponse:
    """Run pending replicates or retry explicitly selected failed row slots."""
    protocol = await _get_owned_protocol(db, protocol_id, user)
    revision = await _require_published_revision(db, protocol)
    try:
        retry_row_result_ids = body.retry_row_result_ids if body is not None else None
        if retry_row_result_ids is not None:
            try:
                is_row_mode = resolve_dataset_row_plan(revision.graph) is not None
            except DatasetRowInputError:
                is_row_mode = False
            if (
                not retry_row_result_ids
                or len(set(retry_row_result_ids)) != len(retry_row_result_ids)
                or (body is not None and body.replicate_labels is not None)
                or (body is not None and body.rerun_replicate_labels)
                or not is_row_mode
            ):
                raise ProtocolValidationError("invalid_retry_selection")
        runs, skipped = await plan_cell_runs(
            db,
            protocol_id=protocol.id,
            experiment_id=protocol.experiment_id,
            owner_id=user.id,
            graph=revision.graph,
            protocol_revision_id=revision.id,
            replicate_labels=set(body.replicate_labels) if body and body.replicate_labels is not None else None,
            rerun_replicate_labels=set(body.rerun_replicate_labels) if body is not None else None,
            retry_row_result_ids=retry_row_result_ids,
            row_indices=body.row_indices if body is not None else None,
        )
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row_mode = resolve_dataset_row_plan(revision.graph) is not None
    if row_mode:
        await db.commit()
        for run in runs:
            try:
                await enqueue_protocol_run(run.id)
            except Exception as exc:
                await fail_protocol_run(
                    db, run.id, error=f"enqueue_failure: {type(exc).__name__}: {exc}"
                )
                await db.commit()
    else:
        for run in runs:
            await enqueue_protocol_run(run.id)
    return CellRunBatchResponse(
        protocol_run_ids=[r.id for r in runs],
        replicate_labels=[r.replicate_label for r in runs if r.replicate_label is not None],
        skipped=skipped,
        protocol_revision_id=revision.id,
        protocol_revision=revision.revision,
        consumption_mode=(
            "per_row" if row_mode else "whole_dataset"
        ),
        row_result_ids=[r.row_result_id for r in runs if r.row_result_id is not None],
    )


@router.get("/{protocol_id}/runs", response_model=list[ProtocolRunResponse])
async def list_protocol_runs_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[ProtocolRunResponse]:
    await _get_owned_protocol(db, protocol_id, user)
    runs = await list_protocol_runs(db, protocol_id=protocol_id)
    return [_protocol_run_response(r) for r in runs]


@router.get("/{protocol_id}/runs/{run_id}", response_model=ProtocolRunResponse)
async def get_protocol_run_endpoint(
    protocol_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProtocolRunResponse:
    await _get_owned_protocol(db, protocol_id, user)
    run = await get_protocol_run(db, run_id)
    if run is None or run.protocol_id != protocol_id:
        raise HTTPException(status_code=404, detail="No such protocol run")
    return _protocol_run_response(run)


@router.post("/{protocol_id}/runs/{run_id}/cancel", response_model=ProtocolRunResponse)
async def cancel_protocol_run_endpoint(
    protocol_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProtocolRunResponse:
    """Cancel a non-terminal run -- immediately if queued, cooperatively if active."""
    await _get_owned_protocol(db, protocol_id, user)
    run = await get_protocol_run(db, run_id)
    if run is None or run.protocol_id != protocol_id:
        raise HTTPException(status_code=404, detail="No such protocol run")
    run = await request_protocol_run_cancellation(db, run_id)
    return _protocol_run_response(run)
