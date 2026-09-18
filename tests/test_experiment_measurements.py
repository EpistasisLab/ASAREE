from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.api import experiments as experiments_api
from asaree.services import experiment_measurements
from asaree.services.measurement_engine import ValidationIssue, ValidationReport, parse_measurement_plan


def test_capability_loss_is_readiness_not_a_blocking_plan_error() -> None:
    plan = parse_measurement_plan(_python_metric_document())
    report = ValidationReport(
        (
            ValidationIssue(
                "python_script_node_missing",
                "The Python Script is unavailable.",
                "producers[0].config.script_node_id",
                "needs_attention",
            ),
        )
    )

    assert experiment_measurements.blocking_measurement_plan_issues(report) == ()
    assert experiment_measurements.unavailable_producer_reasons(plan, report) == {
        "python-quality": {"*": "The Python Script is unavailable."}
    }


def test_malformed_reported_source_configuration_remains_blocking() -> None:
    plan = parse_measurement_plan(_python_metric_document())
    report = ValidationReport(
        (
            ValidationIssue(
                "python_script_node_missing",
                "The Python Script is unavailable.",
                "producers[0].config.script_node_id",
            ),
        )
    )

    assert experiment_measurements.blocking_measurement_plan_issues(report) == report.issues
    assert experiment_measurements.unavailable_producer_reasons(plan, report) == {}


