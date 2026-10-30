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
    parametric = config.get("bootstrap_method", "parametric") == "parametric"
    distribution = None
    if parametric:
        from asaree.stats.fitted_distributions import fitted_distribution

        with warnings.catch_warnings(record=True) as generating_warnings:
            warnings.simplefilter("always")
            original = fit_model(y, x, family, config, trials)
        if not converged_fit(original):
            raise ValueError("The original parametric-bootstrap fit failed acceptance checks")
        distribution = fitted_distribution(original, family, config, trials)
    start = time.monotonic()
    # A bounded worker task must never silently present an incomplete simulation as complete.
    # Production draws are count-bounded, never silently machine-speed dependent.
    # An explicit deadline is for cancellation/testing and yields incomplete output.
    budget = config.get("_bootstrap_budget_s")
    for _ in range(requested):
        if budget is not None and time.monotonic() - start >= budget:
            break
        attempted += 1
        indices = (
            np.arange(len(y))
            if parametric
            else np.concatenate([rng.choice(group, size=len(group), replace=True) for group in groups])
        )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                simulated = distribution.draw(rng, len(y)) if parametric else y[indices]
                result = fit_model(simulated, x[indices], family, config, None if trials is None else trials[indices])
                refit_checks.append(dict(draw=attempted, **result._stats_fit_execution))
                # fit_model already lets other native optimizers finish a stalled fit.
                if not converged_fit(result) or not np.isfinite(result.params).all():
                    raise ValueError("Bootstrap refit failed configured acceptance checks.")
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
    tail = (1 - config.get("confidence_level", 0.95)) / 2
    bounds = np.quantile(draws, [tail, 1 - tail], axis=0) if usable else np.full((2, len(weights)), np.nan)
    status = "completed" if usable else "incomplete" if attempted < requested else "unavailable"
    metadata = dict(
        kind="bootstrap",
        procedure="fitted_model_parametric_bootstrap" if parametric else "case_bootstrap",
        method=(
            "Fitted-model parametric bootstrap; fixed design and selected family; percentile intervals"
            if parametric
            else "Cell-stratified case bootstrap; fixed selected model; percentile intervals"
        ),
        distribution=distribution.construction if parametric else None,
        generating_fit=original._stats_fit_execution if parametric else None,
        generating_fit_warnings=[str(w.message) for w in generating_warnings] if parametric else [],
        confidence_level=config.get("confidence_level", 0.95),
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
}
QUANTILE_PERMUTATION = {"gamma", "poisson", "negbin", "binomial", "beta", "zip", "zinb", "ordinal_logit"}
ASSUMPTIONS = {
    "gaussian": "Independent runs whose additive errors are exchangeable across all conditions (equal variance); "
    "normality is not required. Nuisance terms are estimated; this is an approximate permutation procedure, "
    "not a guarantee of finite-sample exactness.",
    "lognormal": "Independent runs whose log-scale errors are exchangeable across all conditions (constant log-scale "
    "variance). Estimated nuisance terms make finite-sample permutation inference approximate.",
}


def freedman_lane_sampler(rng, null_fitted, residual, exponentiate):
    def draw():
        values = null_fitted + residual[rng.permutation(len(residual))]
        return np.exp(values) if exponentiate else values

    return draw


def quantile_residual_sampler(rng, distribution, values, frame, blocks):
    """Permute fitted-null PIT residuals; reconstruct on each original run's support."""
    missing = [column for column in blocks if column not in frame]
    if missing:
        raise ValueError("Exchangeability block columns are missing: " + ", ".join(missing))
    if blocks and frame[blocks].isna().any().any():
        raise ValueError("Exchangeability block labels are missing for some observations")
    uniforms = distribution.pit(values, rng)
    groups = (
        list(frame.groupby(blocks, sort=True, dropna=False).indices.values()) if blocks else [np.arange(len(values))]
    )
    if not any(len(group) > 1 for group in groups):
        raise ValueError("No exchangeability block contains more than one observation")

    def draw():
        permuted = uniforms.copy()
        for group in groups:
            permuted[group] = uniforms[rng.permutation(group)]
        return distribution.ppf(permuted)

    return draw


