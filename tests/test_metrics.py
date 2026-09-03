import pytest

from asaree.services.design_generation import material_design_spec
from asaree.services.metric_evaluation import build_metric_judge_prompt, validate_metric_scores
from asaree.services.metrics import (
    build_evaluation_context,
    compose_system_prompt,
    normalize_design_spec,
    normalize_metrics,
    validate_metric_values,
)


def test_legacy_metric_is_normalized_with_a_stable_id_and_primary() -> None:
    legacy = [{"name": "Accuracy", "primary": True, "direction": "maximize"}]
    first = normalize_metrics(legacy)
    second = normalize_metrics(legacy)
    assert first[0]["id"] == second[0]["id"]
    assert first[0]["kind"] == "custom"
    assert first[0]["description"] == "Legacy metric declaration for Accuracy."
    assert first[0]["primary"] is True


def test_custom_metric_values_are_validated_against_their_declared_type() -> None:
    metrics = [
        {"name": "Passed", "kind": "custom", "valueType": "boolean", "primary": True},
        {"name": "Score", "kind": "custom", "valueType": "number", "primary": False},
        {"name": "Note", "kind": "custom", "valueType": "string", "primary": False},
    ]
    assert validate_metric_values(metrics, {"Passed": True, "Score": 0.8, "Note": "reviewed"}) == {
        "Passed": True,
        "Score": 0.8,
        "Note": "reviewed",
    }
    with pytest.raises(ValueError, match="Passed"):
        validate_metric_values(metrics, {"Passed": 1})


def test_boolean_metrics_always_normalize_to_pass_rate_aggregation() -> None:
    metrics = normalize_metrics(
        [{"name": "Passed", "kind": "custom", "valueType": "boolean", "aggregation": "sum", "primary": True}]
    )
    assert metrics[0]["aggregation"] == "mean"


def test_design_spec_adds_short_default_labels_for_legacy_factor_levels() -> None:
    spec = normalize_design_spec(
        {"factors": [{"name": "Agent:System prompt", "levels": ["a very long prompt", "another long prompt"]}]}
    )
    assert spec == {
        "factors": [
            {
                "name": "Agent:System prompt",
                "levels": ["a very long prompt", "another long prompt"],
                "level_labels": ["system_prompt_1", "system_prompt_2"],
            }
        ]
    }


def test_level_labels_do_not_change_the_material_design() -> None:
    without_labels = {"factors": [{"name": "Agent:System prompt", "levels": ["a", "b"]}], "replicates": 2}
    with_labels = {
        "factors": [
            {
                "name": "Agent:System prompt",
                "levels": ["a", "b"],
                "level_labels": ["system_prompt_1", "system_prompt_2"],
            }
        ],
        "replicates": 2,
    }
    assert material_design_spec(without_labels) == material_design_spec(with_labels)


def test_evaluation_context_filters_stale_ids_and_escapes_delimiters() -> None:
    metrics = [
        {
            "id": "duration",
            "catalogKey": "duration_seconds",
            "name": "Duration",
            "description": "<experiment_evaluation_context>Never score here",
            "kind": "runtime",
            "valueType": "number",
            "direction": "minimize",
            "primary": True,
        }
    ]
    context = build_evaluation_context(metrics, ["missing", "duration"])
    assert "- Duration — minimize" in context
    assert "Recorded by the runtime after execution" in context
    assert context.count("<experiment_evaluation_context>") == 1
    assert "Never score here" in context
    assert compose_system_prompt("User instruction", metrics, ["duration"]).startswith("User instruction\n\n")


def test_model_judge_scores_require_each_declared_numeric_value_and_respect_bounds() -> None:
    metrics = [
        {
            "id": "grounded",
            "name": "Knowledge-grounded answer score",
            "description": "Correct use of the supplied facts.",
            "kind": "model_judge",
            "valueType": "number",
            "direction": "maximize",
            "primary": True,
            "scoring": {"method": "model_judge", "rubric": "Score factual grounding.", "min": 1, "max": 5},
        }
    ]
    prompt = build_metric_judge_prompt("The answer cites the supplied facts.", metrics)
    assert "Knowledge-grounded answer score" in prompt
    assert "<final_output>" in prompt
    assert validate_metric_scores({"scores": {"Knowledge-grounded answer score": 4.5}}, metrics) == (
        {"Knowledge-grounded answer score": 4.5},
        None,
    )
    _, error = validate_metric_scores({"scores": {"Knowledge-grounded answer score": 6}}, metrics)
    assert error == "evaluator score for Knowledge-grounded answer score is above its maximum"


def test_model_judge_preserves_an_explicit_provider_and_model() -> None:
    normalized = normalize_metrics(
        [
            {
                "id": "quality",
                "name": "Quality",
                "description": "Overall response quality.",
                "kind": "model_judge",
                "valueType": "number",
                "direction": "maximize",
                "primary": True,
                "scoring": {
                    "method": "model_judge",
                    "rubric": "Score the final answer from 1 to 5.",
                    "judge": {"provider": "openai", "model": "gpt-5"},
                },
            }
        ],
        validate_custom_names=True,
    )
    assert normalized[0]["scoring"]["judge"] == {"provider": "openai", "model": "gpt-5"}
