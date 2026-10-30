"""Regression contracts for the human-reviewed inference policy."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from asaree.services.stats_findings import describe_report_findings, primary_comparison_result
from asaree.services.stats_html import render_html
from asaree.services.stats_source import StatsRequest, compute
from asaree.stats.analysis import fit_model
from asaree.stats.fit_checks import native_fit_checks
from asaree.stats.fitted_distributions import FittedDistribution
from asaree.stats.resampling import quantile_residual_sampler


def test_quantile_residual_permutation_preserves_block_membership_and_original_support():
    means = np.linspace(0.2, 0.8, 24)
    distribution = FittedDistribution(stats.beta(means * 12, (1 - means) * 12))
    frame = pd.DataFrame(dict(subject=np.repeat(["one", "two", "three"], 8)))
    values = distribution.draw(np.random.default_rng(8), len(means))
    original_uniforms = distribution.cdf(values)
    draw = quantile_residual_sampler(np.random.default_rng(5), distribution, values, frame, ["subject"])
    first = draw()
    assert np.all((first > 0) & (first < 1))
    permuted_uniforms = distribution.cdf(first)
    for group in frame.groupby("subject").indices.values():
        np.testing.assert_allclose(np.sort(original_uniforms[group]), np.sort(permuted_uniforms[group]), rtol=1e-12)
    assert not np.allclose(first, values)
    repeat = quantile_residual_sampler(np.random.default_rng(5), distribution, values, frame, ["subject"])
    np.testing.assert_array_equal(first, repeat())
    with pytest.raises(ValueError, match="missing"):
        quantile_residual_sampler(np.random.default_rng(5), distribution, values, frame, ["missing"])


@pytest.mark.parametrize(
    "low,high,expected",
    [
        (-0.01, 0.01, "No clear evidence of a difference"),
        (0.1, 0.2, "Evidence of a difference"),
        (None, None, "Primary inference unavailable"),
    ],
)
def test_primary_conclusions_do_not_switch_to_a_significant_sensitivity_result(low, high, expected):
    effect = dict(
        comparison="Treatment A vs B",
        scale="difference",
        primary_ci_low=low,
        primary_ci_high=high,
        primary_status="completed" if low is not None else "unavailable",
        p_adjusted=1e-12,
    )
    assert primary_comparison_result(effect) == expected
    summary = dict(primary_analysis="parametric_bootstrap_intervals", effects=[effect], tests=[])
    finding = describe_report_findings(summary)[0]
    assert ("no clear evidence" in finding) == (expected == "No clear evidence of a difference")
    assert ("unavailable" in finding) == (expected == "Primary inference unavailable")


def test_primary_ratio_interval_touching_one_is_not_significant():
    assert (
        primary_comparison_result(
            dict(scale="ratio", primary_status="completed", primary_ci_low=0.8, primary_ci_high=1.0)
        )
        == "No clear evidence of a difference"
    )


def test_pipeline_and_html_use_primary_intervals_when_analytic_p_is_significant(monkeypatch):
    def straddles_null(frame, x, y, grid, weights, family, config, ratio, trials):
        return (
            pd.DataFrame(dict(bootstrap_ci_low=[-0.01] * len(weights), bootstrap_ci_high=[0.01] * len(weights))),
            dict(
                procedure="fitted_model_parametric_bootstrap",
                method="Parametric bootstrap",
                requested=20,
                attempted=20,
                successful=20,
                failed=0,
                status="completed",
            ),
        )

    monkeypatch.setattr("asaree.stats.resampling.bootstrap_intervals", straddles_null)
    rng = np.random.default_rng(8)
    source = dict(
        rows=[
            dict(
                id=str(i),
                replicate=i,
                factors={"Treatment": str(i % 2)},
                metrics={"value": float(rng.normal() - 10 + 20 * (i % 2))},
            )
            for i in range(80)
        ],
        metric_metadata={},
        fingerprint="policy-test",
        design_revision=1,
        design_revision_id="policy-revision",
        excluded_row_replicates=0,
    )
    output = compute(source, StatsRequest(metric="value", n_boot=20, n_perm=99).model_dump())
    assert output["validity"] == "valid", output["summary"]["warnings"]
    effect = output["summary"]["effects"][0]
    assert effect["analytic_result"] == "Evidence of a difference"
    assert effect["result"] == "No clear evidence of a difference"
    assert "no clear evidence" in output["summary"]["findings"][0]
    html = render_html(dict(id="policy", created_at="fixed", output=output))
    assert "Primary interval result" in html
    assert "Primary parametric-bootstrap 95% interval" in html


def test_initial_fits_and_refits_apply_configured_influence_and_condition_gates():
    rng = np.random.default_rng(7)
    x = np.column_stack((np.ones(80), np.tile([1.0, -1.0], 40)))
    y = rng.normal(size=80)
    config = StatsRequest(metric="value", factors=["A"]).model_dump()
    fit = fit_model(y, x, "gaussian", config)
    assert fit._stats_fit_execution["checks"]["accepted"]
    strict = native_fit_checks(fit, dict(config, family="gaussian", maximum_cooks_distance=1e-12))
    assert not strict["accepted"]
    assert not strict["critical_diagnostics_gate"]
    assert strict["diagnostics"]["critical_failures"]
