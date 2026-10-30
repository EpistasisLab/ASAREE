"""Checks for fixed-family bootstrap execution and permutation report contracts."""

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from asaree.services.stats_resampling import native_permutations
from asaree.services.stats_source import StatsRequest, compute
from asaree.stats.resampling import bootstrap_intervals


def bootstrap_input():
    group = np.repeat(["A", "B"], [24, 36])
    x = np.column_stack((np.ones(len(group)), np.where(group == "A", 1, -1)))
    y = np.random.default_rng(4).normal(size=len(group)) + (group == "B")
    frame = pd.DataFrame(dict(factor_0=group, outcome=y))
    config = StatsRequest(metric="value", factors=["Treatment"], n_boot=20, n_perm=99).model_dump()
    grid = np.array([[1, 1], [1, -1]])
    weights = [[np.array([1, 0]), np.array([0, 1])]]
    return frame, x, y, grid, weights, config


def test_bootstrap_matches_independent_native_refits_and_preserves_cell_counts():
    frame, x, y, grid, weights, config = bootstrap_input()
    bounds, metadata = bootstrap_intervals(frame, x, y, grid, weights, "gaussian", config, False)
    rng = np.random.default_rng(np.random.SeedSequence([config["seed"], 1101]))
    reference = []
    for _ in range(20):
        a = rng.choice(np.arange(24), size=24, replace=True)
        b = rng.choice(np.arange(24, 60), size=36, replace=True)
        indices = np.concatenate((a, b))
        means = sm.OLS(y[indices], x[indices]).fit().predict(grid)
        reference.append(means[0] - means[1])
    np.testing.assert_allclose(bounds.iloc[0], np.quantile(reference, [0.025, 0.975]))
    assert metadata["status"] == "completed"
    assert metadata["successful"] == metadata["attempted"] == 20
    assert metadata["failed"] == 0


@pytest.mark.parametrize("failure", ["fit", "budget"])
def test_incomplete_bootstrap_has_counts_and_no_intervals(monkeypatch, failure):
    frame, x, y, grid, weights, config = bootstrap_input()
    if failure == "budget":
        config["_bootstrap_budget_s"] = 0
    else:

        def fail(*args, **kwargs):
            raise ValueError("Deliberate refit failure")

        monkeypatch.setattr("asaree.stats.analysis.fit_model", fail)
    bounds, metadata = bootstrap_intervals(frame, x, y, grid, weights, "gaussian", config, False)
    assert bounds.isna().all().all()
    assert metadata["status"] != "completed"
    assert metadata["successful"] == 0
    assert metadata["failed"] == metadata["attempted"] == (20 if failure == "fit" else 0)


def test_factorial_permutations_have_counts_and_reproducible_stream():
    frame, _, _, _, _, config = bootstrap_input()
    frame["factor_1"] = np.tile(["C", "D"], 30)
    config.update(factors=["Treatment", "Setting"], family="gaussian")
    first, reason = native_permutations(frame, config)
    second, _ = native_permutations(frame, config)
    assert reason is None
    assert first == second
    # Interactions are resampled with the fitted model's own statistic elsewhere.
    assert {row["term"] for row in first} == {"factor_0", "factor_1"}
    for row in first:
        assert row["n_perm"] == 99
        assert 0.01 <= row["p_perm"] <= 1
        assert row["F_obs"] >= 0
        assert "within strata" in row["method"]


def test_unequal_trial_totals_explain_why_permutation_is_withheld():
    frame, _, _, _, _, config = bootstrap_input()
    frame["trials"] = np.tile([10, 20], 30)
    config.update(denominator="trials", family="binomial")
    rows, reason = native_permutations(frame, config)
    assert rows == []
    assert "unequal trial totals" in reason


def test_automatic_report_preserves_analytic_inference_when_bootstrap_cannot_finish(monkeypatch):
    from asaree.stats import resampling

    actual = resampling.bootstrap_intervals

    def no_budget(frame, x, y, grid, weights, family, config, ratio, trials):
        return actual(frame, x, y, grid, weights, family, dict(config, _bootstrap_budget_s=0), ratio, trials)

    monkeypatch.setattr(resampling, "bootstrap_intervals", no_budget)
    frame, _, y, _, _, config = bootstrap_input()
    source = dict(
        rows=[
            dict(id=str(i), replicate=i + 1, factors={"Treatment": frame.factor_0[i]}, metrics={"value": value})
            for i, value in enumerate(y)
        ],
        fingerprint="snapshot",
        metric_metadata={},
        design_revision=1,
        design_revision_id="revision",
        excluded_row_replicates=0,
    )
    result = compute(source, config)
    assert result["validity"] == "valid"
    summary = result["summary"]
    assert summary["resampling"]["bootstrap"]["attempted"] == 0
    assert result["execution_status"] == summary["execution_status"] == "incomplete"
    effect = summary["effects"][0]
    assert effect["ci_low"] is not None
    assert effect["p_adjusted"] is not None
    assert effect["bootstrap_ci_low"] is None
    assert "budget" in effect["bootstrap_status"]
    assert summary["tests"][0]["permutation_p_adjusted"] is not None


def test_configured_success_fraction_controls_failed_refit_handling(monkeypatch):
    from asaree.stats.analysis import fit_model

    frame, x, y, grid, weights, config = bootstrap_input()
    attempts = 0

    def alternating(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts % 2:
            raise ValueError("Deliberate invalid refit")
        return fit_model(*args, **kwargs)

    monkeypatch.setattr("asaree.stats.analysis.fit_model", alternating)
    rejected, strict = bootstrap_intervals(frame, x, y, grid, weights, "gaussian", config, False)
    accepted, relaxed = bootstrap_intervals(
        frame, x, y, grid, weights, "gaussian", dict(config, minimum_success_fraction=0.5), False
    )
    assert rejected.isna().all().all()
    assert accepted.notna().all().all()
    assert strict["successful"] == relaxed["successful"] == 10
    assert strict["failure_reasons"] == {"Deliberate invalid refit": 10}
    assert strict["status"] == "unavailable"
    assert relaxed["status"] == "completed"


def test_nested_f_statistic_does_not_turn_a_zero_effect_into_negative_roundoff():
    from asaree.stats.nonparametric import OLS, f_statistic

    group = np.tile([1.0, -1.0], 60)
    x = np.column_stack((np.ones(120), group))
    y = np.tile([0.0, 1.0, 1.0, 0.0], 30)
    statistic, df1, df2 = f_statistic(OLS(x), OLS(x[:, :1]), y)
    assert df1 == 1 and df2 == 118
    assert statistic >= 0
    assert statistic == pytest.approx(0, abs=1e-20)
