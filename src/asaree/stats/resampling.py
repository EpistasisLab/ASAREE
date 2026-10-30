"""Fixed-family case bootstrap and interaction null resampling using native model refits."""

from __future__ import annotations

import time
import warnings
from collections import Counter

import numpy as np
import pandas as pd
from scipy import stats


def bootstrap_intervals(frame, x, y, grid, weights, family, config, ratio, trials=None):
    from asaree.stats.analysis import converged_fit, fit_model, predict_model

    requested = config["n_boot"]
    rng = np.random.default_rng(np.random.SeedSequence([config["seed"], 1101]))
    groups = list(frame.groupby([f"factor_{i}" for i in range(len(config["factors"]))]).indices.values())
    draws, failures, attempted = [], 0, 0
    failure_reasons = Counter()
    refit_checks = []
    start = time.monotonic()
    # A bounded worker task must never silently present an incomplete simulation as complete.
    # Production draws are count-bounded, never silently machine-speed dependent.
    # An explicit deadline is for cancellation/testing and yields incomplete output.
    budget = config.get("_bootstrap_budget_s")
    for _ in range(requested):
        if budget is not None and time.monotonic() - start >= budget:
            break
        attempted += 1
        indices = np.concatenate([rng.choice(group, size=len(group), replace=True) for group in groups])
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = fit_model(y[indices], x[indices], family, config, None if trials is None else trials[indices])
                refit_checks.append(dict(draw=attempted, **result._stats_fit_execution))
                # fit_model already lets other native optimizers finish a stalled fit.
                if not converged_fit(result) or not np.isfinite(result.params).all():
                    raise ValueError("Nonconverged bootstrap refit.")
                covariance = np.asarray(result.cov_params())
                if not np.isfinite(covariance).all() or np.linalg.eigvalsh((covariance + covariance.T) / 2).min() <= 0:
                    raise ValueError("Unidentified bootstrap refit.")
                if family not in {"gaussian", "lognormal", "gamma"} and np.max(np.abs(result.params)) > config.get(
                    "maximum_absolute_parameter", 30
                ):
                    raise ValueError("Boundary bootstrap refit.")
                means = np.asarray(predict_model(result, grid, family, config))
                quantities = []
                for pieces in weights:
                    values = np.asarray([w @ means for w in pieces])
                    if ratio:
                        if np.any(values <= 0):
                            raise ValueError("Undefined bootstrap ratio.")
                        value = values[0] / values[1]
                        if len(values) == 4:
                            value /= values[2] / values[3]
                    else:
                        value = values[0] - values[1]
                        if len(values) == 4:
                            value -= values[2] - values[3]
                    quantities.append(value)
                if not np.isfinite(quantities).all():
                    raise ValueError("Nonfinite bootstrap prediction.")
                draws.append(quantities)
        except (ValueError, RuntimeError, np.linalg.LinAlgError, FloatingPointError, OverflowError) as exc:
            failures += 1
            failure_reasons[str(exc)] += 1
    minimum = config.get("minimum_success_fraction", 0.9)
    usable = attempted == requested and len(draws) >= minimum * requested
    bounds = np.quantile(draws, [0.025, 0.975], axis=0) if usable else np.full((2, len(weights)), np.nan)
    status = "completed" if usable else "incomplete" if attempted < requested else "unavailable"
    metadata = dict(
        kind="bootstrap",
        procedure="case_bootstrap",
        method="Cell-stratified case bootstrap; fixed selected model; percentile intervals",
        requested=requested,
        attempted=attempted,
        successful=len(draws),
        failed=failures,
        status=status,
        seed=config["seed"],
        seed_stream=1101,
        minimum_success_fraction=minimum,
        reason="Time budget exceeded" if attempted < requested else "Too many failed refits" if not usable else "",
        failure_reasons=dict(sorted(failure_reasons.items())),
        refit_checks=refit_checks,
    )
    return pd.DataFrame(dict(bootstrap_ci_low=bounds[0], bootstrap_ci_high=bounds[1])), metadata


