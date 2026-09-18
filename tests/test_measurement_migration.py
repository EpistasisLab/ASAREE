from __future__ import annotations

from asaree.services.measurement_migration import (
    legacy_measurement_facets,
    normalize_experiment_measurement_plan,
)
from asaree.services.metrics import normalize_metrics


def test_legacy_declarations_are_not_migrated_into_measurement_plans() -> None:
    metrics = [
        {"name": "Cost", "catalogKey": "cost_usd", "kind": "runtime", "direction": "minimize"},
        {"name": "Clinical quality", "kind": "custom", "direction": "maximize"},
    ]

    assert normalize_experiment_measurement_plan(None, metrics) == {"metrics": [], "producers": [], "inputs": []}


def test_historical_values_and_artifacts_gain_legacy_unknown_provenance() -> None:
    metrics = [{"name": "Clinical quality", "kind": "custom", "direction": "maximize"}]

    facets = legacy_measurement_facets(
        metric_values={"Clinical quality": 0.87},
        artifacts={
            "confusion_matrix": {"labels": ["no", "yes"], "matrix": [[5, 1], [2, 4]]},
            "output_text": "ordinary run output",
            "protocol_run_id": "attempt-1",
        },
        metrics=metrics,
        attempt_id="attempt-1",
    )

    assert facets.observations == [
        {
            "metric_id": normalize_metrics(metrics)[0]["id"],
            "metric_name": "Clinical quality",
            "value_type": "number",
            "status": "measured",
            "value": 0.87,
            "error": None,
            "attempt_id": "attempt-1",
            "producer": {
                "binding_id": "legacy-unknown",
                "producer_id": "legacy.unknown",
                "kind": "legacy",
                "version": "unknown",
            },
            "input_provenance": {},
        }
    ]
    assert facets.artifacts == [
        {
            "artifact_key": "confusion_matrix",
            "kind": "confusion_matrix",
            "payload": {"labels": ["no", "yes"], "matrix": [[5, 1], [2, 4]]},
            "attempt_id": "attempt-1",
            "producer": {
                "binding_id": "legacy-unknown",
                "producer_id": "legacy.unknown",
                "kind": "legacy",
                "version": "unknown",
            },
            "input_provenance": {},
        }
    ]
    assert facets.legacy_values == []


def test_unmapped_non_scalar_values_remain_visible_but_non_rankable() -> None:
    metrics = [{"name": "Reviewer note", "kind": "custom", "valueType": "string", "primary": True}]

    plan = normalize_experiment_measurement_plan(None, metrics)
    facets = legacy_measurement_facets(
        metric_values={
            "Reviewer note": "needs follow-up",
            "Reviewer payload": {"flags": ["manual-review"]},
            "Reviewer list": ["first", "second"],
            "Reviewer null": None,
        },
        artifacts=None,
        metrics=metrics,
        attempt_id="attempt-1",
    )

    assert plan["metrics"] == []
    assert facets.observations == []
    assert facets.artifacts == []
    assert [item["metric_name"] for item in facets.legacy_values] == [
        "Reviewer payload",
        "Reviewer list",
        "Reviewer null",
    ]
    assert [item["value"] for item in facets.legacy_values] == [
        {"flags": ["manual-review"]},
        ["first", "second"],
        None,
    ]
    assert facets.legacy_values[0]["metric_name"] == "Reviewer payload"
    assert facets.legacy_values[0]["producer"] == {
        "binding_id": "legacy-unknown",
        "producer_id": "legacy.unknown",
        "kind": "legacy",
        "version": "unknown",
    }


def test_declared_opaque_custom_values_are_not_classified_as_legacy() -> None:
    facets = legacy_measurement_facets(
        metric_values={"LLM judge evaluation": {"score": 4, "passed": True}},
        artifacts=None,
        metrics=[
            {
                "id": "judge-evaluation",
                "name": "LLM judge evaluation",
                "kind": "custom",
                "valueType": "opaque",
            }
        ],
        attempt_id="attempt-1",
    )

    assert facets.legacy_values == []
