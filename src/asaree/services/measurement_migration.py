"""Historical result projection without measurement-plan migration."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from math import isfinite
from typing import Any

from asaree.services.measurement_engine import (
    MeasurementPlan,
    normalize_measurement_plan,
    parse_measurement_plan,
)
from asaree.services.metrics import normalize_metrics

_LEGACY_PRODUCER = {
    "binding_id": "legacy-unknown",
    "producer_id": "legacy.unknown",
    "kind": "legacy",
    "version": "unknown",
}
_LEGACY_EVALUATION_ARTIFACTS = {
    "calibration",
    "calibration_curve",
    "classification_report",
    "confusion_matrix",
    "per_class",
    "per_class_report",
}


@dataclass(frozen=True)
class LegacyResultFacets:
    observations: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    legacy_values: list[dict[str, Any]]


def normalize_experiment_measurement_plan(document: Any, metrics: Any) -> dict[str, Any]:
    """Return only the explicitly declared canonical plan.

    ``metrics`` remains in the signature while API callers transition, but it
    is deliberately ignored: unsupported feature-branch declarations are not
    inferred or migrated into executable producer bindings.
    """
    del metrics
    migrated_document = deepcopy(document)
    if isinstance(migrated_document, dict):
        producers = migrated_document.get("producers")
        if isinstance(producers, list):
            for producer in producers:
                if isinstance(producer, dict) and producer.get("kind") == "deterministic_evaluator":
                    producer["kind"] = "reported"
    plan = parse_measurement_plan(migrated_document) if migrated_document is not None else MeasurementPlan((), (), ())
    reported_ids = {"asaree.agent_output", "asaree.python_script", "asaree.mcp_tool"}
    reported_bindings = tuple(binding for binding in plan.producers if binding.producer_id in reported_ids)
    reported_binding_ids = {binding.id for binding in reported_bindings}
    reported_metric_ids = {metric_id for binding in reported_bindings for metric_id in binding.outputs.values()}
    normalized = replace(
        plan,
        metrics=tuple(
            replace(metric, value_type="opaque", direction="neutral", aggregation="none", primary=False)
            if metric.id in reported_metric_ids
            else metric
            for metric in plan.metrics
        ),
        producers=tuple(
            replace(binding, kind="reported", artifacts=()) if binding.id in reported_binding_ids else binding
            for binding in plan.producers
        ),
        inputs=tuple(item for item in plan.inputs if item.producer_binding_id not in reported_binding_ids),
    )
    return normalize_measurement_plan(normalized)


def _legacy_metric_id(key: str, metrics: Any) -> tuple[str, str]:
    for metric in normalize_metrics(metrics):
        result_key = metric.get("catalogKey") if metric.get("kind") == "runtime" else metric.get("name")
        if result_key == key:
            return str(metric["id"]), str(metric["name"])
    stable = uuid.uuid5(uuid.NAMESPACE_URL, f"asaree:legacy-metric-value:{key}")
    return f"legacy-value-{stable}", key


def legacy_measurement_facets(
    *, metric_values: Any, artifacts: Any, metrics: Any, attempt_id: str
) -> LegacyResultFacets:
    """Project old raw result JSON into the observation/artifact read model."""
    observations: list[dict[str, Any]] = []
    legacy_values: list[dict[str, Any]] = []
    if isinstance(metric_values, Mapping):
        for key, value in metric_values.items():
            if not isinstance(key, str):
                continue
            if not isinstance(value, bool | int | float) or (isinstance(value, float) and not isfinite(value)):
                metric_id, metric_name = _legacy_metric_id(key, metrics)
                legacy_values.append(
                    {
                        "metric_id": metric_id,
                        "metric_name": metric_name,
                        "value": value,
                        "attempt_id": attempt_id,
                        "producer": dict(_LEGACY_PRODUCER),
                    }
                )
                continue
            value_type = "boolean" if isinstance(value, bool) else "number"
            metric_id, metric_name = _legacy_metric_id(key, metrics)
            observations.append(
                {
                    "metric_id": metric_id,
                    "metric_name": metric_name,
                    "value_type": value_type,
                    "status": "measured",
                    "value": value,
                    "error": None,
                    "attempt_id": attempt_id,
                    "producer": dict(_LEGACY_PRODUCER),
                    "input_provenance": {},
                }
            )
    projected_artifacts = []
    if isinstance(artifacts, Mapping):
        for key, payload in artifacts.items():
            if not isinstance(key, str) or key not in _LEGACY_EVALUATION_ARTIFACTS:
                continue
            projected_artifacts.append(
                {
                    "artifact_key": key,
                    "kind": key,
                    "payload": payload,
                    "attempt_id": attempt_id,
                    "producer": dict(_LEGACY_PRODUCER),
                    "input_provenance": {},
                }
            )
    return LegacyResultFacets(observations, projected_artifacts, legacy_values)


__all__ = ["LegacyResultFacets", "legacy_measurement_facets", "normalize_experiment_measurement_plan"]
