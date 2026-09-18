"""Lifecycle validation for every experiment measurement producer."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.services.measurement_engine import (
    MultiplePrimaryMetricsError,
    ValidationIssue,
    ValidationReport,
    parse_measurement_plan,
    validate_measurement_plan_structure,
)
from asaree.services.measurement_migration import normalize_experiment_measurement_plan
from asaree.services.metrics import normalize_metrics
from asaree.services.reported_metrics import (
    AGENT_OUTPUT_PRODUCER_ID,
    MCP_TOOL_PRODUCER_ID,
    PYTHON_SCRIPT_PRODUCER_ID,
    validate_reported_measurement_plan,
)
from asaree.services.runtime_metrics import RuntimeMetricProducer, validate_runtime_measurement_plan

_PRODUCER_PATH = re.compile(r"^producers\[(\d+)\]")
_OUTPUT_PATH = re.compile(r"^producers\[(\d+)\]\.outputs\.([^\.]+)$")


def blocking_measurement_plan_issues(report: ValidationReport) -> tuple[ValidationIssue, ...]:
    """Return declaration errors that must prevent publishing or execution.

    Readiness issues describe a once-valid producer whose live capability has
    disappeared. The declaration stays durable and other producers may run.
    """
    return tuple(issue for issue in report.issues if measurement_plan_issue_is_blocking(issue))


def measurement_plan_issue_is_blocking(issue: ValidationIssue) -> bool:
    """Whether an issue describes invalid configuration rather than lost capability."""
    return issue.severity == "blocking"


def unavailable_producer_reasons(plan: Any, report: ValidationReport) -> dict[str, dict[str, str]]:
    """Map invalid or unavailable producer outputs to reasons for one attempt.

    A ``*`` key means the whole binding must be skipped. Output-specific keys
    let a shared producer continue to evaluate its remaining healthy outputs.
    """
    declared = parse_measurement_plan(plan)
    reasons: dict[str, dict[str, list[str]]] = {}
    for issue in report.issues:
        if issue.severity != "needs_attention":
            continue
        match = _PRODUCER_PATH.match(issue.path)
        if match is None:
            continue
        index = int(match.group(1))
        if index >= len(declared.producers):
            continue
        output_match = _OUTPUT_PATH.match(issue.path)
        output_key = output_match.group(2) if output_match is not None else "*"
        messages = reasons.setdefault(declared.producers[index].id, {}).setdefault(output_key, [])
        if issue.message not in messages:
            messages.append(issue.message)
    return {
        binding_id: {output_key: "; ".join(messages) for output_key, messages in output_reasons.items()}
        for binding_id, output_reasons in reasons.items()
    }


async def validate_experiment_measurement_plan(
    db: AsyncSession,
    *,
    document: Any,
    metrics: Any,
    graph: Any,
    experiment_id: uuid.UUID,
    owner_id: uuid.UUID,
    allow_preserved_bindings: bool = True,
) -> ValidationReport:
    """Validate the complete producer plan before lock, publish, or execution."""
    try:
        normalized_document = normalize_experiment_measurement_plan(document, metrics)
    except MultiplePrimaryMetricsError:
        return ValidationReport(
            (
                ValidationIssue(
                    "multiple_primary_metrics",
                    "A measurement plan can have at most one primary metric.",
                    "metrics",
                ),
            )
        )
    plan = parse_measurement_plan(normalized_document)
    plan_by_id = {metric.id: metric for metric in plan.metrics}
    selected = normalize_metrics(
        [
            metric.model_dump(mode="python", by_alias=True) if isinstance(metric, BaseModel) else metric
            for metric in metrics or ()
            if isinstance(metric, (Mapping, BaseModel))
        ]
    )
    selected_ids = {metric["id"] for metric in selected if isinstance(metric.get("id"), str)}
    reconciliation_issues: list[ValidationIssue] = []
    for index, metric in enumerate(selected):
        metric_id = metric.get("id")
        if not isinstance(metric_id, str):
            continue
        declared = plan_by_id.get(metric_id)
        if declared is None:
            reconciliation_issues.append(
                ValidationIssue(
                    "selected_metric_missing",
                    f"Selected metric {str(metric.get('name') or metric_id)!r} is missing from the measurement plan.",
                    f"design_spec.metrics[{index}]",
                )
            )
            continue
        selected_semantics = (
            str(metric.get("name") or metric_id),
            metric.get("valueType", "number"),
            metric.get("direction", "maximize"),
            "rate" if metric.get("valueType") == "boolean" else metric.get("aggregation", "mean"),
            bool(metric.get("primary")),
        )
        declared_semantics = (
            declared.name,
            declared.value_type,
            declared.direction,
            declared.aggregation,
            declared.primary,
        )
        if selected_semantics != declared_semantics:
            reconciliation_issues.append(
                ValidationIssue(
                    "selected_metric_mismatch",
                    f"Selected metric {selected_semantics[0]!r} does not match its measurement-plan definition.",
                    f"design_spec.metrics[{index}]",
                )
            )
    for index, declared in enumerate(plan.metrics):
        if declared.id not in selected_ids:
            reconciliation_issues.append(
                ValidationIssue(
                    "unselected_plan_metric",
                    f"Measurement-plan metric {declared.name!r} is not selected in the experiment design.",
                    f"metrics[{index}]",
                )
            )
    if reconciliation_issues:
        return ValidationReport(tuple(reconciliation_issues))
    structural_report = validate_measurement_plan_structure(plan)
    if not structural_report.valid:
        return structural_report
    supported_producers = {
        RuntimeMetricProducer.producer_id: "runtime",
        AGENT_OUTPUT_PRODUCER_ID: "reported",
        PYTHON_SCRIPT_PRODUCER_ID: "reported",
        MCP_TOOL_PRODUCER_ID: "reported",
    }
    producer_issues = []
    for index, binding in enumerate(plan.producers):
        expected_kind = supported_producers.get(binding.producer_id)
        if expected_kind is None:
            producer_issues.append(
                ValidationIssue(
                    "unknown_producer",
                    f"Unknown producer {binding.producer_id!r}.",
                    f"producers[{index}].producer_id",
                )
            )
        elif binding.kind != expected_kind:
            producer_issues.append(
                ValidationIssue(
                    "incompatible_producer_kind",
                    f"Producer {binding.producer_id!r} is {expected_kind!r}, not {binding.kind!r}.",
                    f"producers[{index}].kind",
                )
            )
    if producer_issues:
        return ValidationReport(tuple(producer_issues))
    from asaree.models.experiment import ResearchExperiment

    experiment = await db.get(ResearchExperiment, experiment_id) if hasattr(db, "get") else None
    persisted_bindings = tuple(
        binding
        for document in (
            getattr(experiment, "measurement_plan", None),
        getattr(experiment, "locked_measurement_plan", None),
    )
    if document
    for binding in parse_measurement_plan(normalize_experiment_measurement_plan(document, ())).producers
    )
    preserved_binding_ids = (
        {binding.id for binding in plan.producers if binding in persisted_bindings}
        if allow_preserved_bindings
        else set()
    )
    issues = list(validate_runtime_measurement_plan(plan, preserved_binding_ids=preserved_binding_ids).issues)
    reported_report = await validate_reported_measurement_plan(
        plan,
        graph,
        owner_id=owner_id,
        preserved_binding_ids=preserved_binding_ids,
    )
    issues.extend(reported_report.issues)
    return ValidationReport(tuple(issues))
