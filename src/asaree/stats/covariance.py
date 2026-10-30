"""HC3 sandwich covariance, computed explicitly and verified after installation.

statsmodels 0.14.6 implements HC3 only for OLS (``RegressionResults.cov_HC3``).
For GLM and discrete-model results, ``fit(cov_type="HC3")`` silently installs
``sandwich_covariance.cov_white_simple(use_correction=False)``, which is HC0
(``statsmodels/base/covtype.py``, ``get_robustcov_results``). Passing the
keyword is therefore not evidence of HC3, so every robust covariance here is
built from the model's own score and information, installed on the result,
and the installed matrix is checked against the computed one.

HC3 (MacKinnon & White 1985; Long & Ervin 2000) rescales each observation's
score by 1 / (1 - h_i), where h_i is its leverage:

* OLS: h_i = diag(X (X'X)^-1 X'); identical to ``RegressionResults.cov_HC3``.
* GLM (Gamma, Poisson, binomial): h_i is the IRLS hat value from the expected
  information weights w_i = 1 / (g'(mu_i)^2 V(mu_i)), and the bread is
  (X'WX)^-1, as in R's ``sandwich::vcovHC(type = "HC3")`` for ``glm``. The
  dispersion cancels, so the estimate does not depend on the scale estimate.
* NB2: the scores include the dispersion alpha; the bread is the inverse
  observed information over (beta, alpha); leverage comes from the regression
  block with the NB2 working weights mu / (1 + alpha mu). This is the
  generalized-leverage HC3 used by the custom likelihood engine.

Beta uses joint mean/precision expected information and trace(H^-1 I_i)
leverage. Zero-inflated and ordinal models retain the recorded HC0 fallback.
"""

from __future__ import annotations

import numpy as np

HC3_FAMILIES = ("gaussian", "lognormal", "gamma", "poisson", "binomial", "negbin", "beta")
HC0_ONLY = {
    "zip": "this pipeline has no reviewed HC3 leverage construction for its two linear predictors",
    "zinb": "this pipeline has no reviewed HC3 leverage construction for its two linear predictors",
    "ordinal_logit": "this pipeline has no reviewed HC3 leverage construction for ordinal thresholds",
}
# A leverage this close to 1 makes 1 / (1 - h) unbounded: HC3 is undefined.
MAX_LEVERAGE = 1 - 1e-8


class CovarianceUnavailableError(ValueError):
    """The robust covariance is undefined for this fit."""


def _hc3_from_parts(scores, bread, leverage):
    if not np.isfinite(leverage).all() or np.any(leverage < 0) or np.any(leverage >= MAX_LEVERAGE):
        raise CovarianceUnavailableError(
            "leverage is non-finite, negative, or at least 1 - 1e-8; the HC3 diagnostic gate failed"
        )
    adjusted = scores / (1 - leverage)[:, None]
    return bread @ (adjusted.T @ adjusted) @ bread


def glm_parts(result):
    """Unit-scale GLM scores, expected-information bread, and IRLS hat values."""
    model = result.model
    params = np.asarray(result.params)
    x = model.exog
    scores = model.score_factor(params, scale=1.0)[:, None] * x
    weights = -model.hessian_factor(params, scale=1.0, observed=False)
    bread = np.linalg.inv((x.T * weights) @ x)
    leverage = weights * np.einsum("ij,jk,ik->i", x, bread, x)
    return scores, bread, leverage


def ols_parts(result):
    x = result.model.exog
    bread = np.linalg.inv(x.T @ x)
    leverage = np.einsum("ij,jk,ik->i", x, bread, x)
    return np.asarray(result.resid)[:, None] * x, bread, leverage


def negbin_parts(result):
    model = result.model
    params = np.asarray(result.params)
    x = model.exog
    alpha = params[-1]
    mu = np.exp(x @ params[:-1])
    scores = model.score_obs(params)
    bread = np.linalg.inv(-model.hessian(params))
    weights = mu / (1 + alpha * mu)
    leverage = weights * np.einsum("ij,jk,ik->i", x, np.linalg.inv((x.T * weights) @ x), x)
    return scores, bread, leverage