@pytest.mark.asyncio
async def test_experiment_update_rejects_a_new_unvalidated_producer_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    experiment = SimpleNamespace(
        id=experiment_id,
        owner_id=owner_id,
        design_spec={"metrics": []},
        measurement_plan={"metrics": [], "producers": [], "inputs": []},
        locked_measurement_plan=None,
        locked_at=None,
        task_brief=None,
    )

    class FakeDb:
        async def get(self, _model: object, requested_id: object) -> object | None:
            return experiment if requested_id == experiment_id else None

    async def get_owned(*_args: object) -> object:
        return experiment

    async def fail_update(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("invalid measurement plans must not be persisted")

    monkeypatch.setattr(experiments_api, "_get_owned_experiment", get_owned)
    monkeypatch.setattr(experiments_api, "update_experiment", fail_update)
    metric = {
        "id": "typo",
        "name": "Typo",
        "kind": "runtime",
        "valueType": "number",
        "direction": "minimize",
        "aggregation": "sum",
        "primary": False,
    }
    plan = {
        "metrics": [
            {
                "id": "typo",
                "name": "Typo",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
            }
        ],
        "producers": [
            {
                "id": "runtime-typo",
                "producer_id": "asaree.runtime",
                "kind": "runtime",
                "outputs": {"cost_usd_typo": "typo"},
            }
        ],
        "inputs": [
            {
                "producer_binding_id": "runtime-typo",
                "input_key": "facts",
                "source_key": "attempt.runtime",
            }
        ],
    }

    with pytest.raises(HTTPException, match="no scalar output") as exc_info:
        await experiments_api.update_experiment_endpoint(
            experiment_id,
            experiments_api.UpdateExperimentRequest(
                design_spec={"metrics": [metric]},
                measurement_plan=plan,
            ),
            SimpleNamespace(id=owner_id),
            cast(AsyncSession, FakeDb()),
        )

    assert exc_info.value.status_code == 422


def test_readiness_reasons_are_resolved_for_builtin_python_and_mcp_bindings() -> None:
    plan = parse_measurement_plan(
        {
            "metrics": [
                {"id": "cost", "name": "Cost", "value_type": "number", "direction": "minimize", "aggregation": "sum"},
                {
                    "id": "quality",
                    "name": "Quality",
                    "value_type": "number",
                    "direction": "maximize",
                    "aggregation": "mean",
                },
                {
                    "id": "safety",
                    "name": "Safety",
                    "value_type": "boolean",
                    "direction": "maximize",
                    "aggregation": "rate",
                    "primary": True,
                },
            ],
            "producers": [
                {"id": "runtime", "producer_id": "asaree.runtime", "kind": "runtime", "outputs": {"cost_usd": "cost"}},
                {
                    "id": "python",
                    "producer_id": "asaree.python_script",
                    "kind": "reported",
                    "outputs": {"value": "quality"},
                },
                {
                    "id": "mcp",
                    "producer_id": "asaree.mcp_tool",
                    "kind": "reported",
                    "outputs": {"value": "safety"},
                },
            ],
            "inputs": [],
        }
    )
    report = ValidationReport(
        (
            ValidationIssue(
                "runtime_output_unavailable",
                "Cost is not supported by this runtime.",
                "producers[0].outputs.cost_usd",
                "needs_attention",
            ),
            ValidationIssue(
                "python_script_node_missing",
                "The Python Script is unavailable.",
                "producers[1].config.script_node_id",
                "needs_attention",
            ),
            ValidationIssue(
                "mcp_tool_unavailable",
                "The selected MCP tool is unavailable.",
                "producers[2].config.tool_name",
                "needs_attention",
            ),
        )
    )

    assert experiment_measurements.unavailable_producer_reasons(plan, report) == {
        "runtime": {"cost_usd": "Cost is not supported by this runtime."},
        "python": {"*": "The Python Script is unavailable."},
        "mcp": {"*": "The selected MCP tool is unavailable."},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document", "expected_code"),
    [
        (
            {
                "metrics": [
                    {
                        "id": "cost",
                        "name": "Cost",
                        "value_type": "number",
                        "direction": "minimize",
                        "aggregation": "sum",
                        "primary": True,
                    }
                ],
                "producers": [],
                "inputs": [],
            },
            "unbound_metric",
        ),
        (
            {
                "metrics": [
                    {
                        "id": "cost",
                        "name": "Cost",
                        "value_type": "number",
                        "direction": "minimize",
                        "aggregation": "sum",
                        "primary": True,
                    }
                ],
                "producers": [
                    {
                        "id": "runtime",
                        "producer_id": "asaree.runtime",
                        "kind": "runtime",
                        "outputs": {"cost_usd": "cost"},
                    }
                ],
                "inputs": [],
            },
            "missing_input",
        ),
    ],
)
async def test_experiment_measurement_validation_rejects_invalid_cross_plan_structure(
    document: dict[str, Any],
    expected_code: str,
) -> None:
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document=document,
        metrics=[
            {
                "id": metric["id"],
                "name": metric["name"],
                "kind": "runtime",
                "valueType": metric["value_type"],
                "direction": metric["direction"],
                "aggregation": metric["aggregation"],
                "primary": metric["primary"],
            }
            for metric in document["metrics"]
        ],
        graph={},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert expected_code in {issue.code for issue in report.issues}


@pytest.mark.asyncio
async def test_experiment_measurement_validation_accepts_a_plan_without_a_primary_metric() -> None:
    document = {
        "metrics": [
            {
                "id": "cost",
                "name": "Cost",
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
            }
        ],
        "producers": [
            {
                "id": "runtime",
                "producer_id": "asaree.runtime",
                "kind": "runtime",
                "outputs": {"cost_usd": "cost"},
            }
        ],
        "inputs": [
            {
                "producer_binding_id": "runtime",
                "input_key": "facts",
                "source_key": "attempt.runtime",
            }
        ],
    }

    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document=document,
        metrics=[
            {
                "id": "cost",
                "name": "Cost",
                "kind": "runtime",
                "valueType": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": False,
            }
        ],
        graph={},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert report.valid


@pytest.mark.asyncio
async def test_experiment_measurement_validation_reports_multiple_primary_metrics() -> None:
    metric = {
        "name": "Cost",
        "value_type": "number",
        "direction": "minimize",
        "aggregation": "sum",
        "primary": True,
    }
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document={
            "metrics": [{"id": "cost", **metric}, {"id": "duration", **metric, "name": "Duration"}],
            "producers": [],
            "inputs": [],
        },
        metrics=[],
        graph={},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert [issue.code for issue in report.issues] == ["multiple_primary_metrics"]


@pytest.mark.asyncio
async def test_experiment_measurement_validation_rejects_selected_metric_missing_from_plan() -> None:
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document={},
        metrics=[
            {
                "id": "cost",
                "name": "Cost",
                "kind": "runtime",
                "valueType": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": True,
            }
        ],
        graph={},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert report.issues == (
        ValidationIssue(
            "selected_metric_missing",
            "Selected metric 'Cost' is missing from the measurement plan.",
            "design_spec.metrics[0]",
        ),
    )


@pytest.mark.asyncio
async def test_legacy_runtime_declaration_is_not_migrated_during_validation() -> None:
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document=None,
        metrics=[
            {
                "name": "Cost",
                "catalogKey": "cost_usd",
                "kind": "runtime",
                "valueType": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": True,
            }
        ],
        graph={},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert {issue.code for issue in report.issues} == {"selected_metric_missing"}


@pytest.mark.asyncio
async def test_experiment_measurement_validation_rejects_hallucinated_producer_id() -> None:
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document={
            "metrics": [
                {
                    "id": "quality",
                    "name": "Quality",
                    "value_type": "number",
                    "direction": "maximize",
                    "aggregation": "mean",
                    "primary": True,
                }
            ],
            "producers": [
                {
                    "id": "invented-binding",
                    "producer_id": "invented",
                    "kind": "reported",
                    "outputs": {"quality": "quality"},
                    "artifacts": [],
                    "config": {},
                }
            ],
            "inputs": [],
        },
        metrics=[
            {
                "id": "quality",
                "name": "Quality",
                "kind": "runtime",
                "valueType": "number",
                "direction": "maximize",
                "aggregation": "mean",
                "primary": True,
            }
        ],
        graph={},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert report.issues == (
        ValidationIssue("unknown_producer", "Unknown producer 'invented'.", "producers[0].producer_id"),
    )


@pytest.mark.asyncio
async def test_experiment_measurement_validation_rejects_stale_or_mismatched_plan_metrics() -> None:
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document={
            "metrics": [
                {
                    "id": "cost",
                    "name": "Old cost label",
                    "value_type": "number",
                    "direction": "maximize",
                    "aggregation": "sum",
                    "primary": True,
                },
                {
                    "id": "stale",
                    "name": "Stale metric",
                    "value_type": "number",
                    "direction": "minimize",
                    "aggregation": "mean",
                    "primary": False,
                },
            ],
            "producers": [
                {
                    "id": "runtime",
                    "producer_id": "asaree.runtime",
                    "kind": "runtime",
                    "outputs": {"cost_usd": "cost", "duration_seconds": "stale"},
                }
            ],
            "inputs": [
                {
                    "producer_binding_id": "runtime",
                    "input_key": "facts",
                    "source_key": "attempt.runtime",
                }
            ],
        },
        metrics=[
            {
                "id": "cost",
                "name": "Cost",
                "kind": "runtime",
                "valueType": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "primary": True,
            }
        ],
        graph={},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert {issue.code for issue in report.issues} == {
        "selected_metric_mismatch",
        "unselected_plan_metric",
    }


@pytest.mark.asyncio
async def test_experiment_measurement_reconciliation_canonicalizes_boolean_rate() -> None:
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document={
            "metrics": [
                {
                    "id": "passed",
                    "name": "Passed",
                    "value_type": "boolean",
                    "direction": "maximize",
                    "aggregation": "rate",
                    "primary": True,
                }
            ],
            "producers": [
                {
                    "id": "runtime",
                    "producer_id": "asaree.runtime",
                    "kind": "runtime",
                    "outputs": {"cost_usd": "passed"},
                }
            ],
            "inputs": [
                {
                    "producer_binding_id": "runtime",
                    "input_key": "facts",
                    "source_key": "attempt.runtime",
                }
            ],
        },
        metrics=[
            {
                "id": "passed",
                "name": "Passed",
                "kind": "runtime",
                "valueType": "boolean",
                "direction": "maximize",
                "aggregation": "mean",
                "primary": True,
            }
        ],
        graph={},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert "selected_metric_mismatch" not in {issue.code for issue in report.issues}


def _python_metric_document() -> dict[str, Any]:
    return {
        "metrics": [
            {
                "id": "quality",
                "name": "Quality",
                "value_type": "opaque",
                "direction": "neutral",
                "aggregation": "none",
                "primary": False,
            }
        ],
        "producers": [
            {
                "id": "python-quality",
                "producer_id": "asaree.python_script",
                "kind": "reported",
                "outputs": {"value": "quality"},
                "artifacts": [],
                "config": {
                    "agent_node_id": "agent-1",
                    "script_node_id": "script-1",
                },
            }
        ],
        "inputs": [],
    }


@pytest.mark.asyncio
async def test_python_script_metric_requires_a_ready_direct_agent_script_pair() -> None:
    document = _python_metric_document()
    graph = {
        "nodes": [
            {"id": "agent-1", "type": "agent", "data": {"label": "Writer"}},
            {"id": "script-1", "type": "script", "data": {"label": "Scorer", "config": {"code": ""}}},
        ],
        "edges": [],
    }
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document=document,
        metrics=[
            {
                "id": "quality",
                "name": "Quality",
                "kind": "custom",
                "valueType": "number",
                "direction": "maximize",
                "aggregation": "mean",
                "primary": True,
            }
        ],
        graph=graph,
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert {issue.code for issue in report.issues} == {"python_script_not_connected", "python_script_empty"}


@pytest.mark.asyncio
async def test_python_script_metric_accepts_a_ready_direct_agent_script_pair() -> None:
    graph = {
        "nodes": [
            {"id": "agent-1", "type": "agent", "data": {"label": "Writer"}},
            {"id": "script-1", "type": "script", "data": {"label": "Scorer", "config": {"code": "print('{}')"}}},
        ],
        "edges": [{"source": "script-1", "target": "agent-1", "targetHandle": "tool"}],
    }
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document=_python_metric_document(),
        metrics=[
            {
                "id": "quality",
                "name": "Quality",
                "kind": "custom",
                "valueType": "number",
                "direction": "maximize",
                "aggregation": "mean",
                "primary": True,
            }
        ],
        graph=graph,
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert report.valid


@pytest.mark.asyncio
async def test_agent_output_metric_accepts_an_active_agent_without_an_input_binding() -> None:
    document = {
        "metrics": [
            {
                "id": "quality",
                "name": "Quality",
                "value_type": "number",
                "direction": "neutral",
                "aggregation": "mean",
                "primary": False,
            }
        ],
        "producers": [
            {
                "id": "agent-quality",
                "producer_id": "asaree.agent_output",
                "kind": "reported",
                "outputs": {"value": "quality"},
                "config": {"agent_node_id": "agent-1"},
            }
        ],
        "inputs": [],
    }
    report = await experiment_measurements.validate_experiment_measurement_plan(
        cast(AsyncSession, object()),
        document=document,
        metrics=[
            {
                "id": "quality",
                "name": "Quality",
                "kind": "custom",
                "valueType": "number",
                "direction": "neutral",
                "aggregation": "mean",
                "primary": False,
            }
        ],
        graph={"nodes": [{"id": "agent-1", "type": "agent", "data": {}}], "edges": []},
        experiment_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
    )

    assert report.valid
