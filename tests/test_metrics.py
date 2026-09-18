import re
from pathlib import Path

import pytest

from asaree.services.design_generation import material_design_spec
from asaree.services.measurement_engine import normalize_measurement_plan
from asaree.services.metrics import (
    METRIC_CATALOG,
    RECOMMENDED_RUNTIME_METRIC_KEYS,
    declared_primary_metric,
    design_metrics_from_measurement_plan,
    normalize_design_spec,
    normalize_metrics,
    recommended_runtime_measurements,
    validate_metric_values,
)


def test_frontend_metric_catalog_matches_the_backend_contract() -> None:
    """Fail loudly while display metadata has to be mirrored across languages."""
    source = (Path(__file__).parents[1] / "frontend/src/lib/metricCatalog.ts").read_text()
    frontend_keys = set(re.findall(r"\{ key: '([^']+)'", source))
    frontend_lines = {
        key: next(line for line in source.splitlines() if f"key: '{key}'" in line)
        for key in (str(entry["key"]) for entry in METRIC_CATALOG)
    }

    assert frontend_keys == {str(entry["key"]) for entry in METRIC_CATALOG}
    for entry in METRIC_CATALOG:
        line = frontend_lines[str(entry["key"])]
        for backend_field, frontend_field in (
            ("name", "name"),
            ("shortDescription", "shortDescription"),
            ("kind", "kind"),
            ("valueType", "valueType"),
            ("defaultDirection", "defaultDirection"),
            ("aggregation", "aggregation"),
            ("unit", "unit"),
        ):
            assert f"{frontend_field}: '{entry[backend_field]}'" in line
    assert {key for key, line in frontend_lines.items() if "recommended: true" in line} == set(
        RECOMMENDED_RUNTIME_METRIC_KEYS
    )


def test_legacy_metric_is_normalized_as_an_opaque_display_only_custom_metric() -> None:
    legacy = [{"name": "Accuracy", "primary": True, "direction": "maximize"}]
    first = normalize_metrics(legacy)
    second = normalize_metrics(legacy)
    assert first[0]["id"] == second[0]["id"]
    assert first[0]["kind"] == "custom"
    assert first[0]["description"] == "Legacy metric declaration for Accuracy."
    assert first[0]["valueType"] == "opaque"
    assert first[0]["direction"] == "neutral"
    assert first[0]["aggregation"] == "none"
    assert first[0]["primary"] is False


def test_legacy_metric_id_is_stable_when_an_unrelated_declaration_is_inserted() -> None:
    accuracy = {"name": "Accuracy", "direction": "maximize"}
    before = normalize_metrics([accuracy])
    after = normalize_metrics([{"name": "Cost", "direction": "minimize"}, accuracy])

    assert before[0]["id"] == after[1]["id"]


def test_normalization_preserves_an_explicitly_primary_less_metric_set() -> None:
    normalized = normalize_metrics(
        [
            {"name": "Cost", "direction": "minimize", "primary": False},
            {"name": "Quality", "direction": "maximize", "primary": False},
        ]
    )

    assert [metric["primary"] for metric in normalized] == [False, False]


def test_normalization_rejects_multiple_primary_metrics() -> None:
    with pytest.raises(ValueError, match="at most one primary"):
        normalize_metrics(
            [
                {"name": "Cost", "catalogKey": "cost_usd", "kind": "runtime", "direction": "minimize", "primary": True},
                {
                    "name": "Duration",
                    "catalogKey": "duration_seconds",
                    "kind": "runtime",
                    "direction": "minimize",
                    "primary": True,
                },
            ]
        )


def test_declared_primary_metric_does_not_fall_back_to_an_unselected_metric() -> None:
    assert (
        declared_primary_metric(
            [{"name": "Duration", "catalogKey": "duration_seconds", "kind": "runtime", "primary": False}]
        )
        is None
    )
    assert (
        declared_primary_metric(
            [
                {"name": "Duration", "catalogKey": "duration_seconds", "kind": "runtime", "primary": True},
                {"name": "Cost", "catalogKey": "cost_usd", "kind": "runtime", "primary": False},
            ]
        )["name"]
        == "Duration"
    )


