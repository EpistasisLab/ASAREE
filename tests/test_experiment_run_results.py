"""Pure normalization coverage for the experiment Results scorecard."""

import csv
import io
from decimal import Decimal
from types import SimpleNamespace

from asaree.services.csv_export import result_rows_schema, result_rows_to_csv
from asaree.services.experiment_run_results import (
    _aggregate_metric_values,
    _declared_metric_aggregations,
    _declared_runtime_metrics,
    _has_execution_evidence,
    _node_labels,
    _numeric_metrics,
    _primary_metric,
    _sum,
    _usage,
)


def test_usage_normalizes_provider_token_names_and_derives_total() -> None:
    agent_run = SimpleNamespace(
        token_usage={"prompt_tokens": 120, "completion_tokens": 30},
        cost_estimate=0.042,
    )
    assert _usage(agent_run) == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cost_usd": 0.042,
    }


def test_usage_keeps_motoro_numeric_cost_estimates() -> None:
    agent_run = SimpleNamespace(token_usage={}, cost_estimate=Decimal("0.042000"))
    assert _usage(agent_run)["cost_usd"] == 0.042


def test_usage_keeps_unreported_values_unknown_instead_of_zero() -> None:
    assert _usage(SimpleNamespace(token_usage=None, cost_estimate=None)) == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cost_usd": None,
    }


def test_boolean_metric_is_a_binary_numeric_outcome() -> None:
    assert _numeric_metrics({"passed": True, "failed": False}) == {"passed": 1.0, "failed": 0.0}


def test_primary_metric_uses_the_design_direction() -> None:
    assert _primary_metric({"metrics": [{"name": "loss", "primary": True, "direction": "minimize"}]}) == (
        "loss",
        "minimize",
    )


def test_declared_runtime_metrics_are_projected_from_execution_telemetry() -> None:
    spec = {
        "metrics": [
            {"catalogKey": "cost_usd", "kind": "runtime", "name": "Cost", "primary": True},
            {"catalogKey": "total_tokens", "kind": "runtime", "name": "Total tokens", "primary": False},
        ]
    }
    execution = {"cost_usd": 0.04, "total_tokens": 150, "duration_seconds": 2.5}
    assert _declared_runtime_metrics(spec, execution) == {"cost_usd": 0.04, "total_tokens": 150.0}
    assert _primary_metric(spec) == ("cost_usd", "maximize")


def test_declared_metric_aggregation_defaults_to_average_and_keeps_explicit_totals() -> None:
    assert _declared_metric_aggregations(
        {
            "metrics": [
                {"name": "Quality", "kind": "custom", "valueType": "number", "aggregation": "mean"},
                {"name": "Features", "kind": "custom", "valueType": "number", "aggregation": "sum"},
                {"name": "Passed", "kind": "custom", "valueType": "boolean", "aggregation": "sum"},
            ]
        }
    ) == {"Quality": "mean", "Features": "sum", "Passed": "mean"}


def test_cell_metric_aggregations_apply_the_declared_operation() -> None:
    assert _aggregate_metric_values([1.0, 2.0, 3.0], "mean") == 2.0
    assert _aggregate_metric_values([1.0, 2.0, 3.0], "sum") == 6.0


def test_cell_metric_rollups_sum_current_replicates() -> None:
    assert _sum([1250.0, 300.5, 49.5]) == 1600.0
    assert _sum([]) is None


def test_results_csv_includes_projected_runtime_metrics() -> None:
    csv_text = result_rows_to_csv(
        [
            {
                "replicate_label": "cell__rep1",
                "replicate_number": 1,
                "cell_label": "cell",
                "status": "completed",
                "factor_values": {"model": "small"},
                "metric_values": {"duration_seconds": 2.5, "total_tokens": 150},
                "cost_usd": 0.04,
            }
        ]
    )
    header, row = csv_text.strip().splitlines()
    assert "duration_seconds" in header and "total_tokens" in header
    assert "2.5" in row and "150" in row


