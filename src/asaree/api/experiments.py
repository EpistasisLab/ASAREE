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
from asaree.services.datasets import get_dataset
from asaree.services.design_generation import DesignValidationError, generate_design_cells
from asaree.services.experiments import (
    create_experiment,
    delete_experiment,
    get_experiment,
    get_experiment_by_name,
    list_experiments,
    update_experiment,
)
from asaree.services.factorial_analysis import FactorialAnalysisError, analyze_factorial
from asaree.services.factorial_cells import get_cell, list_cells, upsert_cell

router = APIRouter(prefix="/experiments", tags=["experiments"])


class FactorSpec(BaseModel):
    name: str
    levels: list[Any]


class CreateExperimentRequest(BaseModel):
    name: str
    description: str | None = None
    design_type: str = "factorial"
    task_brief: dict[str, Any] | None = None
    factors: list[FactorSpec] | None = None
    # Usable when the dataset is already registered before the experiment is
    # created; the notebook's own flow registers it AFTER (Step 2 follows
    # Step 1), so it attaches this later via PATCH instead — see
    # UpdateExperimentRequest.
    dataset_id: uuid.UUID | None = None


class UpdateExperimentRequest(BaseModel):
    """All fields optional; only the ones actually set are written -- same
    "unset vs. null" convention ``UpsertCellRequest`` uses below. ``name``
    is how the GUI renames an experiment created with a placeholder name
    straight from the Experiments page; ``dataset_id`` (including explicit
    ``null``, to detach) is the notebook's Step 2 attach-after-create flow.
    ``design_spec`` is a full replacement, not a merge -- the protocol
    canvas's "+ Make experimental factor" flow reads the current value,
    upserts-by-name into ``factors`` client-side, and PATCHes the whole
    dict back, same as how ``Protocol.graph`` is PATCHed. ``archived_at``
    (a timestamp to archive, ``null`` to unarchive) is set by the canvas
    menu's Archive/Unarchive action."""

    name: str | None = None
    description: str | None = None
    # Free text, edited from the Design tab -- same "unset vs. null"
    # convention as every other field here.
    hypothesis: str | None = None
    dataset_id: uuid.UUID | None = None
    design_spec: dict[str, Any] | None = None
    archived_at: datetime | None = None


class ExperimentResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    hypothesis: str | None
    design_type: str
    task_brief: dict[str, Any] | None
    design_spec: dict[str, Any] | None
    dataset_id: uuid.UUID | None
    archived_at: datetime | None
    created_at: datetime


def _experiment_response(e: Any) -> ExperimentResponse:
    return ExperimentResponse(
        id=e.id,
        name=e.name,
        description=e.description,
        hypothesis=e.hypothesis,
        design_type=e.design_type,
        task_brief=e.task_brief,
        design_spec=e.design_spec,
        dataset_id=e.dataset_id,
        archived_at=e.archived_at,
        created_at=e.created_at,
    )


async def _validated_dataset_id(dataset_id: uuid.UUID | None, db: DbSession, user: CurrentUser) -> uuid.UUID | None:
    if dataset_id is None:
        return None
    dataset = await get_dataset(db, dataset_id)
    if dataset is None or dataset.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such dataset")
    return dataset_id


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
    if await get_experiment_by_name(db, body.name, owner_id=user.id) is not None:
        raise HTTPException(status_code=409, detail="An experiment with this name already exists")
    dataset_id = await _validated_dataset_id(body.dataset_id, db, user)
    experiment = await create_experiment(
        db,
        name=body.name,
        owner_id=user.id,
        description=body.description,
        design_type=body.design_type,
        task_brief=body.task_brief,
        design_spec={"factors": [f.model_dump() for f in body.factors]} if body.factors else None,
        dataset_id=dataset_id,
    )
    return _experiment_response(experiment)


@router.get("", response_model=list[ExperimentResponse])
async def list_experiments_endpoint(
    user: CurrentUser, db: DbSession, include_archived: bool = False
) -> list[ExperimentResponse]:
    experiments = await list_experiments(db, owner_id=user.id, include_archived=include_archived)
    return [_experiment_response(e) for e in experiments]


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ExperimentResponse:
    experiment = await _get_owned_experiment(db, experiment_id, user)
    return _experiment_response(experiment)


