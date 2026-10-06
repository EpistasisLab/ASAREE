"""Row executions remain inspectable but never enter factorial analysis."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from asaree.api.experiments import (
    export_replicates_csv_endpoint,
    get_experiment_results_endpoint,
)
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.services.factorial_analysis import FactorialAnalysisError, analyze_experiment_design

pytest_plugins = ("test_dataset_row_results_projection",)


@pytest.mark.asyncio
async def test_published_row_protocol_disables_results_without_calling_analyzer(row_results_setup, monkeypatch) -> None:
    db, ctx = row_results_setup
    ctx["experiment"].design_spec = {
        "factors": [{"name": "prompt", "levels": ["a", "b", "c", "d", "e"]}],
        "metrics": [{"name": "Report", "primary": True, "direction": "maximize"}],
    }
    ctx["design_revision"].design_spec = ctx["experiment"].design_spec

    def unexpected(*_args, **_kwargs):
        raise AssertionError("numerical analyzer must not run for row mode")

    monkeypatch.setattr("asaree.services.factorial_analysis.analyze_factorial", unexpected)
    result = await get_experiment_results_endpoint(
        ctx["experiment"].id, ctx["user"], db, protocol_id=ctx["protocol"].id
    )
    assert result.model_dump() == {
        "available": False,
        "reason": "Per-row executions are available for inspection and export; factorial analysis is not supported.",
        "analysis": None,
        "best_condition": None,
    }


def test_row_result_input_is_rejected_under_whole_dataset() -> None:
    row_result = FactorialRowResult(row_index=0)
    with pytest.raises(FactorialAnalysisError, match="row execution"):
        analyze_experiment_design(
            {"factors": [{"name": "prompt", "levels": ["a", "b"]}], "metrics": []},
            [row_result],
        )


def test_per_row_custom_json_is_opaque_and_analysis_stays_unavailable() -> None:
    opaque = {"score": 9, "passed": True}
    result = analyze_experiment_design(
        {"factors": [{"name": "prompt", "levels": ["a", "b"]}], "metrics": []},
        [{"row_result_id": "row-1", "metric_values": {"Report": opaque}}],
        consumption_mode="per_row",
    )
    assert result["available"] is False
    assert result["analysis"] is None
    assert opaque == {"score": 9, "passed": True}


@pytest.mark.asyncio
async def test_stale_parent_metric_does_not_rank_row_mode_and_legacy_export_is_blocked(row_results_setup) -> None:
    db, ctx = row_results_setup
    ctx["experiment"].design_spec = {
        "factors": [{"name": "arm", "levels": ["left", "right"]}],
        "metrics": [{"name": "Report", "primary": True, "direction": "maximize"}],
    }
    for parent in ctx["parents"]:
        parent.metric_values = {"Report": 1000}
    results = await get_experiment_results_endpoint(
        ctx["experiment"].id, ctx["user"], db, protocol_id=ctx["protocol"].id
    )
    assert results.available is False and results.best_condition is None
    with pytest.raises(HTTPException) as caught:
        await export_replicates_csv_endpoint(
            ctx["experiment"].id, ctx["user"], db, protocol_id=ctx["protocol"].id
        )
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "use_row_results_export"
    assert caught.value.detail["link_text"] == "run-results.csv"


@pytest.mark.asyncio
async def test_historical_whole_publication_ignores_later_draft_scores(row_results_setup) -> None:
    db, ctx = row_results_setup
    ctx["protocol"].graph = {"nodes": [{"id": "agent", "type": "agent", "data": {}}], "edges": []}
    from asaree.services.protocol_revisions import publish_protocol

    whole = await publish_protocol(db, ctx["protocol"], owner_id=ctx["user"].id)
    ctx["experiment"].design_spec = {
        "factors": [{"name": "arm", "levels": ["left", "right"]}],
        "metrics": [{"name": "Report", "primary": True, "direction": "maximize"}],
    }
    for parent in ctx["parents"]:
        parent.metric_values = {"Report": 0.7}
    for cell in ctx["cells"]:
        db.add(FactorialReplicateResult(
            cell_id=cell.id,
            replicate_number=2,
            replicate_label=f"historical-{cell.cell_label}",
            metric_values={"Report": 0.71},
        ))
    # Draft is switched back to row mode after publishing; the explicit whole
    # publication remains authoritative for both legacy projections.
    ctx["protocol"].graph = {
        "nodes": [{"id": "dataset", "type": "dataset", "data": {"config": {"dataset_id": str(ctx["dataset"].id)}}}],
        "edges": [],
    }
    analyzed = await get_experiment_results_endpoint(
        ctx["experiment"].id, ctx["user"], db,
        protocol_id=ctx["protocol"].id, protocol_revision_id=whole.id,
    )
    # This publication captured no factorial declaration. Later draft edits
    # and parent scores cannot manufacture historical analysis or results.
    assert analyzed.available is False
    exported = await export_replicates_csv_endpoint(
        ctx["experiment"].id, ctx["user"], db,
        protocol_id=ctx["protocol"].id, protocol_revision_id=whole.id,
    )
    assert exported.media_type == "text/csv"
    assert "0.71" not in exported.body.decode()