INTERACTION_SEED_STREAM = 3303
FREEDMAN_LANE = {
    "gaussian": (
        "response",
        "no interaction in the expected value on the response scale (difference of differences = 0)",
    ),
    "lognormal": ("log response", "no interaction on the log scale, i.e. the ratio of ratios of expected values = 1"),
    "gamma": ("log response", "no interaction on the log-mean scale, i.e. the ratio of ratios of expected values = 1"),
}
NULL_BOOTSTRAP = {
    "poisson": "no interaction on the log-mean scale, i.e. the ratio of ratios of expected counts = 1",
    "negbin": "no interaction on the log-mean scale, i.e. the ratio of ratios of expected counts = 1",
    "binomial": "no interaction on the logit scale, i.e. the ratio of odds ratios = 1",
}
WITHHELD = {
    "zip": "residual-permutation and null-model simulation for zero-inflated models are not implemented here",
    "zinb": "residual-permutation and null-model simulation for zero-inflated models are not implemented here",
    "beta": "residual-permutation and null-model simulation for beta regression are not implemented here",
    "ordinal_logit": "residual-permutation and null-model simulation for ordered categories are not implemented here",
}
ASSUMPTIONS = {
    "gaussian": "Independent runs whose additive errors are exchangeable across all conditions (equal variance); "
    "normality is not required. Nuisance terms are estimated; this is an approximate permutation procedure, "
    "not a guarantee of finite-sample exactness.",
    "lognormal": "Independent runs whose log-scale errors are exchangeable across all conditions (constant log-scale "
    "variance). Estimated nuisance terms make finite-sample permutation inference approximate.",
    "gamma": "Independent runs and a constant Gamma shape: then y = mean × e with e independent and identically "
    "distributed, so log(y) has additive exchangeable errors and no interaction in log(mean) is the same null as no "
    "interaction in E[log y]. Estimated nuisance terms make finite-sample permutation inference approximate.",
    "poisson": "The fitted term-reduced Poisson model is the correct data-generating distribution. Count errors "
    "are not generally exchangeable (their variance follows the mean); this procedure uses a parametric "
    "bootstrap, not a permutation test.",
    "negbin": "The fitted term-reduced NB2 model (with estimated dispersion) is the correct data-generating "
    "distribution. Count errors are not generally exchangeable (variance follows the mean); "
    "this procedure uses a parametric bootstrap, not a permutation test.",
    "binomial": "The fitted term-reduced binomial model is the correct data-generating distribution. Binary/"
    "binomial errors are not generally exchangeable (variance follows probability); "
    "this procedure uses a parametric bootstrap, not a permutation test.",
}


def freedman_lane_sampler(rng, null_fitted, residual, exponentiate):
    def draw():
        values = null_fitted + residual[rng.permutation(len(residual))]
        return np.exp(values) if exponentiate else values

    return draw


def null_model_sampler(rng, family, linear, params, trials):
    def draw():
        if family == "poisson":
            return rng.poisson(np.exp(linear)).astype(float)
        if family == "negbin":
            alpha = params[-1]
            return rng.negative_binomial(1 / alpha, 1 / (1 + alpha * np.exp(linear))).astype(float)
        probability = 1 / (1 + np.exp(-linear))
        return rng.binomial(1 if trials is None else trials.astype(int), probability).astype(float)

    return draw


