"""Serializable numerical checks, distinct from an optimizer's termination flag.

These checks do not establish a global optimum or validate statistical inference.
An independent score tolerance is applied only when configured by a reviewer.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
from scipy import stats


def native_diagnostics(result, config):
    """The same deterministic diagnostics for initial fits and native refits.

    Missing leverage constructions are recorded, never represented as passed
    influence checks. Statistical critical thresholds come from configuration.
    """
    from asaree.stats.covariance import glm_parts, negbin_parts, ols_parts

    family = config.get("family")
    out = dict(critical_failures=[], unavailable=[])
    try:
        if family in {"gaussian", "lognormal"}:
            scores, bread, leverage = ols_parts(result)
            scale = float(result.scale)
            if scale <= 0 or not np.isfinite(scale):
                raise ValueError("No residual variance")
            scores, bread = scores / scale, bread * scale
        elif family in {"gamma", "poisson", "binomial"}:
            scores, bread, leverage = glm_parts(result)
            scale = float(result.scale)
            scores, bread = scores / scale, bread * scale
        elif family == "negbin":
            scores, bread, leverage = negbin_parts(result)
        elif family == "beta":
            from asaree.stats.beta_covariance import beta_parts

            scores, bread, leverage, _ = beta_parts(
                result, maximum_condition=config.get("maximum_information_condition", 1e10)
            )
        else:
            raise ValueError("No reviewed leverage/influence construction for this family")
        distance = np.einsum("ij,jk,ik->i", scores, bread, scores) / (len(result.params) * (1 - leverage) ** 2)
        out.update(
            max_leverage=float(np.max(leverage)),
            maximum_cooks_distance=float(np.max(distance)),
            influence_method="Generalized score Cook distance s_i^T H^-1 s_i / [p (1-h_i)^2]",
        )
        if not np.isfinite(leverage).all() or np.any(leverage >= 1 - 1e-8):
            out["critical_failures"].append("Undefined or unit leverage")
        threshold = config.get("maximum_cooks_distance", 1)
        if threshold is not None and (not np.isfinite(distance).all() or np.max(distance) > threshold):
            out["critical_failures"].append("Influence exceeds configured generalized Cook distance threshold")
    except (AttributeError, ValueError, TypeError, np.linalg.LinAlgError, FloatingPointError) as exc:
        out["unavailable"].append(str(exc))
        if family in {"gaussian", "lognormal", "gamma", "poisson", "binomial", "negbin", "beta"}:
            out["critical_failures"].append("Required leverage/influence diagnostic failed: " + str(exc))
    residual = getattr(result, "resid", None)
    if family in {"gaussian", "lognormal"} and residual is not None and len(residual) >= 20 and np.std(residual) > 0:
        skew = float(stats.skew(residual))
        p = float(stats.normaltest(residual).pvalue)
        out.update(residual_skewness=skew, residual_normality_p=p)
        if p < 0.001 and abs(skew) > 1:
            out["critical_failures"].append("Severe Gaussian residual skewness")
    pearson = getattr(result, "resid_pearson", None)
    df = getattr(result, "df_resid", 0)
    if pearson is not None and df > 0 and family in {"poisson", "binomial", "gamma", "negbin"}:
        dispersion = float(np.sum(np.asarray(pearson) ** 2) / df)
        out["pearson_dispersion"] = dispersion
        if family in {"poisson", "binomial"} and dispersion > config.get("maximum_pearson_dispersion", 3):
            out["critical_failures"].append("Dispersion exceeds configured threshold")
    return out


def optimizer_converged(result):
    if result.model.__class__.__name__ == "OLS":
        # Direct linear solve, not iterative optimizer termination.
        return bool(np.isfinite(result.params).all())
    return bool(getattr(result, "converged", getattr(result, "mle_retvals", {}).get("converged", False)))


def native_fit_checks(result, config):
    params = np.asarray(result.params)
    checks = dict(
        optimizer_converged=optimizer_converged(result),
        finite_estimates=bool(np.isfinite(params).all()),
        finite_likelihood=bool(np.isfinite(result.llf)),
    )
    try:
        cov = np.asarray(result.cov_params())
        eigenvalues = np.linalg.eigvalsh((cov + cov.T) / 2)
        checks["covariance_valid"] = bool(np.isfinite(cov).all() and eigenvalues.min() > 0)
        checks["identifiable"] = checks["covariance_valid"]
        checks["information_condition"] = (
            float(eigenvalues.max() / eigenvalues.min()) if eigenvalues.min() > 0 else None
        )
        maximum_condition = config.get("maximum_information_condition", 1e10)
        checks["information_stability_gate"] = (
            checks["information_condition"] is not None and checks["information_condition"] <= maximum_condition
            if maximum_condition is not None
            else None
        )
    except (ValueError, TypeError, AttributeError, np.linalg.LinAlgError):
        checks.update(covariance_valid=False, identifiable=False)
    try:
        score = np.asarray(result.model.score(params))
        scaled = float(np.max(np.abs(score)) / max(int(result.nobs), 1))
        checks["maximum_scaled_score"] = scaled if np.isfinite(scaled) else None
    except (ValueError, TypeError, AttributeError, NotImplementedError, np.linalg.LinAlgError):
        checks["maximum_scaled_score"] = None
    tolerance = config.get("maximum_scaled_score")
    family = config.get("family")
    checks["parameter_boundary_gate"] = bool(
        family in {"gaussian", "lognormal", "gamma"}
        or np.max(np.abs(params)) <= config.get("maximum_absolute_parameter", 30)
    )
    checks["dispersion_boundary_gate"] = bool(family not in {"negbin", "zinb"} or params[-1] >= 1e-6)
    checks["probability_boundary_gate"] = True
    checks["fitted_support_gate"] = True
    if hasattr(result, "predict"):
        predicted = np.asarray(result.predict())
        support = bool(np.isfinite(predicted).all())
        if family in {"beta", "binomial", "ordinal_logit"}:
            support = support and bool(np.all((predicted >= 0) & (predicted <= 1)))
        elif family in {"gamma", "poisson", "negbin", "zip", "zinb"}:
            support = support and bool(np.all(predicted > 0 if family == "gamma" else predicted >= 0))
        elif family == "lognormal":
            with np.errstate(over="ignore", under="ignore"):
                response_means = np.exp(predicted)
            support = support and bool(np.isfinite(response_means).all() and np.all(response_means > 0))
        if family == "beta" and hasattr(result.model, "exog_precision"):
            precision = result.model.predict(params, which="precision")
            support = support and bool(np.isfinite(precision).all() and np.all(precision > 0))
        if family == "ordinal_logit":
            support = support and bool(np.allclose(predicted.sum(axis=1), 1))
        checks["fitted_support_gate"] = support
    if family in {"beta", "binomial"} and checks["finite_estimates"]:
        predicted = np.asarray(result.predict())
        checks["probability_boundary_gate"] = bool(
            np.isfinite(predicted).all() and np.all((predicted >= 1e-8) & (predicted <= 1 - 1e-8))
        )
    checks["configured_score_tolerance"] = tolerance
    diagnostics = native_diagnostics(result, config)
    checks["diagnostics"] = diagnostics
    checks["critical_diagnostics_gate"] = (
        not diagnostics["critical_failures"] if not diagnostics["unavailable"] else None
    )
    checks["score_gate"] = (
        (checks["maximum_scaled_score"] is not None and checks["maximum_scaled_score"] <= tolerance)
        if tolerance is not None
        else None
    )
    checks["accepted"] = (
        all(
            checks[key]
            for key in (
                "optimizer_converged",
                "finite_estimates",
                "finite_likelihood",
                "covariance_valid",
                "identifiable",
                "parameter_boundary_gate",
                "dispersion_boundary_gate",
                "probability_boundary_gate",
                "fitted_support_gate",
                "critical_diagnostics_gate",
            )
        )
        and checks["score_gate"] is not False
        and checks.get("information_stability_gate") is not False
    )
    checks["independently_checked_stationary_point"] = checks["accepted"] and checks["score_gate"] is True
    checks["optimum_status"] = (
        "Local stationarity and covariance checks passed; global optimality is not established"
        if checks["independently_checked_stationary_point"]
        else "Independent stationarity not established; no reviewer-configured score gate"
        if tolerance is None
        else "Independent acceptance checks failed"
    )
    return checks


def fit_accepted(result):
    record = getattr(result, "_stats_fit_execution", None)
    return record["checks"]["accepted"] if record else optimizer_converged(result)


def custom_fit_checks(fit, config, model=None):
    tolerance = config.get("maximum_scaled_score")
    scaled = fit.max_abs_grad / max(fit.n_obs, 1)
    valid_covariance = (
        fit.cov_model is not None
        and fit.cov_hc3 is not None
        and all(
            np.isfinite(cov).all() and np.linalg.eigvalsh((cov + cov.T) / 2).min() > 0
            for cov in (fit.cov_model, fit.cov_hc3)
        )
    )
    checks = dict(
        optimizer_converged=bool(fit.converged),
        termination=fit.message,
        iterations=fit.n_iter,
        finite_estimates=bool(np.isfinite(fit.theta).all()),
        finite_likelihood=bool(np.isfinite(fit.loglik)),
        identifiable=bool(fit.hessian_pd),
        covariance_valid=bool(valid_covariance),
        maximum_scaled_score=float(scaled) if np.isfinite(scaled) else None,
        configured_score_tolerance=tolerance,
        score_gate=(bool(np.isfinite(scaled) and scaled <= tolerance) if tolerance is not None else None),
    )
    condition_limit = config.get("maximum_information_condition", 1e10)
    checks["information_stability_gate"] = (
        bool(np.isfinite(fit.hessian_cond) and fit.hessian_cond <= condition_limit)
        if condition_limit is not None and fit.hessian_cond is not None
        else None
    )
    critical = []
    if fit.leverage is not None and (not np.isfinite(fit.leverage).all() or np.max(fit.leverage) >= 1 - 1e-8):
        critical.append("Undefined or unit leverage")
    cooks_limit = config.get("maximum_cooks_distance", 1)
    if (
        cooks_limit is not None
        and fit.cooks is not None
        and (not np.isfinite(fit.cooks).all() or np.max(fit.cooks) > cooks_limit)
    ):
        critical.append("Influence exceeds configured generalized Cook distance threshold")
    checks["critical_diagnostics_gate"] = not critical
    checks["critical_diagnostics"] = critical
    checks["fitted_support_gate"] = True
    if model is not None:
        means = model.family.mean(model.etas(fit.theta), model.aux)
        support = np.isfinite(means).all() and np.all(means >= 0)
        if model.family.name in {"zoib", "betabinomial"}:
            support = support and np.all(means <= 1)
        checks["fitted_support_gate"] = bool(support)
    checks["accepted"] = (
        all(
            checks[key]
            for key in (
                "optimizer_converged",
                "finite_estimates",
                "finite_likelihood",
                "identifiable",
                "covariance_valid",
                "critical_diagnostics_gate",
                "fitted_support_gate",
            )
        )
        and checks["score_gate"] is not False
        and checks["information_stability_gate"] is not False
    )
    checks["independently_checked_stationary_point"] = checks["accepted"] and checks["score_gate"] is True
    return checks


def refit_summary(records):
    """Compact HTML audit; full per-draw records remain in the saved tables."""
    checks = [record.get("checks", record) for record in records]
    gates = {
        "optimizer_converged",
        "finite_estimates",
        "finite_likelihood",
        "identifiable",
        "covariance_valid",
        "parameter_boundary_gate",
        "dispersion_boundary_gate",
        "probability_boundary_gate",
        "score_gate",
        "fitted_support_gate",
        "information_stability_gate",
        "critical_diagnostics_gate",
    }
    failed = Counter(key for check in checks for key, value in check.items() if key in gates and value is False)
    scores = [check["maximum_scaled_score"] for check in checks if check.get("maximum_scaled_score") is not None]
    return dict(
        recorded_fits=len(records),
        optimizer_converged=sum(bool(c.get("optimizer_converged")) for c in checks),
        accepted=sum(bool(c.get("accepted")) for c in checks),
        independently_checked_stationary_points=sum(
            bool(c.get("independently_checked_stationary_point")) for c in checks
        ),
        fits_with_configured_score_gate=sum(c.get("configured_score_tolerance") is not None for c in checks),
        fits_with_retries=sum(len(record.get("attempts", [])) > 1 for record in records),
        failed_checks=dict(sorted(failed.items())),
        maximum_scaled_score=max(scores) if scores else None,
    )
