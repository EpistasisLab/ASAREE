"""Read model for the experiment's latest canvas Test Run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from motoro.runner import list_runs
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.measurement_engine import normalize_measurement_plan
from asaree.services.measurement_migration import normalize_experiment_measurement_plan
from asaree.services.protocol_revisions import get_published_revision, get_revision, is_draft_published


@dataclass(frozen=True)
class ResourceUsage:
    duration_seconds: float | None
    cost_usd: float | None


@dataclass(frozen=True)
class TestRunResourceSummary:
    task: ResourceUsage
    evaluation: ResourceUsage
    total: ResourceUsage


@dataclass(frozen=True)
class TestRunResultProjection:
    observations: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    tested_published_revision: ProtocolRevision | None
    freshness_reasons: tuple[FreshnessReason, ...]
    resources: TestRunResourceSummary


def _seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def _document_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _current_measurement_plan(experiment: Any) -> dict[str, Any] | None:
    if experiment is None:
        return None
    plan = normalize_experiment_measurement_plan(
        experiment.locked_measurement_plan if experiment.locked_at is not None else experiment.measurement_plan,
        ((experiment.locked_design_spec if experiment.locked_at is not None else experiment.design_spec) or {}).get(
            "metrics"
        ),
    )
    return normalize_measurement_plan(plan) if plan["producers"] else None


def _measurement_document(attempt_result: dict[str, Any]) -> dict[str, Any]:
    measurement = attempt_result.get("measurement")
    return measurement if isinstance(measurement, dict) else {}


def _task_cost(observations: list[dict[str, Any]], plan: Any) -> float | None:
    if not isinstance(plan, dict):
        return None
    cost_metric_ids = {
        str(outputs["cost_usd"])
        for producer in plan.get("producers") or []
        if isinstance(producer, dict)
        and isinstance((outputs := producer.get("outputs")), dict)
        and "cost_usd" in outputs
    }
    for observation in observations:
        value = observation.get("value")
        if (
            observation.get("metric_id") in cost_metric_ids
            and observation.get("status") == "measured"
            and isinstance(value, int | float)
            and not isinstance(value, bool)
        ):
            return float(value)
    return None


async def _attributed_task_cost(run: ProtocolRun) -> float | None:
    try:
        attributed = await list_runs(
            owner_id=run.owner_id,
            metadata={"protocol_run_id": str(run.id)},
            limit=None,  # type: ignore[arg-type]
        )
    except Exception:
        return None
    costs = [
        float(value)
        for agent_run in attributed
        if isinstance((value := getattr(agent_run, "cost_estimate", None)), int | float | Decimal)
        and not isinstance(value, bool)
        and value > 0
    ]
    return sum(costs) if costs else None


async def project_test_run_result(
    db: AsyncSession, *, run: ProtocolRun, protocol: Protocol, experiment: Any
) -> TestRunResultProjection:
    attempt_result = run.attempt_result or {}
    measurement = _measurement_document(attempt_result)
    observations = measurement.get("observations") or []
    artifacts = measurement.get("artifacts") or []
    plan_snapshot = attempt_result.get("measurement_plan_snapshot")
    evaluation_summary = attempt_result.get("evaluation_summary") or {}
    task_completed_at = _document_datetime(attempt_result.get("task_completed_at"))
    evaluation_started_at = _document_datetime(attempt_result.get("evaluation_started_at"))
    evaluation_completed_at = _document_datetime(attempt_result.get("evaluation_completed_at"))
    task_duration = _seconds_between(run.started_at, task_completed_at)
    evaluation_duration = _seconds_between(evaluation_started_at, evaluation_completed_at)
    task_cost = _task_cost(observations, plan_snapshot)
    if task_cost is None and run.status in {"completed", "failed", "cancelled", "limit_reached"}:
        task_cost = await _attributed_task_cost(run)
    evaluation_cost = evaluation_summary.get("cost_usd")
    if not isinstance(evaluation_cost, int | float) or isinstance(evaluation_cost, bool):
        evaluation_cost = None

    published = await get_published_revision(db, protocol)
    tested_revision = await get_revision(db, run.protocol_revision_id) if run.protocol_revision_id else None
    freshness_reasons: list[FreshnessReason] = []
    if published is None or run.protocol_revision_id != published.id or not is_draft_published(protocol, published):
        freshness_reasons.append("canvas")
    if plan_snapshot != _current_measurement_plan(experiment):
        freshness_reasons.append("measurement_plan")

    return TestRunResultProjection(
        observations=observations,
        artifacts=artifacts,
        tested_published_revision=tested_revision,
        freshness_reasons=tuple(freshness_reasons),
        resources=TestRunResourceSummary(
            task=ResourceUsage(task_duration, task_cost),
            evaluation=ResourceUsage(evaluation_duration, evaluation_cost),
            total=ResourceUsage(
                task_duration + evaluation_duration
                if task_duration is not None and evaluation_duration is not None
                else None,
                task_cost + float(evaluation_cost) if task_cost is not None and evaluation_cost is not None else None,
            ),
        ),
    )


__all__ = [
    "FreshnessReason",
    "ResourceUsage",
    "TestRunResourceSummary",
    "TestRunResultProjection",
    "project_test_run_result",
]
FreshnessReason = Literal["canvas", "measurement_plan"]
