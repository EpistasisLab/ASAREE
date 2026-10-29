"""HC3 covariance references, interaction null resampling, and degenerate-fit guards."""

import json

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from scipy import optimize, stats

from asaree.services.stats_html import render_html
from asaree.services.stats_prototypes import StatsRequest, compute
from asaree.services.stats_resampling import native_permutations
from asaree.stats.analysis import analyze
from asaree.stats.covariance import (
    glm_parts,
    install_covariance,
    negbin_parts,
    robust_covariance,
    singular_directions,
)

N_PER_CELL = 9
EFFORT = np.repeat(["high", "low"], 2 * N_PER_CELL)
BUDGET = np.tile(np.repeat(["2 features", "4 features"], N_PER_CELL), 2)


def design():
    a = np.where(EFFORT == "high", 1.0, -1.0)
    b = np.where(BUDGET == "2 features", 1.0, -1.0)
    return np.column_stack([np.ones(len(a)), a, b, a * b])


def cells():
    """Cell-mean (indicator) coding: each coefficient is one condition's linear predictor."""
    labels = pd.Series(EFFORT + "|" + BUDGET)
    return pd.get_dummies(labels).to_numpy(float), labels


def hc0(parts):
    scores, bread, _ = parts
    return bread @ (scores.T @ scores) @ bread


def glm(y, x, family):
    return sm.GLM(y, x, family=family).fit()


GLM_FAMILIES = {
    "poisson": (sm.families.Poisson(), lambda rng, mu: rng.poisson(mu * 6).astype(float)),
    "gamma": (sm.families.Gamma(sm.families.links.Log()), lambda rng, mu: rng.gamma(3, mu / 3)),
    "binomial": (sm.families.Binomial(), lambda rng, mu: rng.binomial(1, mu / (1 + mu)).astype(float)),
}


def test_statsmodels_hc3_keyword_is_silently_hc0_for_glms():
    """Regression guard for the reason HC3 is computed explicitly (statsmodels 0.14.6 covtype.py)."""
    rng = np.random.default_rng(1)
    x = design()
    y = rng.poisson(np.exp(1 + 0.3 * x[:, 1]))
    model = sm.GLM(y, x, family=sm.families.Poisson())
    np.testing.assert_allclose(model.fit(cov_type="HC3").cov_params(), model.fit(cov_type="HC0").cov_params())


def test_ols_hc3_matches_statsmodels_native_hc3():
    rng = np.random.default_rng(2)
    x = design()
    y = x @ [1, 0.5, -0.3, 0.1] + rng.normal(0, 1 + (x[:, 1] > 0), len(x))
    result = sm.OLS(y, x).fit()
    covariance, record = robust_covariance(result, "gaussian")
    np.testing.assert_allclose(covariance, result.cov_HC3, rtol=1e-12)
    assert record["computed"] == "HC3"


@pytest.mark.parametrize("name", sorted(GLM_FAMILIES))
def test_glm_sandwich_parts_reproduce_statsmodels_hc0(name):
    family, draw = GLM_FAMILIES[name]
    rng = np.random.default_rng(3)
    x = design()
    y = draw(rng, np.exp(0.5 + 0.4 * x[:, 1] - 0.2 * x[:, 2]))
    native = sm.GLM(y, x, family=family).fit(cov_type="HC0").cov_params()
    np.testing.assert_allclose(hc0(glm_parts(glm(y, x, family))), native, rtol=1e-10)


@pytest.mark.parametrize("name", sorted(GLM_FAMILIES))
def test_glm_hc3_matches_closed_form_for_condition_means(name):
    """Saturated cell-mean model: Var(eta_k) = sum (y - ybar)^2 / ((n_k - 1)^2 (dmu/deta)^2)."""
    family, draw = GLM_FAMILIES[name]
    rng = np.random.default_rng(4)
    x, labels = cells()
    y = draw(rng, np.exp(0.2 + 0.5 * x[:, 0] - 0.4 * x[:, 3]))
    covariance, record = robust_covariance(glm(y, x, family), name)
    expected = []
    for column in range(x.shape[1]):
        values = y[x[:, column] == 1]
        mean = values.mean()
        derivative = mean if name != "binomial" else mean * (1 - mean)
        expected.append(np.sum((values - mean) ** 2) / ((len(values) - 1) ** 2 * derivative**2))
    np.testing.assert_allclose(np.diag(covariance), expected, rtol=1e-8)
    assert record["computed"] == "HC3"
    assert record["max_leverage"] == pytest.approx(1 / N_PER_CELL)


