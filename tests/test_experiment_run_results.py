"""Pure normalization coverage for the experiment Results scorecard."""

from decimal import Decimal
from types import SimpleNamespace

from asaree.services.experiment_run_results import _has_execution_evidence, _node_labels, _primary_metric, _usage


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
    assert _primary_metric(
        {"metrics": [{"name": "loss", "primary": True, "direction": "minimize"}]}
    ) == ("loss", "minimize")


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
