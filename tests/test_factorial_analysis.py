"""Tests for services.factorial_analysis.analyze_experiment_design -- the
Results tab's own entry point, deriving analyze_factorial's parameters from
an experiment's Design tab declarations instead of a caller supplying them.
Pure/no-DB: takes a plain design_spec dict and a list of simple cell-like
objects, matching analyze_factorial's own "stateless function of cells"
contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from asaree.services.factorial_analysis import analyze_experiment_design


@dataclass
class _FakeCell:
    factor_values: dict[str, Any] = field(default_factory=dict)
    metric_values: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    cell_label: str = ""


def _design_spec(**overrides: Any) -> dict[str, Any]:
    base = {
        "factors": [{"name": "tier", "levels": ["small", "large"]}],
        "metrics": [{"name": "accuracy", "primary": True, "direction": "maximize"}],
    }
    base.update(overrides)
    return base


def test_no_factors_is_unavailable() -> None:
    result = analyze_experiment_design(
        {"metrics": [{"name": "accuracy", "primary": True, "direction": "maximize"}]}, []
    )
    assert result["available"] is False
    assert "factors" in result["reason"]
    assert result["analysis"] is None
    assert result["best_condition"] is None


def test_no_primary_metric_is_unavailable() -> None:
    result = analyze_experiment_design(
        _design_spec(metrics=[{"name": "accuracy", "primary": False, "direction": "maximize"}]), []
    )
    assert result["available"] is False
    assert "primary metric" in result["reason"]


def test_non_binary_factor_is_unavailable() -> None:
    spec = _design_spec(factors=[{"name": "tier", "levels": ["small", "medium", "large"]}])
    result = analyze_experiment_design(spec, [])
    assert result["available"] is False
    assert "exactly 2 levels" in result["reason"]
    assert "tier" in result["reason"]


def test_none_design_spec_is_unavailable() -> None:
    result = analyze_experiment_design(None, [])
    assert result["available"] is False
    assert result["analysis"] is None


def test_not_enough_scored_data_is_unavailable_not_a_crash() -> None:
    # Exactly 1 scored cell per condition -- too few data points for
    # analyze_factorial's saturated OLS model (2 points, 2 terms) raises a
    # raw ZeroDivisionError before its own "fewer than 2 scored replicates"
    # check is ever reached. This function's contract is to never crash the
    # Results tab regardless of which numeric edge case degenerate data hits.
    cells = [
        _FakeCell(factor_values={"tier": "small"}, metric_values={"accuracy": 0.8}, cell_label="tier_small"),
        _FakeCell(factor_values={"tier": "large"}, metric_values={"accuracy": 0.9}, cell_label="tier_large"),
    ]
    result = analyze_experiment_design(_design_spec(), cells)
    assert result["available"] is False
    assert result["reason"]
    assert result["analysis"] is None


def test_reference_condition_with_one_replicate_surfaces_real_analyze_factorial_error() -> None:
    # Enough total data points that the OLS fit itself succeeds, but the
    # reference condition (first declared level of every factor) has only 1
    # scored replicate -- this trips analyze_factorial's own real
    # FactorialAnalysisError ("fewer than 2 scored replicates"), not the
    # numeric-edge-case fallback the test above covers.
    spec = _design_spec(
        factors=[{"name": "tier", "levels": ["small", "large"]}, {"name": "effort", "levels": ["low", "high"]}]
    )
    cells = [
        _FakeCell(factor_values={"tier": "small", "effort": "low"}, metric_values={"accuracy": 0.70}),
        _FakeCell(factor_values={"tier": "small", "effort": "high"}, metric_values={"accuracy": 0.75}),
        _FakeCell(factor_values={"tier": "small", "effort": "high"}, metric_values={"accuracy": 0.77}),
        _FakeCell(factor_values={"tier": "large", "effort": "low"}, metric_values={"accuracy": 0.80}),
        _FakeCell(factor_values={"tier": "large", "effort": "low"}, metric_values={"accuracy": 0.82}),
        _FakeCell(factor_values={"tier": "large", "effort": "high"}, metric_values={"accuracy": 0.90}),
        _FakeCell(factor_values={"tier": "large", "effort": "high"}, metric_values={"accuracy": 0.92}),
    ]
    result = analyze_experiment_design(spec, cells)
    assert result["available"] is False
    assert "replicate" in result["reason"]


def test_happy_path_returns_analysis_and_best_condition_maximize() -> None:
    cells = [
        _FakeCell(factor_values={"tier": "small"}, metric_values={"accuracy": 0.70}, cell_label="tier_small"),
        _FakeCell(factor_values={"tier": "small"}, metric_values={"accuracy": 0.72}, cell_label="tier_small__rep2"),
        _FakeCell(factor_values={"tier": "small"}, metric_values={"accuracy": 0.68}, cell_label="tier_small__rep3"),
        _FakeCell(factor_values={"tier": "large"}, metric_values={"accuracy": 0.91}, cell_label="tier_large"),
        _FakeCell(factor_values={"tier": "large"}, metric_values={"accuracy": 0.89}, cell_label="tier_large__rep2"),
        _FakeCell(factor_values={"tier": "large"}, metric_values={"accuracy": 0.93}, cell_label="tier_large__rep3"),
    ]
    result = analyze_experiment_design(_design_spec(), cells)
    assert result["available"] is True
    assert result["reason"] is None
    assert result["analysis"] is not None
    assert result["analysis"]["n_scored"] == 6
    # "large" has the higher mean accuracy and direction is "maximize".
    assert result["best_condition"]["_condition_label"] == "tier_large"


def test_happy_path_best_condition_respects_minimize_direction() -> None:
    spec = _design_spec(metrics=[{"name": "latency", "primary": True, "direction": "minimize"}])
    cells = [
        _FakeCell(factor_values={"tier": "small"}, metric_values={"latency": 1.2}, cell_label="tier_small"),
        _FakeCell(factor_values={"tier": "small"}, metric_values={"latency": 1.3}, cell_label="tier_small__rep2"),
        _FakeCell(factor_values={"tier": "small"}, metric_values={"latency": 1.1}, cell_label="tier_small__rep3"),
        _FakeCell(factor_values={"tier": "large"}, metric_values={"latency": 3.0}, cell_label="tier_large"),
        _FakeCell(factor_values={"tier": "large"}, metric_values={"latency": 3.2}, cell_label="tier_large__rep2"),
        _FakeCell(factor_values={"tier": "large"}, metric_values={"latency": 2.8}, cell_label="tier_large__rep3"),
    ]
    result = analyze_experiment_design(spec, cells)
    assert result["available"] is True
    # "small" has the lower mean latency and direction is "minimize".
    assert result["best_condition"]["_condition_label"] == "tier_small"
