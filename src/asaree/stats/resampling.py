"""Fixed-family case bootstrap and interaction null resampling using native model refits."""

from __future__ import annotations

import time
import warnings

import numpy as np
import pandas as pd
from scipy import stats


def bootstrap_intervals(frame, x, y, grid, weights, family, config, ratio, trials=None):
    from asaree.stats.analysis import converged_fit, fit_model, predict_model

    requested = config["n_boot"]
    rng = np.random.default_rng(np.random.SeedSequence([config["seed"], 1101]))
    groups = list(frame.groupby([f"factor_{i}" for i in range(len(config["factors"]))]).indices.values())
    draws, failures, attempted = [], 0, 0
    start = time.monotonic()
    # A bounded worker task must never silently present an incomplete simulation as complete.
    budget = config.get("_bootstrap_budget_s", 60)
    for _ in range(requested):
        if time.monotonic() - start >= budget:
            break
        attempted += 1
        indices = np.concatenate([rng.choice(group, size=len(group), replace=True) for group in groups])
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = fit_model(y[indices], x[indices], family, config, None if trials is None else trials[indices])
                # fit_model already lets other native optimizers finish a stalled fit.
                if not converged_fit(result) or not np.isfinite(result.params).all():
                    raise ValueError("Nonconverged bootstrap refit.")
                covariance = np.asarray(result.cov_params())
                if not np.isfinite(covariance).all() or np.linalg.eigvalsh((covariance + covariance.T) / 2).min() <= 0:
                    raise ValueError("Unidentified bootstrap refit.")
                if family not in {"gaussian", "lognormal", "gamma"} and np.max(np.abs(result.params)) > 30:
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
        except (ValueError, RuntimeError, np.linalg.LinAlgError, FloatingPointError, OverflowError):
            failures += 1
    usable = attempted == requested and len(draws) >= 0.9 * requested
    bounds = np.quantile(draws, [0.025, 0.975], axis=0) if usable else np.full((2, len(weights)), np.nan)
    status = "completed" if usable else "Time budget exceeded" if attempted < requested else "Too many failed refits"
    metadata = dict(
        kind="bootstrap",
        method="Cell-stratified case bootstrap; fixed selected model; percentile intervals",
        requested=requested,
        attempted=attempted,
        successful=len(draws),
        failed=failures,
        status=status,
        seed=config["seed"],
        seed_stream=1101,
        minimum_success_fraction=0.9,
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
    "zip": "a zero-inflated model has no exchangeable residual, and simulating its null model is not implemented",
    "zinb": "a zero-inflated model has no exchangeable residual, and simulating its null model is not implemented",
    "beta": "beta regression has no exchangeable residual, and simulating its null model is not implemented",
    "ordinal_logit": "ordered categories have no exchangeable residual, and simulating their null model is not "
    "implemented",
}
ASSUMPTIONS = {
    "gaussian": "Independent runs whose additive errors are exchangeable across all conditions (equal variance); "
    "normality is not required. Freedman–Lane is asymptotically exact because the nuisance main effects are "
    "estimated, not known.",
    "lognormal": "Independent runs whose log-scale errors are exchangeable across all conditions (constant log-scale "
    "variance). Freedman–Lane is asymptotically exact because the nuisance main effects are estimated.",
    "gamma": "Independent runs and a constant Gamma shape: then y = mean × e with e independent and identically "
    "distributed, so log(y) has additive exchangeable errors and no interaction in log(mean) is the same null as no "
    "interaction in E[log y]. Freedman–Lane is asymptotically exact because the nuisance main effects are estimated.",
    "poisson": "The fitted no-interaction Poisson model is the correct data-generating distribution. Count errors "
    "are not exchangeable (their variance follows the mean), so no valid permutation exists; this is a parametric "
    "bootstrap, not a permutation test.",
    "negbin": "The fitted no-interaction NB2 model (with its estimated dispersion) is the correct data-generating "
    "distribution. Count errors are not exchangeable (their variance follows the mean), so no valid permutation "
    "exists; this is a parametric bootstrap, not a permutation test.",
    "binomial": "The fitted no-interaction binomial model is the correct data-generating distribution. Binary/"
    "binomial errors are not exchangeable (their variance follows the probability), so no valid permutation "
    "exists; this is a parametric bootstrap, not a permutation test.",
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
    budget = config.get("_interaction_budget_s", 120)
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
        )
        if family in FREEDMAN_LANE:
            scale = FREEDMAN_LANE[family][0]
            row.update(
                method=f"Freedman–Lane permutation of no-interaction residuals on the {scale} scale; "
                "whole runs exchanged across all conditions",
                working_scale=scale,
            )
        elif family in NULL_BOOTSTRAP:
            row.update(
                method=f"Parametric bootstrap from the fitted no-interaction {family} model (null simulation)",
                working_scale="model",
            )
        else:
            row.update(method="Withheld", reason=f"Interaction resampling unavailable: {WITHHELD.get(family, family)}.")
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
            if stats.levene(*groups, center="median").pvalue < 0.01:
                row.update(
                    reason="Interaction permutation withheld: unequal variances across conditions on the "
                    f"{FREEDMAN_LANE[family][0]} scale undermine residual exchangeability."
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
        for _ in range(requested):
            if time.monotonic() - start >= budget:
                break
            row["attempted"] += 1
            simulated = draw()
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    refit = fit_model(simulated, x, family, config, trials)
                    if not converged_fit(refit) or not np.isfinite(refit.params).all():
                        raise ValueError("Nonconverged null refit.")
                    refit_covariance, record = robust_covariance(refit, family)
                    statistic = wald_for_term(refit, refit_covariance, idx)
                if statistic is None or not np.isfinite(statistic) or record["computed"] != "HC3":
                    raise ValueError("Undefined null statistic.")
            except (ValueError, RuntimeError, np.linalg.LinAlgError, FloatingPointError, CovarianceUnavailableError):
                row["failed"] += 1
                continue
            draws.append(statistic)
            exceed += statistic >= observed * (1 - 1e-10)
        row["completed"] = len(draws)
        if row["attempted"] < requested:
            row["reason"] = f"Time budget exceeded after {row['attempted']} of {requested} draws."
        elif len(draws) < 0.9 * requested:
            row["reason"] = f"Too many failed refits ({row['failed']} of {requested})."
        else:
            row["p_perm"] = (1 + exceed) / (len(draws) + 1)
        rows.append(row)
    return rows
