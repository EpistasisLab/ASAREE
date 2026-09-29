"""Fast contract coverage for experiment creation side effects."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from asaree.api import experiments as experiments_api
from asaree.services.protocols import generated_protocol_name


class _NestedTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeDb:
    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction()


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


async def test_import_definition_localizes_mcp_server_ids_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = uuid.uuid4()
    experiment_id = uuid.uuid4()
    source_server_id = uuid.uuid4()
    local_server_id = uuid.uuid4()
    now = datetime.now(UTC)
    experiment = SimpleNamespace(
        id=experiment_id,
        name="Portable experiment",
        description=None,
        hypothesis=None,
        design_type="factorial",
        task_brief=None,
        design_spec={"metrics": []},
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
    graph = {
        "nodes": [
            {"id": "agent", "type": "agent", "data": {"label": "Agent"}},
            {
                "id": "tool",
                "type": "mcp_tool",
                "data": {
                    "label": "Scorer",
                    "config": {
                        "server_id": str(source_server_id),
                        "server_name": "asaree-sklearn-model",
                        "tool_names": ["run_model_script"],
                        "enabled": True,
                    },
                },
            },
        ],
        "edges": [{"source": "tool", "target": "agent", "targetHandle": "tool"}],
    }
    measurement_plan = {
        "metrics": [
            {
                "id": "model-evaluation",
                "name": "Model evaluation",
                "value_type": "opaque",
                "direction": "neutral",
                "aggregation": "none",
                "primary": False,
            }
        ],
        "producers": [
            {
                "id": "model-evaluation-source",
                "producer_id": "asaree.mcp_tool",
                "kind": "reported",
                "outputs": {"value": "model-evaluation"},
                "artifacts": [],
                "config": {
                    "agent_node_id": "agent",
                    "mcp_node_id": "tool",
                    "server_id": str(source_server_id),
                    "tool_name": "run_model_script",
                },
            }
        ],
        "inputs": [],
    }
    experiment.measurement_plan = measurement_plan

    async def require_local_server(_db: object, **kwargs: Any) -> None:
        imported_graph = kwargs["graph"]
        imported_plan = kwargs["document"]
        node_server_id = imported_graph["nodes"][1]["data"]["config"]["server_id"]
        producer_server_id = imported_plan["producers"][0]["config"]["server_id"]
        if node_server_id != str(local_server_id) or producer_server_id != str(local_server_id):
            raise HTTPException(status_code=422, detail="The registered MCP Server is unavailable.")

    async def create_imported_experiment(_db: object, **kwargs: Any) -> SimpleNamespace:
        experiment.measurement_plan = kwargs["measurement_plan"]
        return experiment

    monkeypatch.setattr(experiments_api, "get_experiment_by_name", AsyncMock(return_value=None))
    monkeypatch.setattr(experiments_api, "create_experiment", create_imported_experiment)
    monkeypatch.setattr(experiments_api, "create_protocol", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(experiments_api, "_require_valid_measurement_plan", require_local_server)
    monkeypatch.setattr(experiments_api, "get_experiment_dataset_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        experiments_api.mcp_service,
        "list_servers",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=local_server_id,
                    name="asaree-sklearn-model",
                    owner_id=None,
                    is_system=True,
                )
            ]
        ),
    )
    response = await experiments_api.import_experiment_definition_endpoint(
        experiments_api.ImportExperimentDefinitionRequest(
            name="Portable experiment",
            design_spec={"metrics": []},
            measurement_plan=measurement_plan,
            graph=graph,
        ),
        cast(Any, SimpleNamespace(id=owner_id)),
        cast(Any, _FakeDb()),
    )

    assert response.id == experiment_id
