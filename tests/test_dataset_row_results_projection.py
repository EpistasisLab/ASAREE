"""PostgreSQL Results projection coverage for immutable dataset row slots."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import asaree.api.protocols as protocol_api
from asaree.api.experiments import get_experiment_run_results_endpoint
from asaree.api.protocols import CellRunBatchRequest, create_cell_runs_endpoint
from asaree.models.dataset import RegisteredDataset
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol_run import ProtocolRun
from asaree.services.design_revisions import get_or_create_current
from asaree.services.experiment_run_results import RunResultsProjectionError, summarize_experiment_run_results
from asaree.services.experiments import create_experiment
from asaree.services.factorial_row_results import claim_row_attempt
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocols import create_protocol
from asaree.services.users import get_user_by_email


@pytest_asyncio.fixture
async def row_results_setup(tmp_path: Path) -> AsyncIterator[tuple[AsyncSession, dict]]:
    """Use the seeded application user and a real hash-verified CSV source."""
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            sessions = async_sessionmaker(bind=connection, expire_on_commit=False)
            async with sessions() as db:
                user = await get_user_by_email(db, "test@test.com")
                assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
                raw = b"question,answer\n" + b"prompt,response\n" * 15
                source_path = tmp_path / "results.csv"
                source_path.write_bytes(raw)
                dataset = RegisteredDataset(
                    name=f"results-{uuid.uuid4()}",
                    owner_id=user.id,
                    raw_path=str(source_path),
                    raw_sha256=hashlib.sha256(raw).hexdigest(),
                )
                db.add(dataset)
                await db.flush()
                design_spec = {
                    "factors": [],
                    "replicates": 1,
                    "metrics": [
                        {"id": "reported", "name": "Report", "kind": "custom", "valueType": "opaque"}
                    ],
                }
                experiment = await create_experiment(
                    db,
                    name=f"row-results-{uuid.uuid4()}",
                    owner_id=user.id,
                    design_spec=design_spec,
                    measurement_plan={
                        "metrics": [{"id": "reported", "name": "Report"}],
                        "producers": [
                            {
                                "id": "report-output",
                                "producer_id": "asaree.agent_output",
                                "kind": "reported",
                                "outputs": {"value": "reported"},
                                "config": {"agent_node_id": "agent"},
                            }
                        ],
                        "inputs": [],
                    },
                )
                design_revision = await get_or_create_current(db, experiment.id, design_spec=design_spec)
                parents = []
                cells = []
                for arm in ("left", "right"):
                    cell = FactorialCell(
                        experiment_id=experiment.id,
                        design_revision_id=design_revision.id,
                        cell_label=f"arm={arm}",
                        factor_values={"arm": arm},
                    )
                    db.add(cell)
                    await db.flush()
                    parent = FactorialReplicateResult(
                        cell_id=cell.id,
                        replicate_number=1,
                        replicate_label="duplicate-slot-label",
                    )
                    db.add(parent)
                    await db.flush()
                    cells.append(cell)
                    parents.append(parent)
                graph = {
                    "nodes": [
                        {
                            "id": "dataset",
                            "type": "dataset",
                            "data": {"config": {"dataset_id": str(dataset.id), "dataset_name": dataset.name}},
                        },
                        {"id": "agent", "type": "agent", "data": {}},
                        {"id": "model", "type": "model_openai", "data": {"config": {}}},
                    ],
                    "edges": [
                        {
                            "id": "dataset-agent",
                            "source": "dataset",
                            "target": "agent",
                            "targetHandle": "dataset",
                            "data": {"dataset_input": {"mode": "per_row", "columns": ["question"]}},
                        },
                        {"id": "model-agent", "source": "model", "target": "agent", "targetHandle": "model"},
                    ],
                }
                protocol = await create_protocol(
                    db,
                    name=f"row-results-protocol-{uuid.uuid4()}",
                    owner_id=user.id,
                    experiment_id=experiment.id,
                    graph=graph,
                )
                publication = await publish_protocol(db, protocol, owner_id=user.id)
                protocol.published_revision_id = publication.id
                await db.flush()
                context = {
                    "user": user,
                    "experiment": experiment,
                    "dataset": dataset,
                    "source_path": source_path,
                    "protocol": protocol,
                    "publication": publication,
                    "design_revision": design_revision,
                    "parents": parents,
                    "cells": cells,
                    "design_spec": design_spec,
                }
                yield db, context
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_row_projection_counts_attempts_and_preserves_slots(row_results_setup, monkeypatch) -> None:
    db, ctx = row_results_setup

    async def no_enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", no_enqueue)
    planned = await create_cell_runs_endpoint(
        ctx["protocol"].id, ctx["user"], db, CellRunBatchRequest()
    )
    assert len(planned.row_result_ids) == 30
    runs = [await db.get(ProtocolRun, run_id) for run_id in planned.protocol_run_ids]
    slots = [await db.get(FactorialRowResult, row_id) for row_id in planned.row_result_ids]
    assert all(run is not None for run in runs) and all(slot is not None for slot in slots)

    for index, (run, slot) in enumerate(zip(runs, slots, strict=True)):
        assert run is not None and slot is not None
        workspace_id = f"_protocol_runs/{run.id}"
        workspace = ctx["source_path"].parent / "workspaces" / str(run.id)
        workspace.mkdir(parents=True)
        (workspace / "row.txt").write_text(f"row attempt {index}", encoding="utf-8")
        slot.workspace_id = workspace_id
        if index == 1:
            run.status = "running"
        elif index == 2:
            run.status = "finalizing"
        elif index == 29:
            run.status = "failed"
            run.error = "failed attempt"
        elif index >= 3:
            run.status = "completed"
            metric_status = "unavailable" if index == 3 else "measured"
            value = None if index == 4 else {"opaque": [index, None]} if index == 5 else index
            run.attempt_result = {
                "measurement": {
                    "observations": [
                        {"metric_id": "reported", "status": metric_status, "value": value}
                    ]
                }
            }
            if metric_status == "measured":
                slot.metric_values = {"Report": value}

    results = await summarize_experiment_run_results(
        db, experiment_id=ctx["experiment"].id, design_spec=ctx["design_spec"]
    )
    summary = results["row_summary"]
    assert results["consumption_mode"] == "per_row"
    assert results["cells"] == [] and results["replicates"] == []
    assert results["primary_metric"] is None and results["primary_metric_direction"] is None
    assert len(results["row_results"]) == 30
    assert summary == {
        "cell_count": 2,
        "parent_replicate_count": 2,
        "row_count": 15,
        "expected": 30,
        "planned": 30,
        "pending": 1,
        "running": 2,
        "completed": 26,
        "failed": 1,
        "cancelled": 0,
        "scored": 25,
        "missing_reported": 1,
        "metric_coverage": {
            "reported": {"measured": 25, "unavailable": 5, "failed": 0, "cancelled": 0, "other": 0}
        },
    }
    duplicate_label_rows = [row for row in results["row_results"] if row["replicate_label"] == "duplicate-slot-label"]
    assert len({row["replicate_result_id"] for row in duplicate_label_rows}) == 2
    # Planning claims slots in UUID order; Results orders by cell/replicate/row.
    # Match persisted slot identities rather than assuming those orders agree.
    rows_by_id = {row["row_result_id"]: row for row in results["row_results"]}
    assert set(rows_by_id) == {str(slot.id) for slot in slots}
    null_row = rows_by_id[str(slots[4].id)]
    opaque_row = rows_by_id[str(slots[5].id)]
    assert null_row["metric_values"] == {"Report": None}
    assert opaque_row["metric_values"] == {"Report": {"opaque": [5, None]}}
    assert null_row["latest_attempt"]["run_id"] == str(runs[4].id)
    assert null_row["latest_attempt"]["workspace_id"] == null_row["workspace_id"]

    # Retry one failed row through the production claim API; history is immutable
    # and only the run still attached to the stable slot is current.
    failed_run = runs[29]
    failed_slot = slots[29]
    assert failed_run is not None and failed_slot is not None
    retry = await claim_row_attempt(
        db,
        row_result_id=failed_slot.id,
        expected_run_id=failed_run.id,
        create_kwargs={
            "protocol_id": ctx["protocol"].id,
            "owner_id": ctx["user"].id,
            "replicate_label": "duplicate-slot-label",
            "factor_values": {"arm": "right"},
            "replicate_result_id": failed_slot.replicate_result_id,
            "design_revision_id": ctx["design_revision"].id,
            "protocol_revision_id": ctx["publication"].id,
            "row_result_id": failed_slot.id,
            "dataset_row": failed_run.dataset_row,
        },
    )
    assert retry is not None
    retried = await summarize_experiment_run_results(
        db, experiment_id=ctx["experiment"].id, design_spec=ctx["design_spec"]
    )
    retried_slot = next(row for row in retried["row_results"] if row["row_result_id"] == str(failed_slot.id))
    assert [attempt["current"] for attempt in retried_slot["attempts"]] == [False, True]
    assert retried_slot["latest_attempt"]["run_id"] == str(retry.id)
    assert retried["row_summary"]["failed"] == 0
    assert retried["row_summary"]["pending"] == 2

    # A removed source never makes persisted slot history disappear.
    ctx["source_path"].unlink()
    source_missing = await summarize_experiment_run_results(
        db, experiment_id=ctx["experiment"].id, design_spec=ctx["design_spec"]
    )
    assert source_missing["row_summary"]["row_count"] is None
    assert source_missing["row_summary"]["expected"] is None
    assert len(source_missing["row_results"]) == 30

    no_metrics = await summarize_experiment_run_results(
        db, experiment_id=ctx["experiment"].id, design_spec={"metrics": []}
    )
    assert no_metrics["row_summary"]["scored"] == 0
    assert no_metrics["row_summary"]["metric_coverage"] == {}


@pytest.mark.asyncio
async def test_row_results_selectors_forecast_and_whole_scorecard(row_results_setup, monkeypatch) -> None:
    db, ctx = row_results_setup
    # A protocol revision and design revision are scoped to this experiment.
    other_user = await get_user_by_email(db, "other@test.com")
    assert other_user is not None, "run scripts/seed_row_test_users.py before row database tests"
    other = await create_protocol(
        db,
        name=f"foreign-results-{uuid.uuid4()}",
        owner_id=other_user.id,
        graph={"nodes": [], "edges": []},
    )
    foreign_experiment = await create_experiment(
        db,
        name=f"foreign-results-experiment-{uuid.uuid4()}",
        owner_id=other_user.id,
    )
    other.experiment_id = foreign_experiment.id
    foreign_publication = await publish_protocol(db, other)
    with pytest.raises(RunResultsProjectionError, match="protocol_revision_not_found"):
        await summarize_experiment_run_results(
            db,
            experiment_id=ctx["experiment"].id,
            design_spec=ctx["design_spec"],
            protocol_revision_id=foreign_publication.id,
        )
    with pytest.raises(HTTPException) as foreign_publication_response:
        await get_experiment_run_results_endpoint(
            ctx["experiment"].id,
            ctx["user"],
            db,
            protocol_id=ctx["protocol"].id,
            protocol_revision_id=foreign_publication.id,
        )
    assert foreign_publication_response.value.status_code == 404
    foreign_design = await get_or_create_current(db, foreign_experiment.id)
    with pytest.raises(RunResultsProjectionError, match="design_revision_not_found"):
        await summarize_experiment_run_results(
            db,
            experiment_id=ctx["experiment"].id,
            design_spec=ctx["design_spec"],
            design_revision_id=foreign_design.id,
        )
    with pytest.raises(HTTPException) as foreign_design_response:
        await get_experiment_run_results_endpoint(
            ctx["experiment"].id,
            ctx["user"],
            db,
            design_revision_id=foreign_design.id,
        )
    assert foreign_design_response.value.status_code == 404

    await create_protocol(
        db,
        name=f"ambiguous-{uuid.uuid4()}",
        owner_id=ctx["user"].id,
        experiment_id=ctx["experiment"].id,
    )
    with pytest.raises(HTTPException) as ambiguous:
        await get_experiment_run_results_endpoint(ctx["experiment"].id, ctx["user"], db)
    assert ambiguous.value.status_code == 422
    assert ambiguous.value.detail == "ambiguous_protocol"

    # The current draft is irrelevant: the selected publication controls mode.
    ctx["protocol"].graph = {"nodes": [], "edges": []}
    per_row = await summarize_experiment_run_results(
        db,
        experiment_id=ctx["experiment"].id,
        design_spec=ctx["design_spec"],
        protocol_id=ctx["protocol"].id,
        protocol_revision_id=ctx["publication"].id,
    )
    assert per_row["consumption_mode"] == "per_row"

    async def no_enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", no_enqueue)
    await create_cell_runs_endpoint(
        ctx["protocol"].id, ctx["user"], db, CellRunBatchRequest()
    )

    # Publishing a whole-dataset canvas exposes the legacy scorecard shape and
    # row attempts stay out of its label-based history.
    ctx["protocol"].graph = {"nodes": [{"id": "agent", "type": "agent", "data": {}}], "edges": []}
    whole_publication = await publish_protocol(db, ctx["protocol"], owner_id=ctx["user"].id)
    whole = await get_experiment_run_results_endpoint(
        ctx["experiment"].id, ctx["user"], db, protocol_id=ctx["protocol"].id
    )
    assert whole.consumption_mode == "whole_dataset"
    assert whole.row_summary is None and whole.row_results == []
    assert whole.cells and whole.replicates
    assert whole_publication.id == ctx["protocol"].published_revision_id


@pytest.mark.asyncio
async def test_preplanning_forecast_uses_verified_source(row_results_setup) -> None:
    db, ctx = row_results_setup
    # Remove the planned cells, but retain the declaration and verified source.
    for cell in ctx["cells"]:
        await db.delete(cell)
    ctx["experiment"].design_spec = {
        "factors": [{"name": "arm", "levels": ["left", "right"]}],
        "replicates": 3,
        "metrics": [],
    }
    ctx["design_revision"].design_spec = ctx["experiment"].design_spec
    forecast = await summarize_experiment_run_results(
        db,
        experiment_id=ctx["experiment"].id,
        design_spec=ctx["experiment"].design_spec,
    )
    assert forecast["row_summary"]["planned"] == 0
    assert forecast["row_summary"]["cell_count"] == 0
    assert forecast["row_summary"]["parent_replicate_count"] == 0
    assert forecast["row_summary"]["expected"] == 90