def robust_covariance(result, family, fallback="hc0", maximum_information_condition=1e10):
    """Return (covariance, record) with the estimator actually computed."""
    if family in ("gaussian", "lognormal"):
        scores, bread, leverage = ols_parts(result)
        implementation = "OLS hat values; matches statsmodels RegressionResults.cov_HC3"
    elif family in ("gamma", "poisson", "binomial"):
        scores, bread, leverage = glm_parts(result)
        implementation = "GLM expected-information bread and IRLS hat values (as R sandwich::vcovHC for glm)"
    elif family == "negbin":
        scores, bread, leverage = negbin_parts(result)
        implementation = (
            "NB2 scores for (coefficients, alpha), observed-information bread, "
            "leverage from the regression block with weights mu / (1 + alpha mu)"
        )
    elif family == "beta":
        from asaree.stats.beta_covariance import beta_parts

        try:
            scores, bread, leverage, _ = beta_parts(result, maximum_condition=maximum_information_condition)
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise CovarianceUnavailableError(f"Joint beta expected information unavailable: {exc}") from exc
        implementation = (
            "Joint mean/precision beta scores; expected-information bread H^-1; "
            "generalized leverage h_i = trace(H^-1 I_i), including cross-information; "
            "score weights 1/(1-h_i), without clipping"
        )
    elif family in HC0_ONLY:
        if fallback == "withhold":
            raise CovarianceUnavailableError(f"HC3 not implemented for {family}; configured fallback is withhold")
        # statsmodels' white-simple sandwich: verified HC0, not relabelled as HC3.
        from statsmodels.stats.sandwich_covariance import cov_white_simple

        covariance = np.asarray(cov_white_simple(result, use_correction=False))
        return covariance, dict(
            requested="HC3",
            computed="HC0",
            implementation="statsmodels sandwich_covariance.cov_white_simple (no small-sample correction)",
            reason=f"HC3 not implemented in this pipeline: {HC0_ONLY[family]}. Configured fallback: HC0.",
        )
    else:
        raise CovarianceUnavailableError(f"no robust covariance mapping for {family}")
    covariance = _hc3_from_parts(scores, bread, leverage)
    return covariance, dict(
        requested="HC3",
        computed="HC3",
        implementation=implementation,
        max_leverage=float(leverage.max()),
        leverage_sum=float(leverage.sum()),
    )


def install_covariance(result, covariance, record):
    """Make every statsmodels inference call on ``result`` use ``covariance``.

    Wald tests, t_test, conf_int, bse and get_prediction all read
    ``cov_params()``, which prefers ``cov_params_default``. Cached values from
    the model-based covariance are dropped, then the installed matrix and one
    Wald statistic are checked so a silent fallback cannot go unnoticed.
    """
    inner = getattr(result, "_results", result)
    inner.cov_params_default = covariance
    inner.cov_type = record["computed"]
    inner.cov_kwds = {"description": f"{record['computed']} sandwich covariance ({record['implementation']})"}
    inner.use_t = False
    inner._cache = {}
    installed = np.asarray(result.cov_params())
    params = np.asarray(result.params)
    test = result.wald_test(np.eye(len(params))[:1], scalar=True)
    expected = params[0] ** 2 / covariance[0, 0] if covariance[0, 0] > 0 else np.nan
    record["verified"] = bool(
        np.array_equal(installed, covariance)
        and np.allclose(np.asarray(result.bse), np.sqrt(np.clip(np.diag(covariance), 0, None)))
        and (not np.isfinite(expected) or np.isclose(float(test.statistic), expected, rtol=1e-8))
    )
    if not record["verified"]:
        raise CovarianceUnavailableError("the installed covariance does not match the computed robust covariance")
    return record


def singular_directions(covariance, restriction, relative_tolerance=1e-10):
    """True when R V R' is numerically singular relative to the scale of V."""
    restricted = restriction @ covariance @ restriction.T
    restricted = (restricted + restricted.T) / 2
    reference = np.linalg.eigvalsh((covariance + covariance.T) / 2).max()
    norm = np.linalg.norm(restriction, 2) ** 2
    if not np.isfinite(restricted).all() or reference <= 0:
        return True
    return bool(np.linalg.eigvalsh(restricted).min() <= relative_tolerance * reference * norm)
