"""Serializable numerical checks, distinct from an optimizer's termination flag.

These checks do not establish a global optimum or validate statistical inference.
An independent score tolerance is applied only when configured by a reviewer.
"""

from __future__ import annotations

from collections import Counter

import numpy as np


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
    if family in {"beta", "binomial"} and checks["finite_estimates"]:
        predicted = np.asarray(result.predict())
        checks["probability_boundary_gate"] = bool(
            np.isfinite(predicted).all() and np.all((predicted >= 1e-8) & (predicted <= 1 - 1e-8))
        )
    checks["configured_score_tolerance"] = tolerance
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
            )
        )
        and checks["score_gate"] is not False
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


def custom_fit_checks(fit, config):
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
    checks["accepted"] = (
        all(
            checks[key]
            for key in (
                "optimizer_converged",
                "finite_estimates",
                "finite_likelihood",
                "identifiable",
                "covariance_valid",
            )
        )
        and checks["score_gate"] is not False
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
