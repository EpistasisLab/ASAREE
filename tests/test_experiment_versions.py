"""Frozen experiment settings and version-aligned analysis projections."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from asaree.api.experiments import _factorial_analysis_selection
from asaree.services.experiment_versions import (
    experiment_settings,
    publication_matches_experiment,
    version_design_spec,
    version_measurement_plan,
)


def test_snapshot_does_not_share_nested_draft_settings():
    experiment = SimpleNamespace(
        hypothesis="Original", design_type="factorial",
        design_spec={"factors": [{"name": "arm", "levels": ["a", "b"]}]},
        measurement_plan={"metrics": [{"name": "Quality"}]},
        task_brief={"reference_values": {"answer": "old"}},
    )
    snapshot = experiment_settings(experiment)
    experiment.design_spec["factors"][0]["levels"].append("c")
    experiment.measurement_plan["metrics"][0]["name"] = "New metric"
    experiment.task_brief["reference_values"]["answer"] = "new"
    publication = SimpleNamespace(experiment_snapshot=snapshot)
    assert version_design_spec(publication, experiment.design_spec)["factors"][0]["levels"] == ["a", "b"]
    assert version_measurement_plan(publication, experiment.measurement_plan)["metrics"][0]["name"] == "Quality"
    assert snapshot["task_brief"]["reference_values"] == {"answer": "old"}


def test_explicit_empty_version_settings_never_fall_back_to_draft():
    publication = SimpleNamespace(experiment_snapshot={"design_spec": None, "measurement_plan": None})
    assert version_design_spec(publication, {"factors": ["new"]}) is None
    assert version_measurement_plan(publication, {"metrics": ["new"]}) is None
    legacy = SimpleNamespace(experiment_snapshot=None)
    assert version_design_spec(legacy, {"replicates": 2}) == {"replicates": 2}


@pytest.mark.asyncio
async def test_design_only_change_marks_experiment_unpublished(monkeypatch):
    experiment = SimpleNamespace(id=uuid.uuid4(), hypothesis="old", design_type="factorial",
                                 design_spec={}, measurement_plan=None, task_brief=None)
    publication = SimpleNamespace(experiment_snapshot=experiment_settings(experiment), design_revision_id=None)
    protocol = SimpleNamespace(experiment_id=experiment.id)
    db = SimpleNamespace(get=AsyncMock(return_value=experiment))
    monkeypatch.setattr("asaree.services.experiment_versions.get_current_revision", AsyncMock(return_value=None))
    assert await publication_matches_experiment(db, protocol, publication)
    experiment.hypothesis = "new"
    assert not await publication_matches_experiment(db, protocol, publication)


@pytest.mark.asyncio
async def test_analysis_reads_selected_attempt_metrics_and_design(monkeypatch):
    version_id = uuid.uuid4()
    projection = {
        "consumption_mode": "whole_dataset", "selected_design_spec": {"metrics": [{"name": "old"}]},
        "replicates": [{"replicate_label": "a", "cell_label": "a", "factor_values": {"arm": "a"},
                        "metric_values": {"old": 0.75}, "run_id": "old-run"}],
    }
    summarize = AsyncMock(return_value=projection)
    monkeypatch.setattr("asaree.api.experiments.summarize_experiment_run_results", summarize)
    experiment = SimpleNamespace(id=uuid.uuid4(), design_spec={"metrics": [{"name": "new"}]})
    selected = await _factorial_analysis_selection(
        None, experiment, protocol_id=None, design_revision_id=None, protocol_revision_id=version_id,
    )
    assert selected["design_spec"] == projection["selected_design_spec"]
    assert selected["replicates"][0].metric_values == {"old": 0.75}
    assert selected["replicates"][0].run_id == "old-run"
    assert summarize.call_args.kwargs["protocol_revision_id"] == version_id