def interaction_null_tests(frame, x, y, design, family, config, trials, result, covariance_record, inference_reason):
    """Interaction permutations using the same fitted-model Wald statistic.

    Gaussian/log-normal models use additive residual Freedman–Lane. Other
    supported native families use the approved PIT/quantile analogue. Estimated
    null distributions make this approximate, not an exact randomization test.
    No null-model parametric bootstrap is substituted for a permutation p-value.
    """
    from asaree.stats.analysis import converged_fit, fit_model, wald_for_term
    from asaree.stats.covariance import CovarianceUnavailableError, robust_covariance

    rows = []
    requested = config["n_perm"]
    budget = config.get("_interaction_budget_s")
    covariance = np.asarray(result.cov_params())
    estimator = (covariance_record or {}).get("computed")
    for term, column_slice in design.design_info.term_name_slices.items():
        if ":" not in term:
            continue
        design_idx = np.arange(x.shape[1])[column_slice]
        idx = design_idx + (1 if family in {"zip", "zinb"} else -1 if family == "ordinal_logit" else 0)
        reduced_x = np.delete(x, design_idx, axis=1)
        row = dict(
            term=term,
            kind="permutation" if family in FREEDMAN_LANE or family in QUANTILE_PERMUTATION else None,
            statistic=f"{estimator or 'Unavailable covariance'} Wald chi-square of the fitted {family} model",
            df=len(idx),
            observed_statistic=None,
            requested=requested,
            attempted=0,
            completed=0,
            failed=0,
            p_perm=None,
            assumptions=ASSUMPTIONS.get(family),
            seed=config["seed"],
            seed_stream=INTERACTION_SEED_STREAM,
            reason="",
            status="not_run",
            procedure=None,
            minimum_success_fraction=config.get("minimum_success_fraction", 0.9),
            removed_columns=[str(design.columns[i]) for i in design_idx],
            retained_columns=[str(column) for i, column in enumerate(design.columns) if i not in design_idx],
            exchangeability_blocks=config.get("exchangeability_blocks", []),
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
        elif family in QUANTILE_PERMUTATION:
            row.update(
                method=f"Approximate GLM Freedman–Lane analogue: fitted-null quantile-residual permutation ({family})",
                working_scale="Randomized PIT for discrete outcomes; fitted CDF for continuous outcomes",
                procedure="quantile_residual_permutation",
                assumptions="Independent observations or explicitly exchangeable observations within the named blocks; "
                "correct fitted null distribution and adequate residual exchangeability. Parameters and nuisance terms "
                "are estimated, so inference is approximate. Support preservation does not establish exchangeability.",
            )
        else:
            row.update(
                method="Not run",
                availability="not_implemented",
                statistic=None,
                reason=f"Interaction residual permutation is not implemented for {family}.",
            )
            rows.append(row)
            continue
        if inference_reason:
            row.update(reason=f"Interaction resampling not run: {inference_reason}")
            rows.append(row)
            continue
        if estimator not in {"HC3", "HC0"}:
            row.update(reason="Interaction resampling not run: the analytic robust statistic is unavailable.")
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
                    raise ValueError("the term-reduced model failed fit acceptance checks")
                if family == "negbin" and null_params[-1] < 1e-6:
                    raise ValueError("the term-reduced NB2 dispersion is at its boundary")
                from asaree.stats.fitted_distributions import fitted_distribution

                distribution = fitted_distribution(null, family, config, trials)
                draw = quantile_residual_sampler(rng, distribution, y, frame, config.get("exchangeability_blocks", []))
                row["diagnostic_gate"] = dict(
                    null_fit_accepted=True,
                    finite_interior_pit=True,
                    limitation="These numerical checks do not establish exchangeability",
                )
                row["null_distribution"] = distribution.construction
            except (ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
                row.update(reason=f"Interaction quantile-residual permutation not run: {exc}.")
                rows.append(row)
                continue

        draws, exceed, start = [], 0, time.monotonic()
        failure_reasons = Counter()
        refit_checks = []
        for _ in range(requested):
            if budget is not None and time.monotonic() - start >= budget:
                break
            row["attempted"] += 1
            try:
                simulated = draw()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    refit = fit_model(simulated, x, family, config, trials)
                    refit_checks.append(dict(draw=row["attempted"], **refit._stats_fit_execution))
                    if not converged_fit(refit) or not np.isfinite(refit.params).all():
                        raise ValueError("Permutation refit failed configured acceptance checks.")
                    refit_covariance, record = robust_covariance(
                        refit,
                        family,
                        config.get("hc3_fallback", "hc0"),
                        config.get("maximum_information_condition", 1e10),
                    )
                    statistic = wald_for_term(refit, refit_covariance, idx)
                if statistic is None or not np.isfinite(statistic) or record["computed"] != estimator:
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