def test_negative_binomial_hc3_parts_match_references():
    rng = np.random.default_rng(5)
    x = design()
    mu = np.exp(3 + 0.4 * x[:, 1] - 0.3 * x[:, 2])
    y = rng.negative_binomial(4, 4 / (4 + mu)).astype(float)
    result = sm.NegativeBinomial(y, x, loglike_method="nb2").fit(disp=0)
    native_hc0 = sm.NegativeBinomial(y, x, loglike_method="nb2").fit(disp=0, cov_type="HC0").cov_params()
    np.testing.assert_allclose(hc0(negbin_parts(result)), native_hc0, rtol=1e-8)
    covariance, record = robust_covariance(result, "negbin")
    # With alpha fixed at its estimate, the coefficient block equals GLM-NB HC3.
    fixed = glm(y, x, sm.families.NegativeBinomial(alpha=result.params[-1]))
    scores, bread, leverage = glm_parts(fixed)
    adjusted = scores / (1 - leverage)[:, None]
    np.testing.assert_allclose(covariance[:4, :4], bread @ adjusted.T @ adjusted @ bread, rtol=1e-6)
    assert record["computed"] == "HC3"


def test_hc3_is_close_to_the_leave_one_out_jackknife():
    rng = np.random.default_rng(6)
    x = design()
    family = sm.families.Gamma(sm.families.links.Log())
    y = rng.gamma(2, np.exp(1 + 0.3 * x[:, 1]) / 2)
    covariance, _ = robust_covariance(glm(y, x, family), "gamma")
    estimates = np.array([glm(np.delete(y, i), np.delete(x, i, 0), family).params for i in range(len(y))])
    centered = estimates - estimates.mean(0)
    jackknife = (len(y) - 1) / len(y) * centered.T @ centered
    np.testing.assert_allclose(np.diag(covariance) / np.diag(jackknife), 1, atol=0.1)


def test_installed_covariance_drives_every_statsmodels_inference_call():
    rng = np.random.default_rng(7)
    x = design()
    result = glm(rng.poisson(5, len(x)).astype(float), x, sm.families.Poisson())
    covariance, record = robust_covariance(result, "poisson")
    install_covariance(result, covariance, record)
    assert record["verified"]
    np.testing.assert_array_equal(result.cov_params(), covariance)
    np.testing.assert_allclose(result.bse, np.sqrt(np.diag(covariance)))
    restriction = np.eye(4)[[3]]
    beta = result.params[3]
    assert result.wald_test(restriction, scalar=True).statistic == pytest.approx(beta**2 / covariance[3, 3])
    gradient = np.exp(x[0] @ result.params) * x[0]
    assert result.get_prediction(x[:1]).se_mean[0] == pytest.approx(np.sqrt(gradient @ covariance @ gradient))


def test_multipart_models_record_hc0_instead_of_claiming_hc3():
    rng = np.random.default_rng(8)
    x = design()
    y = rng.beta(2 + x[:, 1], 5)
    from statsmodels.othermod.betareg import BetaModel

    covariance, record = robust_covariance(BetaModel(y, x).fit(disp=0), "beta")
    assert record["computed"] == "HC0"
    assert "HC3 unsupported" in record["reason"]
    assert np.isfinite(covariance).all()


def gamma_frame(seed=9):
    rng = np.random.default_rng(seed)
    mu = np.exp(1 + 0.3 * (EFFORT == "high") - 0.2 * (BUDGET == "2 features"))
    return pd.DataFrame(dict(factor_0=EFFORT, factor_1=BUDGET, outcome=rng.gamma(4, mu / 4)))


def config(family, **kwargs):
    return dict(
        StatsRequest(metric="value", factors=["Effort", "Feature budget"], n_boot=50, n_perm=99).model_dump(),
        family=family,
        robust=True,
        **kwargs,
    )


