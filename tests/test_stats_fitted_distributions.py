"""Native fitted-distribution support, PIT reconstruction and parametric intervals."""

import numpy as np
import pandas as pd
import pytest

from asaree.services.stats_source import StatsRequest
from asaree.stats.analysis import fit_model
from asaree.stats.fitted_distributions import fitted_distribution
from asaree.stats.resampling import bootstrap_intervals


@pytest.mark.parametrize(
    "family",
    ["gaussian", "lognormal", "gamma", "poisson", "negbin", "binomial", "beta", "ordinal_logit", "zip", "zinb"],
)
def test_fitted_quantiles_preserve_support_and_reconstruct_pit_observations(family):
    rng = np.random.default_rng(71)
    x = np.column_stack((np.ones(120), np.tile([1.0, -1.0], 60)))
    config = StatsRequest(metric="value", factors=["A"], n_boot=20, n_perm=99).model_dump()
    config.update(family=family, ordinal_levels=["low", "medium", "high"])
    trials = np.tile([10, 20], 60) if family == "binomial" else None
    values = {
        "gaussian": lambda: rng.normal(size=120),
        "lognormal": lambda: rng.lognormal(size=120),
        "gamma": lambda: rng.gamma(3, 2, 120),
        "poisson": lambda: rng.poisson(4, 120).astype(float),
        "negbin": lambda: rng.negative_binomial(2, 0.4, 120).astype(float),
        "binomial": lambda: rng.binomial(trials, 0.4).astype(float),
        "beta": lambda: rng.beta(3, 5, 120),
        "ordinal_logit": lambda: rng.integers(0, 3, 120).astype(float),
        "zip": lambda: (rng.binomial(1, 0.7, 120) * rng.poisson(4, 120)).astype(float),
        "zinb": lambda: (rng.binomial(1, 0.7, 120) * rng.negative_binomial(2, 0.4, 120)).astype(float),
    }[family]()
    fit = fit_model(values, x, family, config, trials)
    if family not in {"ordinal_logit", "zip", "zinb"}:
        assert fit._stats_fit_execution["checks"]["accepted"], fit._stats_fit_execution["checks"]
    else:
        assert fit._stats_fit_execution["checks"]["critical_diagnostics_gate"] is None
    distribution = fitted_distribution(fit, family, config, trials)
    uniforms = distribution.pit(values, np.random.default_rng(7))
    np.testing.assert_allclose(distribution.ppf(uniforms), values, rtol=1e-9, atol=1e-9)
    first = distribution.draw(np.random.default_rng(8), len(values))
    np.testing.assert_array_equal(first, distribution.draw(np.random.default_rng(8), len(values)))
    assert np.isfinite(first).all()
    if family in {"lognormal", "gamma", "beta"}:
        assert (first > 0).all()
    if family == "beta":
        assert (first < 1).all()
    if distribution.discrete:
        assert (first >= 0).all() and np.array_equal(first, first.round())
    if trials is not None:
        assert (first <= trials).all()


def test_parametric_percentile_intervals_match_independent_fixed_design_refits():
    rng = np.random.default_rng(19)
    x = np.column_stack((np.ones(80), np.tile([1.0, -1.0], 40)))
    y = rng.normal(size=80) + x[:, 1]
    grid = np.array([[1.0, 1.0], [1.0, -1.0]])
    weights = [[np.array([1.0, 0.0]), np.array([0.0, 1.0])]]
    frame = pd.DataFrame(dict(factor_0=np.tile(["A", "B"], 40), outcome=y))
    config = StatsRequest(metric="value", factors=["A"], n_boot=20, n_perm=99).model_dump()
    config["family"] = "gaussian"
    intervals, metadata = bootstrap_intervals(frame, x, y, grid, weights, "gaussian", config, False)
    original = fit_model(y, x, "gaussian", config)
    distribution = fitted_distribution(original, "gaussian", config)
    rng = np.random.default_rng(np.random.SeedSequence([config["seed"], 1101]))
    reference = []
    for _ in range(20):
        simulated = distribution.draw(rng, len(y))
        means = fit_model(simulated, x, "gaussian", config).predict(grid)
        reference.append(means[0] - means[1])
    np.testing.assert_allclose(intervals.iloc[0], np.quantile(reference, [0.025, 0.975]))
    assert metadata["procedure"] == "fitted_model_parametric_bootstrap"
    assert metadata["successful"] == 20
