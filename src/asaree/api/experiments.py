"""Research experiments and their factorial cell results.

``PUT /experiments/{id}/cells/{cell_label}`` is the one endpoint that replaces
both of the notebook's old ``client.runs.update(mlm_run_id, metadata={...})``
calls — pre-scoring and post-scoring are just two calls to it with different
fields, merged onto the same row (see ``services.factorial_cells.upsert_cell``).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from asaree.deps import CurrentUser, DbSession
from asaree.services.csv_export import cells_that_ran, cells_to_csv
from asaree.services.datasets import get_dataset
from asaree.services.design_generation import DesignValidationError, generate_design_cells, get_design_impact
from asaree.services.design_revisions import (
    DesignRevisionError,
    delete_revision,
    get_revision,
    list_revision_summaries,
)
from asaree.services.experiment_artifacts import create_artifact, delete_artifact, get_artifact, list_artifacts
from asaree.services.experiments import (
    create_experiment,
    create_untitled_experiment,
    delete_experiment,
    get_dataset_ids_by_experiment,
    get_experiment,
    get_experiment_by_name,
    get_experiment_dataset_ids,
    list_experiments,
    set_experiment_datasets,
    update_experiment,
)
from asaree.services.factorial_analysis import FactorialAnalysisError, analyze_experiment_design, analyze_factorial
from asaree.services.factorial_cells import get_cell, list_cells, upsert_cell
from asaree.services.protocol_runs import list_experiment_trials
from asaree.services.protocols import sync_protocol_names_to_experiment

# For a Content-Disposition filename only -- never touches the experiment's
# own stored name, just what the browser offers to save the download as.
_UNSAFE_FILENAME_CHAR = re.compile(r"[^A-Za-z0-9._-]")

router = APIRouter(prefix="/experiments", tags=["experiments"])


class FactorSpec(BaseModel):
    name: str
    levels: list[Any]


class CreateExperimentRequest(BaseModel):
    # Optional on purpose: omit it (or send blank/whitespace) and the server
    # allocates the next free "Untitled Experiment N" itself, atomically. That
    # is what the GUI's one-click create does -- a client cannot pick this name
    # safely, because reading the name list and inserting are two round trips
    # against a namespace other sessions are also writing to. See
    # services.experiments.create_untitled_experiment.
    name: str | None = None
    description: str | None = None
    design_type: str = "factorial"
    task_brief: dict[str, Any] | None = None
    factors: list[FactorSpec] | None = None
    # Usable when the dataset is already registered before the experiment is
    # created; the notebook's own flow registers it AFTER (Step 2 follows
    # Step 1), so it attaches this later via PATCH instead — see
    # UpdateExperimentRequest. ``dataset_ids`` is the real field;
    # ``dataset_id`` is the one-dataset shorthand kept for the SDK/notebook
    # (see _resolved_dataset_ids).
    dataset_ids: list[uuid.UUID] | None = None
    dataset_id: uuid.UUID | None = None


class UpdateExperimentRequest(BaseModel):
    """All fields optional; only the ones actually set are written -- same
    "unset vs. null" convention ``UpsertCellRequest`` uses below. ``name``
    is how the GUI renames an experiment created with a placeholder name
    straight from the Experiments page; ``dataset_ids`` (a full replacement,
    ``[]`` to detach everything) is what the protocol canvas sends whenever
    its set of Dataset nodes changes, and ``dataset_id`` is the same thing
    for exactly one dataset -- the notebook's Step 2 attach-after-create
    flow, unchanged. ``design_spec`` is a full replacement, not a merge --
    the protocol canvas's "+ Make experimental factor" flow reads the current
    value, upserts-by-name into ``factors`` client-side, and PATCHes the
    whole dict back, same as how ``Protocol.graph`` is PATCHed.
    ``archived_at`` (a timestamp to archive, ``null`` to unarchive) is set by
    the canvas menu's Archive/Unarchive action."""

    name: str | None = None
    description: str | None = None
    # Free text, edited from the Design tab -- same "unset vs. null"
    # convention as every other field here.
    hypothesis: str | None = None
    dataset_ids: list[uuid.UUID] | None = None
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
    # Every dataset wired into this experiment's canvas, in wiring order (see
    # models/experiment_dataset.py). ``dataset_id`` is a read-only view of the
    # first one, kept so existing SDK/notebook callers that predate multiple
    # datasets keep working unchanged -- it is NOT a stored column any more.
    dataset_ids: list[uuid.UUID]
    dataset_id: uuid.UUID | None
    archived_at: datetime | None
    created_at: datetime


