"""Pure normalization coverage for the experiment Results scorecard."""

from decimal import Decimal
from types import SimpleNamespace

from asaree.services.csv_export import result_rows_to_csv
from asaree.services.experiment_run_results import (
    _declared_runtime_metrics,
    _has_execution_evidence,
    _node_labels,
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
