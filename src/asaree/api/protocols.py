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
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    find_gated_pairs,
    plan_cell_runs,
    plan_single_cell_run,
    topological_order,
    validate_coordination_strategy,
    validate_single_node_runnable,
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
    "unset vs. null" convention ``UpdateExperimentRequest``/``UpsertCellRequest``
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
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProtocolRunResponse(BaseModel):
    id: uuid.UUID
    protocol_id: uuid.UUID
    status: str
    node_runs: dict[str, Any]
    error: str | None
    cell_label: str | None
    factor_values: dict[str, Any] | None
    target_node_id: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CreateProtocolRunRequest(BaseModel):
    # Omitted/null -- today's ad-hoc, un-substituted whole-graph run. Set --
    # runs that one already-generated cell for real, its own factor_values
    # substituted in (see services.protocol_execution.plan_single_cell_run),
    # the same as one entry of "Run all cells" but picked by name instead of
    # running every not-yet-scored cell at once.
    cell_label: str | None = None


class CellRunBatchResponse(BaseModel):
    """One "run all cells" trigger fans out into these -- one ProtocolRun per
    not-yet-scored cell. ``skipped`` is how many cells already had
    metric_values and were left alone (resume semantics)."""

    protocol_run_ids: list[uuid.UUID]
    cell_labels: list[str]
    skipped: int


async def _get_owned_protocol(db: DbSession, protocol_id: uuid.UUID, user: CurrentUser) -> Any:
    protocol = await get_protocol(db, protocol_id)
    if protocol is None or protocol.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such protocol")
    return protocol


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
    return ProtocolResponse.model_validate(protocol)


@router.get("", response_model=list[ProtocolResponse])
async def list_protocols_endpoint(
    user: CurrentUser, db: DbSession, experiment_id: uuid.UUID | None = None
) -> list[ProtocolResponse]:
    protocols = await list_protocols(db, owner_id=user.id, experiment_id=experiment_id)
    return [ProtocolResponse.model_validate(p) for p in protocols]


@router.get("/{protocol_id}", response_model=ProtocolResponse)
async def get_protocol_endpoint(protocol_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProtocolResponse:
    protocol = await _get_owned_protocol(db, protocol_id, user)
    return ProtocolResponse.model_validate(protocol)


@router.patch("/{protocol_id}", response_model=ProtocolResponse)
async def update_protocol_endpoint(
    protocol_id: uuid.UUID, body: UpdateProtocolRequest, user: CurrentUser, db: DbSession
) -> ProtocolResponse:
    await _get_owned_protocol(db, protocol_id, user)
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        existing = await get_protocol_by_name(db, fields["name"], owner_id=user.id)
        if existing is not None and existing.id != protocol_id:
            raise HTTPException(status_code=409, detail="A protocol with this name already exists")
    if "experiment_id" in fields:
        fields["experiment_id"] = await _validated_experiment_id(fields["experiment_id"], db, user)
    protocol = await update_protocol(db, protocol_id, fields=fields)
    assert protocol is not None  # existence already checked above
    return ProtocolResponse.model_validate(protocol)


@router.delete("/{protocol_id}", status_code=204)
async def delete_protocol_endpoint(protocol_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await _get_owned_protocol(db, protocol_id, user)
    await delete_protocol(db, protocol_id)


@router.post("/{protocol_id}/runs", response_model=ProtocolRunResponse, status_code=201)
async def create_protocol_run_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession, body: CreateProtocolRunRequest | None = None
) -> ProtocolRunResponse:
    protocol = await _get_owned_protocol(db, protocol_id, user)
    cell_label = body.cell_label if body else None
    try:
        if cell_label:
            run = await plan_single_cell_run(
                db,
                protocol_id=protocol_id,
                experiment_id=protocol.experiment_id,
                owner_id=user.id,
                graph=protocol.graph,
                cell_label=cell_label,
            )
        else:
            topological_order(protocol.graph)
            experiment = await get_experiment(db, protocol.experiment_id) if protocol.experiment_id else None
            design_spec = experiment.design_spec if experiment is not None else None
            validate_coordination_strategy(design_spec, has_gated_pair=bool(find_gated_pairs(protocol.graph)))
            run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=user.id)
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
    try:
        validate_single_node_runnable(protocol.graph, node_id)
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run = await create_protocol_run(db, protocol_id=protocol_id, owner_id=user.id, target_node_id=node_id)
    await enqueue_protocol_run(run.id)
    return ProtocolRunResponse.model_validate(run)


@router.post("/{protocol_id}/cell-runs", response_model=CellRunBatchResponse, status_code=201)
async def create_cell_runs_endpoint(
    protocol_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> CellRunBatchResponse:
    """"Run all cells": one ProtocolRun per not-yet-scored FactorialCellResult
    under this protocol's linked experiment, each with that cell's own
    factor_values substituted into the graph's factor-bound fields at
    execution time (see services.protocol_execution.run_protocol)."""
    protocol = await _get_owned_protocol(db, protocol_id, user)
    try:
        runs, skipped = await plan_cell_runs(
            db,
            protocol_id=protocol.id,
            experiment_id=protocol.experiment_id,
            owner_id=user.id,
            graph=protocol.graph,
        )
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for run in runs:
        await enqueue_protocol_run(run.id)
    return CellRunBatchResponse(
        protocol_run_ids=[r.id for r in runs],
        cell_labels=[r.cell_label for r in runs if r.cell_label is not None],
        skipped=skipped,
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