def _experiment_response(e: Any, dataset_ids: list[uuid.UUID]) -> ExperimentResponse:
    return ExperimentResponse(
        id=e.id,
        name=e.name,
        description=e.description,
        hypothesis=e.hypothesis,
        design_type=e.design_type,
        task_brief=e.task_brief,
        design_spec=e.design_spec,
        dataset_ids=dataset_ids,
        dataset_id=dataset_ids[0] if dataset_ids else None,
        archived_at=e.archived_at,
        created_at=e.created_at,
    )


async def _validated_dataset_ids(dataset_ids: list[uuid.UUID], db: DbSession, user: CurrentUser) -> list[uuid.UUID]:
    for dataset_id in dataset_ids:
        dataset = await get_dataset(db, dataset_id)
        if dataset is None or dataset.owner_id != user.id:
            raise HTTPException(status_code=404, detail="No such dataset")
    return dataset_ids


def _resolved_dataset_ids(fields: dict[str, Any]) -> list[uuid.UUID] | None:
    """The dataset list a request is asking for, or ``None`` to leave the
    experiment's datasets alone.

    ``dataset_ids`` wins when both are given. ``dataset_id`` is the
    one-dataset shorthand: a value means "exactly this one", and an explicit
    ``null`` means "none" -- the same detach it always meant, now expressed as
    emptying the list. *Unset* is what leaves things untouched, which is why
    this reads an ``exclude_unset`` dump rather than the model itself.
    """
    if fields.get("dataset_ids") is not None:
        return list(fields["dataset_ids"])
    if "dataset_id" in fields:
        return [fields["dataset_id"]] if fields["dataset_id"] is not None else []
    return None


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
    # Which generation of the design this cell belongs to. Cells returned by
    # the default (unfiltered) reads are always the current revision's.
    design_revision_id: uuid.UUID
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
    name = (body.name or "").strip()
    if name and await get_experiment_by_name(db, name, owner_id=user.id) is not None:
        raise HTTPException(status_code=409, detail="An experiment with this name already exists")
    dataset_ids = await _validated_dataset_ids(
        _resolved_dataset_ids(body.model_dump(exclude_unset=True)) or [], db, user
    )
    fields: dict[str, Any] = {
        "description": body.description,
        "design_type": body.design_type,
        "task_brief": body.task_brief,
        "design_spec": {"factors": [f.model_dump() for f in body.factors]} if body.factors else None,
        "dataset_ids": dataset_ids,
    }
    # No name given -> the server names it, and the 409 above is unreachable:
    # allocation and insert share this request's transaction, so there is no
    # window for another session to take the name in between.
    experiment = (
        await create_untitled_experiment(db, owner_id=user.id, **fields)
        if not name
        else await create_experiment(db, name=name, owner_id=user.id, **fields)
    )
    return _experiment_response(experiment, await get_experiment_dataset_ids(db, experiment.id))


@router.get("", response_model=list[ExperimentResponse])
async def list_experiments_endpoint(
    user: CurrentUser, db: DbSession, include_archived: bool = False
) -> list[ExperimentResponse]:
    experiments = await list_experiments(db, owner_id=user.id, include_archived=include_archived)
    # One query for every experiment's datasets, not one per experiment.
    by_experiment = await get_dataset_ids_by_experiment(db, [e.id for e in experiments])
    return [_experiment_response(e, by_experiment.get(e.id, [])) for e in experiments]


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ExperimentResponse:
    experiment = await _get_owned_experiment(db, experiment_id, user)
    return _experiment_response(experiment, await get_experiment_dataset_ids(db, experiment_id))


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
    # Datasets are join-table rows, so they're written separately from the
    # plain-column setattr path below -- and popped out of `fields` first, or
    # update_experiment would reject them as not settable.
    requested_dataset_ids = _resolved_dataset_ids(fields)
    fields.pop("dataset_ids", None)
    fields.pop("dataset_id", None)
    if requested_dataset_ids is not None:
        await _validated_dataset_ids(requested_dataset_ids, db, user)
        await set_experiment_datasets(db, experiment_id, requested_dataset_ids)
    if fields:
        experiment = await update_experiment(db, experiment_id, fields=fields)
        assert experiment is not None  # existence already checked above
    if fields.get("name"):
        # A protocol's name is a snapshot of the experiment's name at the
        # moment the canvas created it; re-sync it here, server-side, so an
        # SDK/notebook rename fixes it up too and not just the GUI's.
        await sync_protocol_names_to_experiment(
            db, experiment_id=experiment_id, experiment_name=experiment.name, owner_id=user.id
        )
    return _experiment_response(experiment, await get_experiment_dataset_ids(db, experiment_id))


