"""Research experiments and their factorial cell results.

``PUT /experiments/{id}/cells/{cell_label}`` is the one endpoint that replaces
both of the notebook's old ``client.runs.update(mlm_run_id, metadata={...})``
calls — pre-scoring and post-scoring are just two calls to it with different
fields, merged onto the same row (see ``services.factorial_cells.upsert_cell``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from asaree.deps import CurrentUser, DbSession
from asaree.services.experiments import (
    create_experiment,
    delete_experiment,
    get_experiment,
    get_experiment_by_name,
    list_experiments,
)
from asaree.services.factorial_cells import get_cell, list_cells, upsert_cell

router = APIRouter(prefix="/experiments", tags=["experiments"])


class CreateExperimentRequest(BaseModel):
    name: str
    description: str | None = None
    design_type: str = "factorial"
    task_brief: dict[str, Any] | None = None


class ExperimentResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    design_type: str
    task_brief: dict[str, Any] | None
    created_at: datetime


def _experiment_response(e: Any) -> ExperimentResponse:
    return ExperimentResponse(
        id=e.id,
        name=e.name,
        description=e.description,
        design_type=e.design_type,
        task_brief=e.task_brief,
        created_at=e.created_at,
    )


class UpsertCellRequest(BaseModel):
    """All fields optional; only the ones actually set are written.

    ``factor_values``/``metric_values``/``artifacts`` are merged into
    whatever's already stored, not replaced — pass just the pre-scoring
    fields (e.g. ``artifacts={"payload": ..., "code_sha256": ...}``) on the
    first call, just the post-scoring ones (``metric_values={"roc_auc": ...}``,
    ``artifacts={"permutation_importance_top15": [...]}``) on the second —
    both land on the same row, neither erases the other.
    """

    run_id: uuid.UUID | None = None
    workspace_id: str | None = None
    factor_values: dict[str, Any] | None = None
    metric_values: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None


class CellResponse(BaseModel):
    id: uuid.UUID
    cell_label: str
    run_id: uuid.UUID | None
    workspace_id: str | None
    factor_values: dict[str, Any] | None
    metric_values: dict[str, Any] | None
    artifacts: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


async def _get_owned_experiment(db: DbSession, experiment_id: uuid.UUID, user: CurrentUser) -> Any:
    experiment = await get_experiment(db, experiment_id)
    if experiment is None or experiment.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such experiment")
    return experiment


@router.post("", response_model=ExperimentResponse, status_code=201)
async def create_experiment_endpoint(
    body: CreateExperimentRequest, user: CurrentUser, db: DbSession
) -> ExperimentResponse:
    if await get_experiment_by_name(db, body.name) is not None:
        raise HTTPException(status_code=409, detail="An experiment with this name already exists")
    experiment = await create_experiment(
        db,
        name=body.name,
        owner_id=user.id,
        description=body.description,
        design_type=body.design_type,
        task_brief=body.task_brief,
    )
    return _experiment_response(experiment)


@router.get("", response_model=list[ExperimentResponse])
async def list_experiments_endpoint(user: CurrentUser, db: DbSession) -> list[ExperimentResponse]:
    experiments = await list_experiments(db, owner_id=user.id)
    return [_experiment_response(e) for e in experiments]


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ExperimentResponse:
    experiment = await _get_owned_experiment(db, experiment_id, user)
    return _experiment_response(experiment)


@router.delete("/{experiment_id}", status_code=204)
async def delete_experiment_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await _get_owned_experiment(db, experiment_id, user)
    await delete_experiment(db, experiment_id)


@router.put("/{experiment_id}/cells/{cell_label}", response_model=CellResponse)
async def upsert_cell_endpoint(
    experiment_id: uuid.UUID, cell_label: str, body: UpsertCellRequest, user: CurrentUser, db: DbSession
) -> CellResponse:
    await _get_owned_experiment(db, experiment_id, user)
    fields = body.model_dump(exclude_unset=True)
    cell = await upsert_cell(db, experiment_id=experiment_id, cell_label=cell_label, fields=fields)
    return CellResponse.model_validate(cell)


@router.get("/{experiment_id}/cells/{cell_label}", response_model=CellResponse)
async def get_cell_endpoint(
    experiment_id: uuid.UUID, cell_label: str, user: CurrentUser, db: DbSession
) -> CellResponse:
    await _get_owned_experiment(db, experiment_id, user)
    cell = await get_cell(db, experiment_id=experiment_id, cell_label=cell_label)
    if cell is None:
        raise HTTPException(status_code=404, detail="No such cell")
    return CellResponse.model_validate(cell)


@router.get("/{experiment_id}/cells", response_model=list[CellResponse])
async def list_cells_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[CellResponse]:
    await _get_owned_experiment(db, experiment_id, user)
    cells = await list_cells(db, experiment_id=experiment_id)
    return [CellResponse.model_validate(c) for c in cells]
