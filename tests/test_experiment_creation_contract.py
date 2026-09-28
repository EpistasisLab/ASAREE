"""Fast contract coverage for experiment creation side effects."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from asaree.api import experiments as experiments_api
from asaree.services.protocols import generated_protocol_name


async def test_create_endpoint_provisions_linked_empty_canvas(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = uuid.uuid4()
    experiment_id = uuid.uuid4()
    now = datetime.now(UTC)
    experiment = SimpleNamespace(
        id=experiment_id,
        name="SDK experiment",
        description=None,
        hypothesis=None,
        design_type="factorial",
        task_brief=None,
        design_spec=None,
        measurement_plan=None,
        metric_recommendations=None,
        latest_test_run_id=None,
        archived_at=None,
        locked_at=None,
        locked_protocol_revision_id=None,
        locked_design_spec=None,
        locked_measurement_plan=None,
        owner_id=owner_id,
        created_at=now,
        updated_at=now,
    )
    create_protocol = AsyncMock()
    monkeypatch.setattr(experiments_api, "get_experiment_by_name", AsyncMock(return_value=None))
    monkeypatch.setattr(experiments_api, "_validated_dataset_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(experiments_api, "create_experiment", AsyncMock(return_value=experiment))
    monkeypatch.setattr(experiments_api, "create_protocol", create_protocol)
    monkeypatch.setattr(experiments_api, "get_experiment_dataset_ids", AsyncMock(return_value=[]))
    db = object()

    response = await experiments_api.create_experiment_endpoint(
        experiments_api.CreateExperimentRequest(name="SDK experiment"),
        cast(Any, SimpleNamespace(id=owner_id)),
        cast(Any, db),
    )

    assert response.id == experiment_id
    create_protocol.assert_awaited_once_with(
        db,
        name=generated_protocol_name(experiment.name, experiment_id),
        owner_id=owner_id,
        experiment_id=experiment_id,
    )