@router.delete("/{experiment_id}", status_code=204)
async def delete_experiment_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await _get_owned_experiment(db, experiment_id, user)
    await delete_experiment(db, experiment_id)


@router.post("/{experiment_id}/generate-design", response_model=list[CellResponse])
async def generate_design_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[CellResponse]:
    """Materialize one cell per combination of the experiment's declared
    factors — the cross product, computed fresh each call.

    Safe to call again: a design producing the same set of cells merges into
    the current revision, and one producing a different set opens a new
    revision, carrying forward the results of every combination the two share.
    The previous design's cells become history rather than lingering in the
    current view (see ``generate_design_cells``)."""
    experiment = await _get_owned_experiment(db, experiment_id, user)
    design_spec = experiment.design_spec or {}
    factors = design_spec.get("factors") or []
    try:
        cells = await generate_design_cells(
            db,
            experiment_id=experiment_id,
            factors=factors,
            replicates=design_spec.get("replicates") or 1,
            randomization_seed=design_spec.get("randomization_seed"),
            design_spec=experiment.design_spec,
        )
    except DesignValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [CellResponse.model_validate(c) for c in cells]


class DesignRevisionResponse(BaseModel):
    id: uuid.UUID
    revision: int
    # Null = this is the experiment's current design; a timestamp = when it
    # was replaced. The frontend keys "current vs history" off this.
    superseded_at: datetime | None
    # The design_spec snapshot that produced this revision, so a superseded
    # revision's numbers stay interpretable after the experiment's own spec
    # has moved on.
    design_spec: dict[str, Any] | None
    cell_count: int
    scored_count: int
    created_at: datetime


class DesignImpactResponse(BaseModel):
    has_generated_design: bool
    regeneration_required: bool
    current_cell_count: int
    proposed_cell_count: int
    added_count: int
    retained_count: int
    removed_count: int


@router.get("/{experiment_id}/design-impact", response_model=DesignImpactResponse)
async def design_impact_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DesignImpactResponse:
    """Preview the cell-set change regeneration would make, without writing it."""
    experiment = await _get_owned_experiment(db, experiment_id, user)
    try:
        impact = await get_design_impact(db, experiment_id=experiment_id, design_spec=experiment.design_spec)
    except DesignValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DesignImpactResponse(**impact.__dict__)