def test_results_csv_projects_factors_to_a_numeric_design_matrix() -> None:
    csv_text = result_rows_to_csv(
        [
            {
                "replicate_label": "small__rep1",
                "factor_values": {"critic": True, "model": "small", "temperature": 0.2},
                "metric_values": {"accuracy": 0.8, "passed": True},
            },
            {
                "replicate_label": "large__rep1",
                "factor_values": {"critic": False, "model": "large", "temperature": 0.8},
                "metric_values": {"accuracy": 0.9, "passed": False},
            },
        ]
    )
    rows = list(csv.DictReader(io.StringIO(csv_text)))

    assert "critic_enabled" in rows[0]
    assert "temperature" in rows[0]
    # Categorical factors use treatment coding: alphabetically first "large"
    # is the reference level, and "small" becomes the indicator column.
    assert "model_small" in rows[0]
    assert "model" not in rows[0]
    assert rows[0]["critic_enabled"] == "1"
    assert rows[1]["critic_enabled"] == "0"
    assert rows[0]["model_small"] == "1"
    assert rows[1]["model_small"] == "0"
    assert rows[0]["temperature"] == "0.2"
    assert rows[1]["temperature"] == "0.8"
    assert rows[0]["passed"] == "1"
    assert rows[1]["passed"] == "0"


def test_results_csv_schema_describes_treatment_coding_and_binary_outcomes() -> None:
    schema = result_rows_schema(
        [
            {"factor_values": {"model": "small"}, "metric_values": {"passed": True}},
            {"factor_values": {"model": "large"}, "metric_values": {"passed": False}},
        ],
        {"passed": "boolean"},
    )
    factor = next(column for column in schema["columns"] if column["name"] == "model_small")
    outcome = next(column for column in schema["columns"] if column["name"] == "passed")
    assert factor == {
        "name": "model_small",
        "role": "factor",
        "source_factor": "model",
        "encoding": "categorical",
        "level": "small",
        "reference_level": "large",
    }
    assert outcome == {"name": "passed", "role": "outcome", "value_type": "boolean", "cell_aggregation": "mean"}


def test_results_csv_uses_persisted_level_labels_instead_of_long_treatment_values() -> None:
    long_prompts = [
        "Classify every record and explain each decision in exhaustive detail.",
        "Describe this dataset in full detail, including every available column and caveat.",
        "Summarize the dataset for a clinical researcher in one concise paragraph.",
    ]
    rows = [{"factor_values": {"Agent:System prompt": prompt}} for prompt in long_prompts]
    design_spec = {
        "factors": [
            {
                "name": "Agent:System prompt",
                "levels": long_prompts,
                "level_labels": ["classifier", "full_description", "concise_summary"],
            }
        ]
    }

    csv_text = result_rows_to_csv(rows, design_spec)
    schema = result_rows_schema(rows, design_spec=design_spec)

    header = csv_text.splitlines()[0]
    assert "agent_system_prompt_full_description" in header
    assert "agent_system_prompt_concise_summary" in header
    assert "Describe this dataset" not in header
    treatment = next(column for column in schema["columns"] if column["name"] == "agent_system_prompt_full_description")
    assert treatment["level_label"] == "full_description"
    assert treatment["reference_level_label"] == "classifier"


def test_node_labels_prefers_the_canvas_name_over_its_durable_id() -> None:
    assert _node_labels({"nodes": [{"id": "node-mtj4m99c-l0beqhd2", "data": {"label": "Model evaluator"}}]}) == {
        "node-mtj4m99c-l0beqhd2": "Model evaluator"
    }


def test_node_labels_uses_the_canvas_placeholder_when_an_old_node_has_no_title() -> None:
    assert _node_labels({"nodes": [{"id": "node-old", "type": "agent", "data": {}}]}) == {"node-old": "Agent"}


def test_execution_evidence_excludes_completed_configuration_nodes() -> None:
    assert _has_execution_evidence({"status": "completed", "output_text": None, "error": None}) is False
    assert _has_execution_evidence({"status": "completed", "run_id": "agent-run"}) is True
    assert _has_execution_evidence({"status": "failed", "error": "boom"}) is True