def count_frame(family, seed=12):
    rng = np.random.default_rng(seed)
    mu = np.exp(2 + 0.3 * (EFFORT == "high") - 0.2 * (BUDGET == "2 features"))
    outcome = rng.poisson(mu) if family == "poisson" else rng.negative_binomial(3, 3 / (3 + mu))
    return pd.DataFrame(dict(factor_0=EFFORT, factor_1=BUDGET, outcome=outcome.astype(float)))


@pytest.mark.parametrize("family", ["gamma", "poisson", "negbin"])
def test_robust_fits_install_verified_hc3(family):
    frame = gamma_frame() if family == "gamma" else count_frame(family)
    output = analyze(frame, config(family))
    assert output["covariance"]["computed"] == "HC3"
    assert output["covariance"]["verified"]
    assert all("HC3 robust covariance" in method for method in output["tables"]["tests"].p_method)
    assert all("HC3 robust covariance" in method for method in output["tables"]["contrasts"].p_method)


def test_gamma_aic_uses_the_maximum_likelihood_shape_and_counts_it():
    frame = gamma_frame()
    output = analyze(frame, config("gamma"))
    fit = output["tables"]["model_fit"].iloc[0]
    mean = analyze(frame, config("gamma"))["tables"]["residuals"].fitted.to_numpy()
    y = frame.outcome.to_numpy()

    def negative(log_shape):
        shape = np.exp(log_shape)
        return -stats.gamma.logpdf(y, shape, scale=mean / shape).sum()

    reference = -optimize.minimize_scalar(negative).fun
    assert fit.loglik == pytest.approx(reference, rel=1e-7)
    assert fit.n_parameters == 5
    assert fit.aic == pytest.approx(-2 * reference + 10, rel=1e-7)


def interaction(output):
    return output["tables"]["interaction_resampling"].iloc[0].to_dict()


def test_gamma_interaction_is_a_freedman_lane_permutation_of_the_models_wald_statistic():
    frame = gamma_frame()
    first = analyze(frame, dict(config("gamma"), _resample=True))
    second = analyze(frame, dict(config("gamma"), _resample=True))
    row = interaction(first)
    assert row == interaction(second)
    assert row["kind"] == "permutation"
    assert "Freedman–Lane" in row["method"] and "log response" in row["method"]
    assert row["observed_statistic"] == pytest.approx(first["tables"]["tests"].statistic.iloc[-1])
    assert row["completed"] == row["requested"] == 99
    assert 1 / 100 <= row["p_perm"] <= 1
    assert row["seed_stream"] == 3303


@pytest.mark.parametrize("family", ["poisson", "negbin"])
def test_count_interaction_is_labelled_a_parametric_bootstrap(family):
    row = interaction(analyze(count_frame(family), dict(config(family), _resample=True)))
    assert row["kind"] == "parametric bootstrap"
    assert "not a permutation test" in row["assumptions"]
    assert row["completed"] + row["failed"] == row["attempted"] == 99
    assert row["p_perm"] is not None


def test_zero_inflated_interaction_resampling_is_withheld_with_its_reason():
    frame = gamma_frame()
    frame["outcome"] = np.round(frame.outcome * 2) * (np.arange(len(frame)) % 3 > 0)
    row = interaction(analyze(frame, dict(config("zip"), _resample=True)))
    assert row["p_perm"] is None
    assert "zero-inflated" in row["reason"]


def exact_count_source():
    """n_features-style data: every run equals its Feature budget, with no within-condition variation."""
    return dict(
        rows=[
            dict(
                id=str(i),
                replicate=i + 1,
                factors={"Effort": effort, "Feature budget": budget},
                metrics={"n_features_used": 2 if budget == "2 features" else 4},
            )
            for i, (effort, budget) in enumerate(zip(EFFORT, BUDGET, strict=True))
        ],
        fingerprint="exact",
        design_revision=1,
        design_revision_id="revision",
        excluded_row_replicates=0,
        metric_metadata={"n_features_used": {"unit": "features"}},
    )


def automatic(source, metric):
    return compute("stats_sm", source, StatsRequest(metric=metric, n_boot=50, n_perm=99).model_dump(mode="json"))