@router.get("/{experiment_id}/design-revisions", response_model=list[DesignRevisionResponse])
async def list_design_revisions_endpoint(
    experiment_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[DesignRevisionResponse]:
    """Every generation of this experiment's design, current one first."""
    await _get_owned_experiment(db, experiment_id, user)
    summaries = await list_revision_summaries(db, experiment_id=experiment_id)
    return [
        DesignRevisionResponse(
            id=s.revision.id,
            revision=s.revision.revision,
            superseded_at=s.revision.superseded_at,
            design_spec=s.revision.design_spec,
            cell_count=s.cell_count,
            scored_count=s.scored_count,
            created_at=s.revision.created_at,
        )
        for s in summaries
    ]


@router.delete("/{experiment_id}/design-revisions/{revision_id}", status_code=204)
async def delete_design_revision_endpoint(
    experiment_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    """Permanently delete a superseded design and every cell under it.

    409 for the current design: replacing it is what generate-design does, and
    deleting it would leave the experiment with no design at all. 404 if the
    revision belongs to a different experiment, so a revision id from one
    experiment can't be used to delete through another."""
    await _get_owned_experiment(db, experiment_id, user)
    revision = await get_revision(db, revision_id)
    if revision is None or revision.experiment_id != experiment_id:
        raise HTTPException(status_code=404, detail="No such design revision")
    try:
        await delete_revision(db, revision_id)
    except DesignRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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


class ResultsResponse(BaseModel):
    available: bool
    # Set only when available is False -- why there's nothing to show yet
    # (no factors/primary metric declared, a factor with other than 2
    # levels, or not enough scored replicates), surfaced as one consistent
    # state for the Results tab regardless of which precondition failed.
    reason: str | None
    analysis: dict[str, Any] | None
    # analysis["emm_cells"], picked by the declared primary metric's own
    # direction -- analyze_factorial itself has no notion of "best," only
    # non-inferiority vs. a reference condition.
    best_condition: dict[str, Any] | None


@router.get("/{experiment_id}/results", response_model=ResultsResponse)
async def get_experiment_results_endpoint(
    experiment_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ResultsResponse:
    """See services.factorial_analysis.analyze_experiment_design -- this
    endpoint is a thin pass-through, all the real derivation/wrapping logic
    lives there so it's unit-testable without a request/response cycle."""
    experiment = await _get_owned_experiment(db, experiment_id, user)
    cells = await list_cells(db, experiment_id=experiment_id)
    result = analyze_experiment_design(experiment.design_spec, cells)
    return ResultsResponse(**result)


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
async def list_cells_endpoint(
    experiment_id: uuid.UUID, user: CurrentUser, db: DbSession, revision_id: uuid.UUID | None = None
) -> list[CellResponse]:
    """The current design's cells. Pass ``revision_id`` to read a superseded
    design's instead -- see GET /design-revisions for the ids."""
    await _get_owned_experiment(db, experiment_id, user)
    cells = await list_cells(db, experiment_id=experiment_id, revision_id=revision_id)
    return [CellResponse.model_validate(c) for c in cells]


@router.get("/{experiment_id}/cells.csv")
async def export_cells_csv_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """One row per cell that's actually run, one column per factor_values/
    metric_values key seen across them -- see services.csv_export
    (cells_that_ran / cells_to_csv)."""
    experiment = await _get_owned_experiment(db, experiment_id, user)
    cells = await list_cells(db, experiment_id=experiment_id)
    csv_text = cells_to_csv(cells_that_ran(cells))
    filename = _UNSAFE_FILENAME_CHAR.sub("_", experiment.name.strip()) or "experiment"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}-cells.csv"'},
    )


class CreateArtifactRequest(BaseModel):
    name: str
    kind: str
    content: str


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    name: str
    kind: str
    content: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.post("/{experiment_id}/artifacts", response_model=ArtifactResponse, status_code=201)
async def create_artifact_endpoint(
    experiment_id: uuid.UUID, body: CreateArtifactRequest, user: CurrentUser, db: DbSession
) -> ArtifactResponse:
    """A durable landing spot for anything a use case wants to keep past one
    run -- an ``analyze`` snapshot, a CSV export, or anything else -- create-
    once/append-style, never an upsert target the way a cell is (see
    ExperimentArtifact's own docstring)."""
    await _get_owned_experiment(db, experiment_id, user)
    artifact = await create_artifact(
        db, experiment_id=experiment_id, name=body.name, kind=body.kind, content=body.content
    )
    return ArtifactResponse.model_validate(artifact)


@router.get("/{experiment_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts_endpoint(experiment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[ArtifactResponse]:
    await _get_owned_experiment(db, experiment_id, user)
    artifacts = await list_artifacts(db, experiment_id=experiment_id)
    return [ArtifactResponse.model_validate(a) for a in artifacts]


@router.get("/{experiment_id}/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact_endpoint(
    experiment_id: uuid.UUID, artifact_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ArtifactResponse:
    await _get_owned_experiment(db, experiment_id, user)
    artifact = await get_artifact(db, experiment_id=experiment_id, artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="No such artifact")
    return ArtifactResponse.model_validate(artifact)


@router.delete("/{experiment_id}/artifacts/{artifact_id}", status_code=204)
async def delete_artifact_endpoint(
    experiment_id: uuid.UUID, artifact_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    await _get_owned_experiment(db, experiment_id, user)
    artifact = await get_artifact(db, experiment_id=experiment_id, artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="No such artifact")
    await delete_artifact(db, artifact_id=artifact_id)


# "pending" is ProtocolRun's own internal vocabulary -- the Runs tab calls a
# submitted-but-not-started run "queued". A cell with no run at all is kept
# distinct as "not_started" (see ExperimentTrial's docstring).
_RUN_STATUS_TO_TRIAL_STATUS = {"pending": "queued"}


class TrialResponse(BaseModel):
    cell_label: str
    factor_values: dict[str, Any]
    metric_values: dict[str, Any]
    status: str
    run_id: uuid.UUID | None
    error: str | None
    updated_at: datetime


@router.get("/{experiment_id}/runs", response_model=list[TrialResponse])
async def list_experiment_trials_endpoint(
    experiment_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[TrialResponse]:
    """One row per cell (a "trial" -- a factor-level combination x replicate),
    not per ProtocolRun -- a cell that's never been run is still a trial
    (status "not_started"), which listing ProtocolRuns alone would miss. See
    services.protocol_runs.list_experiment_trials."""
    await _get_owned_experiment(db, experiment_id, user)
    trials = await list_experiment_trials(db, experiment_id=experiment_id)
    return [
        TrialResponse(
            cell_label=t.cell_label,
            factor_values=t.factor_values,
            metric_values=t.metric_values,
            status=_RUN_STATUS_TO_TRIAL_STATUS.get(t.status, t.status),
            run_id=t.run_id,
            error=t.error,
            updated_at=t.updated_at,
        )
        for t in trials
    ]