@router.patch("/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment_endpoint(
    experiment_id: uuid.UUID, body: UpdateExperimentRequest, user: CurrentUser, db: DbSession
) -> ExperimentResponse:
    experiment = await _get_owned_experiment(db, experiment_id, user)
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        existing = await get_experiment_by_name(db, fields["name"], owner_id=user.id)
        if existing is not None and existing.id != experiment_id:
            raise HTTPException(status_code=409, detail="An experiment with this name already exists")
    if "dataset_id" in fields:
        fields["dataset_id"] = await _validated_dataset_id(fields["dataset_id"], db, user)
    if fields:
        experiment = await update_experiment(db, experiment_id, fields=fields)
        assert experiment is not None  # existence already checked above
    return _experiment_response(experiment)


@router.delete("/{experiment_id}", status_code=204)
async def delete_experiment_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await _get_owned_experiment(db, experiment_id, user)
    await delete_experiment(db, experiment_id)


@router.post("/{experiment_id}/generate-design", response_model=list[CellResponse])
async def generate_design_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[CellResponse]:
    """Materialize one cell per combination of the experiment's declared
    factors — the cross product, computed fresh each call. Safe to call again
    after widening a factor's levels: existing cells' results are untouched,
    only the new combinations get created (see ``generate_design_cells``)."""
    experiment = await _get_owned_experiment(db, experiment_id, user)
    design_spec = experiment.design_spec or {}
    factors = design_spec.get("factors")
    if not factors:
        raise HTTPException(status_code=422, detail="This experiment has no factors declared (design_spec.factors)")
    try:
        cells = await generate_design_cells(
            db,
            experiment_id=experiment_id,
            factors=factors,
            replicates=design_spec.get("replicates") or 1,
            randomization_seed=design_spec.get("randomization_seed"),
        )
    except DesignValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [CellResponse.model_validate(c) for c in cells]


class AnalyzeFactorialRequest(BaseModel):
    """The spinal_surgery use case's specific methodology (design doc §10) —
    not the generic nonparametric-regression capability tracked separately
    (ASAREE#1). Deliberately explicit rather than inferred: ``positive_levels``
    and ``reference_condition`` are exactly the two things the source notebook
    reads from a manifest instead of guessing, because guessing (e.g. a
    substring match on a model name) can silently invert an effect's sign.
    """

    condition_factors: list[str]
    positive_levels: dict[str, Any]
    reference_condition: dict[str, Any]
    primary_metric: str
    alpha: float = 0.05
    delta: float = 0.05
    n_resamples: int = 10_000
    seed: int = 42
    failure_flag_key: str = "failure_flag"
    cost_keys: list[str] = ["total_tokens", "usd", "wallclock_s"]  # noqa: RUF012


@router.post("/{experiment_id}/analyze")
async def analyze_factorial_endpoint(
    experiment_id: uuid.UUID, body: AnalyzeFactorialRequest, user: CurrentUser, db: DbSession
) -> dict[str, Any]:
    """Failure homogeneity, factorial effects (Freedman-Lane + max-stat FWER),
    estimated marginal means, non-inferiority vs. the reference condition
    (BCa bootstrap + Holm), and heteroscedasticity diagnostics — computed
    fresh from this experiment's current cells, not persisted."""
    await _get_owned_experiment(db, experiment_id, user)
    cells = await list_cells(db, experiment_id=experiment_id)
    try:
        return analyze_factorial(
            cells,
            condition_factors=body.condition_factors,
            positive_levels=body.positive_levels,
            reference_condition=body.reference_condition,
            primary_metric=body.primary_metric,
            alpha=body.alpha,
            delta=body.delta,
            n_resamples=body.n_resamples,
            seed=body.seed,
            failure_flag_key=body.failure_flag_key,
            cost_keys=body.cost_keys,
        )
    except FactorialAnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