def interaction_null_tests(frame, x, y, design, family, config, trials, result, covariance_record, inference_reason):
    """Resample the fitted model's own HC3 Wald statistic for each interaction term.

    Nuisance main effects are kept by resampling around the fitted reduced
    (no-interaction) model:

    * Gaussian / log-normal / Gamma: Freedman–Lane permutation of reduced-model
      residuals on the scale where the errors are additive and exchangeable.
    * Poisson / NB2 / binomial: their errors are not exchangeable, so the null
      distribution is simulated from the fitted reduced model (parametric
      bootstrap) and labelled as such.

    Each draw refits the selected family and recomputes its HC3 Wald statistic,
    so the resampled statistic is the analytic one, not a working-scale OLS F.
    """
    from asaree.stats.analysis import converged_fit, fit_model, wald_for_term
    from asaree.stats.covariance import CovarianceUnavailableError, robust_covariance

    rows = []
    requested = config["n_perm"]
    budget = config.get("_interaction_budget_s")
    covariance = np.asarray(result.cov_params())
    for term, column_slice in design.design_info.term_name_slices.items():
        if ":" not in term:
            continue
        idx = np.arange(x.shape[1])[column_slice]
        reduced_x = np.delete(x, idx, axis=1)
        row = dict(
            term=term,
            kind="permutation"
            if family in FREEDMAN_LANE
            else "parametric bootstrap"
            if family in NULL_BOOTSTRAP
            else None,
            statistic=f"HC3 Wald chi-square of the fitted {family} model",
            df=len(idx),
            observed_statistic=None,
            requested=requested,
            attempted=0,
            completed=0,
            failed=0,
            p_perm=None,
            null_hypothesis=FREEDMAN_LANE.get(family, (None, NULL_BOOTSTRAP.get(family)))[1],
            assumptions=ASSUMPTIONS.get(family),
            seed=config["seed"],
            seed_stream=INTERACTION_SEED_STREAM,
            reason="",
            status="not_run",
            procedure=None,
            minimum_success_fraction=config.get("minimum_success_fraction", 0.9),
            removed_columns=[str(design.columns[i]) for i in idx],
            retained_columns=[str(column) for i, column in enumerate(design.columns) if i not in idx],
        )
        row["null_hypothesis"] = (
            f"All sum-coded coefficients for {term} are zero on the fitted model's scale; "
            "only this term is removed, retaining all other fitted terms."
        )
        if family in FREEDMAN_LANE:
            scale = FREEDMAN_LANE[family][0]
            row.update(
                method=f"Freedman–Lane permutation of term-reduced-model residuals on the {scale} scale; "
                "whole runs exchanged across all conditions",
                working_scale=scale,
                procedure="freedman_lane",
            )
        elif family in NULL_BOOTSTRAP:
            row.update(
                method=f"Parametric bootstrap from the fitted term-reduced {family} model (null simulation)",
                working_scale="model",
                procedure="null_parametric_bootstrap",
            )
        else:
            row.update(
                method="Not run",
                availability="not_implemented",
                statistic=None,
                reason=f"Interaction resampling unavailable: {WITHHELD.get(family, family)}.",
            )
            rows.append(row)
            continue
        if inference_reason:
            row.update(reason=f"Interaction resampling not run: {inference_reason}")
            rows.append(row)
            continue
        if (covariance_record or {}).get("computed") != "HC3":
            row.update(reason="Interaction resampling not run: the analytic HC3 statistic is unavailable.")
            rows.append(row)
            continue
        observed = wald_for_term(result, covariance, idx)
        if observed is None or not np.isfinite(observed):
            row.update(reason="Interaction resampling not run: the observed Wald statistic is undefined.")
            rows.append(row)
            continue
        row["observed_statistic"] = observed
        rng = np.random.default_rng(np.random.SeedSequence([config["seed"], INTERACTION_SEED_STREAM]))
        if family in FREEDMAN_LANE:
            working = np.log(y) if family in ("lognormal", "gamma") else y
            groups = [working[i] for i in frame.groupby([c for c in frame if c.startswith("factor_")]).indices.values()]
            gate_p = float(stats.levene(*groups, center="median").pvalue)
            threshold = config.get("exchangeability_alpha", 0.01)
            row["diagnostic_gate"] = dict(
                method="Brown–Forsythe (median-centered Levene)",
                p=gate_p,
                threshold=threshold,
                limitation="Passing does not establish exchangeability",
            )
            if not np.isfinite(gate_p) or gate_p < threshold:
                row.update(
                    availability="assumptions_not_supported",
                    reason="Interaction permutation withheld: unequal variances across conditions on the "
                    f"{FREEDMAN_LANE[family][0]} scale undermine residual exchangeability.",
                )
                rows.append(row)
                continue
            coefficients = np.linalg.lstsq(reduced_x, working, rcond=None)[0]
            null_fitted = reduced_x @ coefficients

            draw = freedman_lane_sampler(rng, null_fitted, working - null_fitted, family in ("lognormal", "gamma"))
        else:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    null = fit_model(y, reduced_x, family, config, trials)
                null_params = np.asarray(null.params)
                row["null_fit_checks"] = null._stats_fit_execution
                if not converged_fit(null) or not np.isfinite(null_params).all():
                    raise ValueError("the no-interaction model did not converge")
                if family == "negbin" and null_params[-1] < 1e-6:
                    raise ValueError("the no-interaction NB2 dispersion is at its boundary")
            except (ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
                row.update(reason=f"Interaction bootstrap not run: {exc}.")
                rows.append(row)
                continue
            draw = null_model_sampler(rng, family, reduced_x @ null_params[: reduced_x.shape[1]], null_params, trials)

        draws, exceed, start = [], 0, time.monotonic()
        failure_reasons = Counter()
        refit_checks = []
        for _ in range(requested):
            if budget is not None and time.monotonic() - start >= budget:
                break
            row["attempted"] += 1
            simulated = draw()
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    refit = fit_model(simulated, x, family, config, trials)
                    refit_checks.append(dict(draw=row["attempted"], **refit._stats_fit_execution))
                    if not converged_fit(refit) or not np.isfinite(refit.params).all():
                        raise ValueError("Nonconverged null refit.")
                    refit_covariance, record = robust_covariance(refit, family, config.get("hc3_fallback", "hc0"))
                    statistic = wald_for_term(refit, refit_covariance, idx)
                if statistic is None or not np.isfinite(statistic) or record["computed"] != "HC3":
                    raise ValueError("Undefined null statistic.")
            except (
                ValueError,
                RuntimeError,
                np.linalg.LinAlgError,
                FloatingPointError,
                CovarianceUnavailableError,
            ) as exc:
                row["failed"] += 1
                failure_reasons[str(exc)] += 1
                continue
            draws.append(statistic)
            exceed += statistic >= observed * (1 - 1e-10)
        row["completed"] = len(draws)
        row["failure_reasons"] = dict(sorted(failure_reasons.items()))
        row["refit_checks"] = refit_checks
        row["exceedances"] = exceed
        if row["attempted"] < requested:
            row["status"] = "incomplete"
            row["reason"] = f"Time budget exceeded after {row['attempted']} of {requested} draws."
        elif len(draws) < row["minimum_success_fraction"] * requested:
            row["status"] = "unavailable"
            row["reason"] = f"Too many failed refits ({row['failed']} of {requested})."
        else:
            row["status"] = "completed"
            row["p_perm"] = (1 + exceed) / (len(draws) + 1)
        rows.append(row)
    return rows
