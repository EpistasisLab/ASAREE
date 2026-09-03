"""Protocols -- the executable agent/tool graph a visual canvas edits.

The graph itself is freely editable JSON the canvas reads and writes whole.
``POST /{id}/runs`` compiles it (topological order, rejecting a cycle/empty
graph before anything is created) and hands the walk to the worker --
mirroring ``POST /runs``'s own create-then-enqueue shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from asaree.deps import CurrentUser, DbSession
from asaree.services.experiments import get_experiment
from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    find_gated_pairs,
    plan_cell_runs,
    plan_single_replicate_run,
    topological_order,
    validate_coordination_strategy,
    validate_single_node_runnable,
)
from asaree.services.protocol_revisions import (
    get_published_revision,
    get_revision,
    is_draft_published,
    publish_protocol,
)
from asaree.services.protocol_runs import (
    create_protocol_run,
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
    id: uuid.UUID
    protocol_id: uuid.UUID
    status: str
    node_runs: dict[str, Any]
    error: str | None
    replicate_label: str | None
    replicate_result_id: uuid.UUID | None
    factor_values: dict[str, Any] | None
    design_revision_id: uuid.UUID | None
    protocol_revision_id: uuid.UUID | None
    target_node_id: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProtocolRevisionResponse(BaseModel):
    id: uuid.UUID
    protocol_id: uuid.UUID
    revision: int
    graph: dict[str, Any]
    published_at: datetime

    class Config:
        from_attributes = True

class CreateProtocolRunRequest(BaseModel):
    # Omitted/null -- today's ad-hoc, un-substituted whole-graph run. Set --
    # runs that one already-generated replicate for real, its cell's factor_values
    # substituted in (see services.protocol_execution.plan_single_replicate_run),
    # the same as one entry of "Run all cells" but picked by name instead of
    # running every not-yet-scored replicate at once.
    replicate_label: str | None = None


class CellRunBatchRequest(BaseModel):
    """Previously scored replicates the user explicitly chose to run again.

    Omit this body for the normal resume behavior: every unscored replicate
    runs, while scored ones remain skipped.
    """

    # When omitted, this is the whole current design. Supplying labels scopes
    # a run from one cell to just that cell's replicates.
    replicate_labels: list[str] | None = None
    rerun_replicate_labels: list[str] = []


class CellRunBatchResponse(BaseModel):
    """One "run all cells" trigger fans out into these -- one ProtocolRun per
    not-yet-scored replicate. ``skipped`` is how many replicates already had
    metric_values and were left alone (resume semantics)."""

    protocol_run_ids: list[uuid.UUID]
    replicate_labels: list[str]
    skipped: int
    protocol_revision_id: uuid.UUID
    protocol_revision: int


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
        graph=protocol.graph,
        published_revision_id=published.id if published else None,
        published_revision=published.revision if published else None,
        has_unpublished_changes=not is_draft_published(protocol, published),
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
async def create_protocol_endpoint(
    body: CreateProtocolRequest, user: CurrentUser, db: DbSession
) -> ProtocolResponse:
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
async def publish_protocol_endpoint(protocol_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProtocolResponse:
    """Make the current autosaved canvas the immutable version future runs use."""
    protocol = await _get_owned_protocol(db, protocol_id, user)
    if protocol.experiment_id:
        experiment = await get_experiment(db, protocol.experiment_id)
        if experiment is not None and experiment.locked_at is not None:
            raise HTTPException(
                status_code=409,
                detail="Experiment is locked. Unlock it before publishing a changed canvas.",
            )
    try:
        topological_order(protocol.graph)
        experiment = await get_experiment(db, protocol.experiment_id) if protocol.experiment_id else None
        design_spec = experiment.design_spec if experiment is not None else None
        validate_coordination_strategy(design_spec, has_gated_pair=bool(find_gated_pairs(protocol.graph)))
        validate_factor_bindings(design_spec, protocol.graph)
    except (ProtocolValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await publish_protocol(db, protocol)
    return await _protocol_response(db, protocol)


@router.get("/{protocol_id}/revisions/{revision_id}", response_model=ProtocolRevisionResponse)
async def get_protocol_revision_endpoint(
    protocol_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProtocolRevisionResponse:
    await _get_owned_protocol(db, protocol_id, user)
    revision = await get_revision(db, revision_id)
    if revision is None or revision.protocol_id != protocol_id:
        raise HTTPException(status_code=404, detail="No such protocol revision")
    return ProtocolRevisionResponse.model_validate(revision)


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
    try:
        if replicate_label:
            run = await plan_single_replicate_run(
                db,
                protocol_id=protocol_id,
                experiment_id=protocol.experiment_id,
                owner_id=user.id,
                graph=revision.graph,
                replicate_label=replicate_label,
                protocol_revision_id=revision.id,
            )
        else:
            topological_order(revision.graph)
            experiment = await get_experiment(db, protocol.experiment_id) if protocol.experiment_id else None
            design_spec = experiment.design_spec if experiment is not None else None
            validate_coordination_strategy(design_spec, has_gated_pair=bool(find_gated_pairs(revision.graph)))
            run = await create_protocol_run(
                db, protocol_id=protocol_id, owner_id=user.id, protocol_revision_id=revision.id
            )
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await enqueue_protocol_run(run.id)
    return ProtocolRunResponse.model_validate(run)


@router.post("/{protocol_id}/nodes/{node_id}/run", response_model=ProtocolRunResponse, status_code=201)
async def run_single_node_endpoint(
    protocol_id: uuid.UUID, node_id: str, user: CurrentUser, db: DbSession
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
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run = await create_protocol_run(
        db, protocol_id=protocol_id, owner_id=user.id, target_node_id=node_id, protocol_revision_id=revision.id
    )
    await enqueue_protocol_run(run.id)
    return ProtocolRunResponse.model_validate(run)


@router.post("/{protocol_id}/cell-runs", response_model=CellRunBatchResponse, status_code=201)
async def create_cell_runs_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession, body: CellRunBatchRequest | None = None
) -> CellRunBatchResponse:
    """Run every pending replicate plus any explicitly selected reruns."""
    protocol = await _get_owned_protocol(db, protocol_id, user)
    revision = await _require_published_revision(db, protocol)
    try:
        runs, skipped = await plan_cell_runs(
            db,
            protocol_id=protocol.id,
            experiment_id=protocol.experiment_id,
            owner_id=user.id,
            graph=revision.graph,
            protocol_revision_id=revision.id,
            replicate_labels=set(body.replicate_labels) if body and body.replicate_labels is not None else None,
            rerun_replicate_labels=set(body.rerun_replicate_labels) if body is not None else None,
        )
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for run in runs:
        await enqueue_protocol_run(run.id)
    return CellRunBatchResponse(
        protocol_run_ids=[r.id for r in runs],
        replicate_labels=[r.replicate_label for r in runs if r.replicate_label is not None],
        skipped=skipped,
        protocol_revision_id=revision.id,
        protocol_revision=revision.revision,
    )


@router.get("/{protocol_id}/runs", response_model=list[ProtocolRunResponse])
async def list_protocol_runs_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[ProtocolRunResponse]:
    await _get_owned_protocol(db, protocol_id, user)
    runs = await list_protocol_runs(db, protocol_id=protocol_id)
    return [ProtocolRunResponse.model_validate(r) for r in runs]


@router.get("/{protocol_id}/runs/{run_id}", response_model=ProtocolRunResponse)
async def get_protocol_run_endpoint(
    protocol_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProtocolRunResponse:
    await _get_owned_protocol(db, protocol_id, user)
    run = await get_protocol_run(db, run_id)
    if run is None or run.protocol_id != protocol_id:
        raise HTTPException(status_code=404, detail="No such protocol run")
    return ProtocolRunResponse.model_validate(run)


@router.post("/{protocol_id}/runs/{run_id}/cancel", response_model=ProtocolRunResponse)
async def cancel_protocol_run_endpoint(
    protocol_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ProtocolRunResponse:
    """Requests cancellation of a non-terminal run -- a no-op (200, unchanged
    row) if it's already completed/failed/cancelled. Only raises the flag;
    services.protocol_execution.run_protocol's node loop is what actually
    honors it, between nodes, and transitions status to "cancelled" itself."""
    await _get_owned_protocol(db, protocol_id, user)
    run = await get_protocol_run(db, run_id)
    if run is None or run.protocol_id != protocol_id:
        raise HTTPException(status_code=404, detail="No such protocol run")
    run = await request_protocol_run_cancellation(db, run_id)
    return ProtocolRunResponse.model_validate(run)