def test_exact_fit_keeps_descriptive_estimates_and_withholds_all_inference():
    output = automatic(exact_count_source(), "n_features_used")
    json.dumps(output, allow_nan=False)
    summary = output["summary"]
    assert summary["status"] == "valid"
    assert "Exact fit" in summary["inference_reason"]
    for test in summary["tests"]:
        assert test["p_adjusted"] is None and test["permutation_p_adjusted"] is None
        assert test["result"] == "Inference unavailable"
    estimates = {effect["comparison"]: effect for effect in summary["effects"]}
    assert estimates["Effort: high vs low"]["estimate"] == pytest.approx(1)
    assert estimates["Feature budget: 2 features vs 4 features"]["estimate"] == pytest.approx(0.5)
    for effect in summary["effects"]:
        assert effect["ci_low"] is None and effect["p_adjusted"] is None and effect["bootstrap_ci_low"] is None
        assert "Exact fit" in effect["unavailable_reason"]
    assert summary["resampling"]["bootstrap"]["attempted"] == 0
    for row in summary["resampling"]["permutations"]:
        assert row.get("p_perm") is None
        assert row.get("F_obs") is None or row["F_obs"] >= 0
    html = render_html(dict(id="x", created_at="now", output=output))
    assert "Inference unavailable." in html
    assert "&lt;0.001" not in html


def test_exact_fit_permutation_check_explains_the_undefined_f_statistic():
    frame = pd.DataFrame(dict(factor_0=EFFORT, factor_1=BUDGET, outcome=np.where(BUDGET == "2 features", 2.0, 4.0)))
    rows, reason = native_permutations(frame, config("poisson"))
    assert reason is None
    assert {row["term"] for row in rows} == {"factor_0", "factor_1"}
    assert all(row["n_perm"] == 0 and "F is undefined" in row["reason"] for row in rows)


def test_exact_continuous_fit_reports_why_aic_cannot_rank_candidates():
    source = exact_count_source()
    for row in source["rows"]:
        row["metrics"] = {"Duration": 1.5 if row["factors"]["Feature budget"] == "2 features" else 3.25}
    source["metric_metadata"] = {"Duration": {"unit": "seconds"}}
    summary = automatic(source, "Duration")["summary"]
    assert summary["status"] == "valid"
    assert "AIC is undefined" in summary["model_fit"]["rule"]
    assert all(test["p_adjusted"] is None for test in summary["tests"])


def test_partly_constant_conditions_are_flagged():
    source = exact_count_source()
    rng = np.random.default_rng(10)
    for row in source["rows"]:
        if row["factors"]["Feature budget"] == "4 features":
            row["metrics"]["n_features_used"] = int(rng.integers(3, 7))
    summary = automatic(source, "n_features_used")["summary"]
    assert any("In 2 of 4 conditions every replicate had the same value" in w for w in summary["warnings"])


def test_singular_directions_detects_a_zero_variance_contrast():
    covariance = np.diag([1.0, 2.0, 0.0])
    assert singular_directions(covariance, np.array([[0.0, 0.0, 1.0]]))
    assert not singular_directions(covariance, np.array([[1.0, 1.0, 1.0]]))


def test_report_columns_carry_methods_and_model_fit_panel():
    source = exact_count_source()
    rng = np.random.default_rng(11)
    for row in source["rows"]:
        row["metrics"]["n_features_used"] = int(rng.poisson(3 if row["factors"]["Effort"] == "high" else 5))
    output = automatic(source, "n_features_used")
    summary = output["summary"]
    assert summary["column_methods"]["tests"]["p_adjusted"].startswith("Wald chi-square test (HC3 robust covariance)")
    assert [t["label"] for t in summary["tests"]] == ["Effort", "Feature budget", "Effort × Feature budget"]
    titles = [section["title"] for section in summary["details"]]
    assert titles == [
        "Model selection",
        "Factor tests",
        "Comparisons",
        "Permutation and null-resampling checks",
        "Bootstrap intervals",
        "Multiplicity and interpretation",
    ]
    interaction_row = next(e for e in summary["effects"] if "×" in e["comparison"])
    assert interaction_row["null_hypothesis"] == "ratio of ratios = 1"
    assert "ratio of ratios" in interaction_row["target"]
    html = render_html(dict(id="x", created_at="now", output=output))
    assert '<section class="model-fit" aria-label="Model fit">' in html
    assert "ΔAIC vs best usable" in html
    # Method labels sit under the column heading, not inside the Effect cells.
    effect_cell = html.split("<tbody>")[2].split("</td>")[0]
    assert "Wald" not in effect_cell
