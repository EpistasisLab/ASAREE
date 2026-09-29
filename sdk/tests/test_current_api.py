"""Regression coverage for API features added after the original trimmed SDK."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from asaree_client.models import Experiment, RegisteredDataset, Trial
from asaree_client.resources.experiments import Experiments
from asaree_client.resources.protocols import Protocols


class RecordingClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        return self.response

    def _get(self, path: str, **kwargs: Any) -> Any:
        return self._record("GET", path, **kwargs)

    def _post(self, path: str, **kwargs: Any) -> Any:
        return self._record("POST", path, **kwargs)

    def _patch(self, path: str, **kwargs: Any) -> Any:
        return self._record("PATCH", path, **kwargs)


def _experiment_response() -> dict[str, Any]:
    now = datetime.now(tz=UTC).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "name": "current",
        "description": None,
        "hypothesis": None,
        "design_type": "factorial",
        "task_brief": None,
        "design_spec": None,
        "measurement_plan": None,
        "dataset_ids": [],
        "dataset_id": None,
        "archived_at": None,
        "locked_at": None,
        "locked_protocol_revision_id": None,
        "locked_design_spec": None,
        "locked_measurement_plan": None,
        "created_at": now,
        "updated_at": now,
    }


def test_current_experiment_response_parses_lock_and_recommendation_state() -> None:
    now = datetime.now(tz=UTC)
    experiment = Experiment(
        id=uuid.uuid4(),
        name="current",
        description=None,
        hypothesis=None,
        design_type="factorial",
        task_brief=None,
        design_spec=None,
        measurement_plan=None,
        metric_recommendations={"applied_version": 2},
        metric_recommendation_set_version=2,
        latest_test_run_id=uuid.uuid4(),
        dataset_ids=[],
        dataset_id=None,
        archived_at=None,
        locked_at=now,
        locked_protocol_revision_id=uuid.uuid4(),
        locked_design_spec={"replicates": 3},
        locked_measurement_plan=None,
        created_at=now,
        updated_at=now,
    )

    assert experiment.locked_at == now
    assert experiment.metric_recommendation_set_version == 2


def test_current_dataset_response_parses_split_provenance() -> None:
    dataset = RegisteredDataset(
        id=uuid.uuid4(),
        name="cohort",
        target_column="outcome",
        split_method="quick",
        split_group_column="patient_id",
        split_test_size=0.25,
        split_seed=42,
    )

    assert dataset.split_group_column == "patient_id"
    assert dataset.split_seed == 42


def test_trial_parses_truncated_runs() -> None:
    trial = Trial(
        replicate_label="cell-1-r1",
        factor_values={},
        metric_values={},
        status="completed",
        run_id=uuid.uuid4(),
        obsolete=False,
        truncated=True,
        error=None,
        updated_at=datetime.now(tz=UTC),
    )

    assert trial.truncated is True


def test_experiment_update_sends_new_measurement_fields() -> None:
    client = RecordingClient(response=_experiment_response())
    protocol_id = uuid.uuid4()

    Experiments(client).update(
        "experiment-id",
        measurement_plan=None,
        measurement_validation_protocol_id=protocol_id,
        metric_recommendations={"dismissed_version": 2},
    )

    assert client.calls == [
        (
            "PATCH",
            "/experiments/experiment-id",
            {
                "json": {
                    "measurement_plan": None,
                    "measurement_validation_protocol_id": str(protocol_id),
                    "metric_recommendations": {"dismissed_version": 2},
                }
            },
        )
    ]


def test_run_cells_serializes_scoped_reruns() -> None:
    client = RecordingClient(
        response={
            "protocol_run_ids": [],
            "replicate_labels": [],
            "skipped": 0,
            "protocol_revision_id": str(uuid.uuid4()),
            "protocol_revision": 1,
        }
    )

    Protocols(client).run_cells(
        "protocol-id",
        replicate_labels=["cell-1-r1"],
        rerun_replicate_labels=["cell-1-r1"],
    )

    assert client.calls == [
        (
            "POST",
            "/protocols/protocol-id/cell-runs",
            {
                "json": {
                    "replicate_labels": ["cell-1-r1"],
                    "rerun_replicate_labels": ["cell-1-r1"],
                }
            },
        )
    ]