def test_recommended_runtime_measurements_form_one_normalized_non_primary_plan() -> None:
    declarations, plan = recommended_runtime_measurements()

    assert [metric["catalogKey"] for metric in declarations] == [
        "cost_usd",
        "duration_seconds",
        "total_tokens",
        "tool_calls",
    ]
    assert [metric["primary"] for metric in declarations] == [False, False, False, False]
    assert normalize_measurement_plan(plan) == plan
    assert plan["producers"][0]["outputs"] == {
        "cost_usd": "runtime-cost",
        "duration_seconds": "runtime-duration",
        "total_tokens": "runtime-total-tokens",
        "tool_calls": "runtime-tool-calls",
    }


def test_design_metrics_are_derived_from_a_caller_supplied_plan() -> None:
    declarations = design_metrics_from_measurement_plan(
        {
            "metrics": [
                {
                    "id": "elapsed",
                    "name": "Elapsed time",
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
                    "outputs": {"duration_seconds": "elapsed"},
                    "config": {},
                }
            ],
            "inputs": [],
        }
    )

    assert declarations == [
        {
            "id": "elapsed",
            "catalogKey": "duration_seconds",
            "name": "Elapsed time",
            "description": "Wall-clock duration of the run.",
            "kind": "runtime",
            "valueType": "number",
            "direction": "minimize",
            "primary": False,
            "aggregation": "sum",
            "unit": "seconds",
        }
    ]


def test_custom_metric_values_are_retained_without_type_validation() -> None:
    metrics = [
        {"name": "Passed", "kind": "custom", "valueType": "boolean", "primary": True},
        {"name": "Score", "kind": "custom", "valueType": "number", "primary": False},
    ]
    assert validate_metric_values(metrics, {"Passed": True, "Score": 0.8}) == {
        "Passed": True,
        "Score": 0.8,
    }
    assert validate_metric_values(metrics, {"Passed": 1, "Score": {"grade": "A"}}) == {
        "Passed": 1,
        "Score": {"grade": "A"},
    }


def test_custom_boolean_declarations_normalize_to_opaque_display_only_metrics() -> None:
    metrics = normalize_metrics(
        [{"name": "Passed", "kind": "custom", "valueType": "boolean", "aggregation": "sum", "primary": True}]
    )
    assert metrics[0]["valueType"] == "opaque"
    assert metrics[0]["aggregation"] == "none"
    assert metrics[0]["primary"] is False


def test_duplicate_custom_labels_remain_persistable_drafts() -> None:
    spec = normalize_design_spec(
        {
            "metrics": [
                {"id": "draft-1", "name": "Untitled custom metric", "kind": "custom"},
                {"id": "draft-2", "name": "Untitled custom metric", "kind": "custom"},
            ]
        },
        validate_metrics=True,
    )

    assert [metric["id"] for metric in spec["metrics"]] == ["draft-1", "draft-2"]


def test_design_spec_adds_short_default_labels_for_legacy_factor_levels() -> None:
    spec = normalize_design_spec(
        {"factors": [{"name": "Agent:System prompt", "levels": ["a very long prompt", "another long prompt"]}]}
    )
    assert spec == {
        "factors": [
            {
                "name": "Agent:System prompt",
                "levels": ["a very long prompt", "another long prompt"],
                "level_labels": ["level1", "level2"],
            }
        ]
    }


def test_level_labels_change_the_material_design_because_they_name_cells() -> None:
    without_labels = {"factors": [{"name": "Agent:System prompt", "levels": ["a", "b"]}], "replicates": 2}
    with_labels = {
        "factors": [
            {
                "name": "Agent:System prompt",
                "levels": ["a", "b"],
                "level_labels": ["level1", "level2"],
            }
        ],
        "replicates": 2,
    }
    assert material_design_spec(without_labels) != material_design_spec(with_labels)
