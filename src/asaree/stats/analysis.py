"""Fit established models; only descriptive summaries are assembled here.

Numerical fitting and inference belong to statsmodels/scikit-learn, and
design coding belongs to patsy. The custom likelihood engine in this package
is used only where no published package covers the model.
"""

from __future__ import annotations

import contextlib
import itertools
import warnings

import numpy as np
import pandas as pd
import patsy
import statsmodels.api as sm
from scipy import stats
from sklearn.linear_model import TweedieRegressor
from statsmodels.discrete.count_model import ZeroInflatedNegativeBinomialP, ZeroInflatedPoisson
from statsmodels.miscmodels.ordinal_model import OrderedModel
from statsmodels.othermod.betareg import BetaModel
from statsmodels.stats.multitest import multipletests

from asaree.stats.covariance import (
    CovarianceUnavailableError,
    install_covariance,
    robust_covariance,
    singular_directions,
)

FAMILIES = (
    "gaussian",
    "lognormal",
    "gamma",
    "poisson",
    "negbin",
    "zip",
    "zinb",
    "beta",
    "binomial",
    "ordinal_logit",
    "tweedie",
)


def fit_model(y, x, family, config, trials=None):
    """Shared native fit for the original sample, bootstrap refits and null simulations.

    Always the model-based fit: robust covariance is computed separately by
    ``covariance.robust_covariance`` because statsmodels' ``cov_type="HC3"``
    silently means HC0 outside OLS.
    """
    if family in ("gaussian", "lognormal"):
        return sm.OLS(np.log(y) if family == "lognormal" else y, x).fit()
    if family == "negbin":
        return finish_fit(sm.NegativeBinomial(y, x, loglike_method="nb2").fit(disp=0, maxiter=500))
    if family in ("zip", "zinb"):
        cls = ZeroInflatedPoisson if family == "zip" else ZeroInflatedNegativeBinomialP
        return finish_fit(cls(y, x, exog_infl=np.ones((len(y), 1))).fit(disp=0, maxiter=500))
    if family == "beta":
        return finish_fit(BetaModel(y, x).fit(method="bfgs", gtol=1e-9, disp=0, maxiter=500))
    if family == "ordinal_logit":
        if len(config["ordinal_levels"]) < 2 or len(np.unique(y)) != len(config["ordinal_levels"]):
            raise ValueError("A bootstrap sample omitted an ordinal category.")
        return finish_fit(OrderedModel(y, x[:, 1:], distr="logit").fit(method="bfgs", disp=0, maxiter=500))
    families = {
        "gamma": sm.families.Gamma(sm.families.links.Log()),
        "poisson": sm.families.Poisson(),
        "binomial": sm.families.Binomial(),
    }
    response = np.column_stack((y, trials - y)) if family == "binomial" and trials is not None else y
    return sm.GLM(response, x, family=families[family]).fit()


def finish_fit(result):
    """Let other native optimizers finish a maximum-likelihood fit that stopped early.

    Newton (NB2) and BFGS (beta) can stop just short of an optimum, notably when
    the NB2 dispersion is small. L-BFGS, then Newton, restart from the last
    estimate; convergence is only ever what a native optimizer reports.
    """
    for method in ("lbfgs", "newton"):
        if converged_fit(result) or not np.isfinite(result.params).all():
            break
        attempt = result.model.fit(start_params=result.params, method=method, disp=0, maxiter=500)
        if not np.isfinite(attempt.params).all():
            break
        result = attempt
    return result


def converged_fit(result):
    return bool(getattr(result, "converged", getattr(result, "mle_retvals", {}).get("converged", True)))


def comparable_likelihood(result, family, y):
    """Response-scale log-likelihood at the MLE of every parameter, and its parameter count.

    AIC is only comparable across candidates when each likelihood is for the
    same observations on the same (response) scale and counts every estimated
    parameter. statsmodels' OLS AIC omits the residual variance, OLS on log(y)
    is a density for log(y), and its Gamma GLM log-likelihood uses the Pearson
    scale estimate (not the MLE) and omits the shape parameter from AIC.
    """
    k = len(result.params)
    if family in ("gaussian", "lognormal"):
        llf = float(result.llf) - (float(np.log(y).sum()) if family == "lognormal" else 0.0)
        basis = "OLS profile likelihood plus the residual variance" + (
            "; log(y) density converted to the response scale with the Jacobian" if family == "lognormal" else ""
        )
        return llf, k + 1, basis, {}
    if family == "gamma":
        from scipy.optimize import minimize_scalar

        model = result.model

        def negative(log_scale):
            return -model.family.loglike(
                model.endog, result.mu, model.var_weights, model.freq_weights, np.exp(log_scale)
            )

        start = np.log(max(float(result.scale), 1e-300))
        optimum = minimize_scalar(negative, bracket=(start - 1, start + 1))
        return (
            float(-optimum.fun),
            k + 1,
            "Gamma likelihood with the shape estimated by maximum likelihood",
            dict(gamma_shape_ml=float(np.exp(-optimum.x))),
        )
    return float(result.llf), k, "native maximum likelihood", {}


def residual_check(y, fitted):
    """True when every observation equals its fitted value (no residual variation)."""
    scale = max(float(np.max(np.abs(y))), np.finfo(float).tiny)
    return bool(np.max(np.abs(np.asarray(y, float) - np.asarray(fitted, float))) <= 1e-7 * scale)


def wald_for_term(result, covariance, idx):
    """HC3 Wald chi-square for the coefficients in ``idx`` (None when undefined)."""
    params = np.asarray(result.params)
    restriction = np.eye(len(params))[idx]
    if singular_directions(covariance, restriction):
        return None
    beta = params[idx]
    return float(beta @ np.linalg.solve(covariance[np.ix_(idx, idx)], beta))


def predict_model(result, matrix, family, config):
    if family in ("zip", "zinb"):
        return result.predict(matrix, exog_infl=np.ones((len(matrix), 1)))
    if family == "ordinal_logit":
        return result.model.predict(result.params, matrix[:, 1:]) @ np.arange(1, len(config["ordinal_levels"]) + 1)
    values = result.predict(matrix)
    if family == "lognormal":
        return np.exp(values + np.sum(result.resid**2) / len(result.resid) / 2)
    return values


def analyze(frame: pd.DataFrame, config: dict) -> dict:
    family = config["family"]
    if family not in FAMILIES:
        raise ValueError(f"No established mapping for {family}; see the original /stats implementation.")
    factors = config["factors"]
    # Safe internal aliases: user factor names never become formula source code.
    data = pd.DataFrame({f"f{i}": frame[f"factor_{i}"].astype(str) for i in range(len(factors))})
    aliases = list(data.columns)
    terms = ["C(" + alias + ", Sum)" for alias in aliases]
    order = min(config["max_order"], len(terms))
    formula = "1 + " + " + ".join(":".join(t) for k in range(1, order + 1) for t in itertools.combinations(terms, k))
    design = patsy.dmatrix(formula, data, return_type="dataframe")
    x = np.asarray(design)
    y = frame["outcome"].to_numpy(float)
    if np.linalg.matrix_rank(x) < x.shape[1] or len(y) <= x.shape[1] + 2:
        raise ValueError("Design is rank deficient or has too few residual observations; lower interaction order.")
    cells = pd.DataFrame(itertools.product(*[sorted(data[a].unique()) for a in aliases]), columns=aliases)
    grid = np.asarray(patsy.build_design_matrices([design.design_info], cells)[0])
    notes = [
        "Native covariance inference; nonlinear comparison intervals use the delta method. "
        "Marginal means weight factorial cells equally."
    ]
    notes.append(
        "Design columns use patsy sum coding: "
        + ", ".join(f"{alias} = {name}" for alias, name in zip(aliases, factors, strict=True))
    )
    tables = {}
    robust = config.get("robust", False)
    usable, inference_reason, covariance_record = False, None, None
    covariance_label = "model-based covariance"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if family == "tweedie":
            if np.any(y < 0):
                raise ValueError("Tweedie requires nonnegative outcomes.")
            fit = TweedieRegressor(
                power=config["tweedie_power"], alpha=0, fit_intercept=False, link="log", max_iter=1000, tol=1e-9
            ).fit(x, y)
            predict = fit.predict
            means = predict(grid)
            fitted = predict(x)
            tables["coefficients"] = pd.DataFrame({"parameter": design.design_info.column_names, "estimate": fit.coef_})
            notes.append("Tweedie: fixed power; likelihood, AIC, coefficient intervals and tests unavailable.")
            result = None
        else:
            if family == "lognormal" and np.any(y <= 0):
                raise ValueError("Log-normal requires positive outcomes.")
            if family == "ordinal_logit":
                codes = pd.Series(frame["outcome_raw"]).map({v: i for i, v in enumerate(config["ordinal_levels"])})
                if codes.isna().any():
                    raise ValueError("Declare every ordinal level in order.")
                y = codes.to_numpy(float)
            trials = frame["trials"].to_numpy(float) if config.get("denominator") else None
            result = fit_model(y, x, family, config, trials)

            def predict(matrix):
                return predict_model(result, matrix, family, config)

            means, fitted = np.asarray(predict(grid)), np.asarray(predict(x))
            converged = converged_fit(result)
            # Exact fit: no residual variation, so no sampling uncertainty can be estimated.
            # Checked on the scale the model is fitted on (log scale for log-normal OLS).
            exact = family not in ("ordinal_logit",) and residual_check(
                np.log(y) if family == "lognormal" else y if trials is None else y / trials,
                np.asarray(result.fittedvalues) if family in ("gaussian", "lognormal") else fitted,
            )
            # Identification uses the unit-scale information, which an exact fit does not destroy.
            try:
                normalized = np.asarray(result.normalized_cov_params, float)
            except (AttributeError, ValueError, TypeError):
                normalized = np.full((len(result.params), len(result.params)), np.nan)
            reasons = []
            if not converged:
                reasons.append("the optimizer did not converge")
            if not np.isfinite(result.params).all():
                reasons.append("non-finite parameter estimates")
            if not (np.isfinite(normalized).all() and np.linalg.eigvalsh((normalized + normalized.T) / 2).min() > 0):
                reasons.append("parameters are not identified (singular information matrix)")
            if not np.isfinite(means).all():
                reasons.append("non-finite predicted means")
            llf, n_parameters, likelihood_basis, likelihood_extra = (np.nan, len(result.params), "unavailable", {})
            with contextlib.suppress(ValueError, FloatingPointError, np.linalg.LinAlgError):
                llf, n_parameters, likelihood_basis, likelihood_extra = comparable_likelihood(result, family, y)
            aic = -2 * llf + 2 * n_parameters if np.isfinite(llf) else np.nan
            if exact and family not in ("poisson", "binomial"):
                # The dispersion MLE sits on its boundary (zero variance), so the likelihood is unbounded.
                llf, aic = np.nan, np.nan
                likelihood_basis = "undefined: exact fit puts the dispersion estimate on its boundary"
            if not np.isfinite(aic) and not exact:
                reasons.append("non-finite likelihood")
            if family in ("binomial", "beta") and np.any((fitted < 1e-8) | (fitted > 1 - 1e-8)):
                reasons.append("boundary probabilities or separation")
            if family not in ("gaussian", "lognormal", "gamma") and np.max(np.abs(result.params)) > 30:
                reasons.append("extreme parameters suggest separation or a boundary fit")
            if family in ("negbin", "zinb") and np.isfinite(result.params[-1]) and result.params[-1] < 1e-6:
                reasons.append(
                    "the negative-binomial dispersion is at its boundary (alpha ≈ 0); "
                    "the model reduces to Poisson and alpha is not identified"
                )
            diagnostic = dict(family=family, severe=False, flags="")
            flags = []
            if family in ("gaussian", "lognormal") and len(y) >= 20 and not exact:
                residual = np.asarray(result.resid)
                diagnostic["residual_skewness"] = float(stats.skew(residual))
                diagnostic["residual_normality_p"] = float(stats.normaltest(residual).pvalue)
                if diagnostic["residual_normality_p"] < 0.001 and abs(diagnostic["residual_skewness"]) > 1:
                    flags.append("Residuals are strongly skewed for this Gaussian assumption.")
                    diagnostic["severe"] = True
            pearson = getattr(result, "resid_pearson", None)
            if family in ("poisson", "binomial", "negbin", "gamma") and pearson is not None and result.df_resid > 0:
                diagnostic["pearson_dispersion"] = float(np.sum(np.asarray(pearson) ** 2) / result.df_resid)
                if family in ("poisson", "binomial") and diagnostic["pearson_dispersion"] > 3:
                    flags.append("Residual variation substantially exceeds this model's variance assumption.")
                    diagnostic["severe"] = True
            if family == "negbin":
                diagnostic["negbin_alpha"] = float(result.params[-1])
            diagnostic.update(likelihood_extra)
            diagnostic["flags"] = " ".join(flags)
            # Advanced manual runs retain diagnostic flags. Automatic selection
            # excludes these failed assumptions, in addition to numerical failures.
            if robust and diagnostic["severe"]:
                reasons.append(diagnostic["flags"])
            usable = not reasons
            tables["diagnostics"] = pd.DataFrame([diagnostic])
            notes.extend(flags)
            if exact:
                inference_reason = (
                    "Exact fit: every replicate equals its fitted value, so there is no residual variation "
                    "from which to estimate sampling uncertainty. Estimates are descriptive only."
                )
            covariance = np.asarray(result.cov_params()) if usable else None
            if robust and (exact or not usable):
                covariance_record = dict(
                    requested="HC3",
                    computed=None,
                    reason=inference_reason if exact else "the model failed validation",
                )
            if usable and robust and not exact:
                try:
                    covariance, covariance_record = robust_covariance(result, family)
                    install_covariance(result, covariance, covariance_record)
                    covariance_label = f"{covariance_record['computed']} robust covariance"
                except (CovarianceUnavailableError, np.linalg.LinAlgError) as exc:
                    inference_reason = f"Robust covariance unavailable: {exc}."
                    covariance_record = dict(requested="HC3", computed=None, reason=str(exc))
            if usable and inference_reason is None and singular_directions(covariance, np.eye(len(covariance))):
                notes.append(
                    "The parameter covariance is singular in at least one direction; tests and comparisons "
                    "that depend on that direction are reported as unavailable."
                )
            intervals = np.asarray(result.conf_int()) if usable and inference_reason is None else None
            base_names = list(design.design_info.column_names)
            native_names = list(result.model.exog_names)
            if family in ("zip", "zinb"):
                names = [native_names[0], *base_names, *native_names[1 + len(base_names) :]]
            elif family == "ordinal_logit":
                names = [*base_names[1:], *native_names[len(base_names) - 1 :]]
            else:
                names = [*base_names, *native_names[len(base_names) :]]
            if len(names) != len(result.params):
                names = names[: len(result.params)]
                names += [f"ancillary_{i}" for i in range(len(result.params) - len(names))]
            missing = np.full(len(result.params), np.nan)
            tables["coefficients"] = pd.DataFrame(
                dict(
                    parameter=names,
                    estimate=np.asarray(result.params),
                    se=np.asarray(result.bse) if intervals is not None else missing,
                    p=np.asarray(result.pvalues) if intervals is not None else missing,
                    ci_low=intervals[:, 0] if intervals is not None else missing,
                    ci_high=intervals[:, 1] if intervals is not None else missing,
                )
            )
            tables["model_fit"] = pd.DataFrame(
                [
                    dict(
                        family=family,
                        n=len(y),
                        loglik=llf,
                        aic=aic,
                        n_parameters=n_parameters,
                        likelihood_basis=likelihood_basis,
                        native_aic=float(result.aic) if np.isfinite(result.aic) else np.nan,
                        converged=converged,
                        usable=usable,
                        exclusion_reason="; ".join(reasons),
                        exact_fit=exact,
                        inference_reason=inference_reason or "",
                        covariance=(covariance_record or {}).get("computed")
                        or ("model-based" if not robust else "unavailable"),
                    )
                ]
            )
            if family in ("gaussian", "lognormal"):
                notes.append(
                    "OLS coefficients; robust inference uses z/chi-square reference distributions. AIC counts the "
                    "residual variance as an estimated parameter."
                )
            if family == "gamma":
                notes.append(
                    "Gamma AIC uses the maximum-likelihood shape; HC3 inference does not depend on the dispersion."
                )
            if not converged:
                notes.append("WARNING: established optimizer did not converge; inspect coefficients and warnings.")
            # Established Wald tests on design terms, padded for ancillary / inflation parameters.
            rows = []
            offset = 1 if family in ("zip", "zinb") else 0
            for term, column_slice in design.design_info.term_name_slices.items():
                if term == "Intercept":
                    continue
                idx = np.arange(x.shape[1])[column_slice] + offset - (family == "ordinal_logit")
                restriction = np.eye(len(result.params))[idx]
                row = dict(term=term, statistic=np.nan, df=len(idx), p=np.nan, p_method="Wald test unavailable")
                if not usable:
                    row["unavailable_reason"] = "The model failed validation."
                elif inference_reason:
                    row["unavailable_reason"] = inference_reason
                elif singular_directions(covariance, restriction):
                    row["unavailable_reason"] = (
                        f"The {covariance_label} is singular for this term (no residual variation in the "
                        "conditions that identify it), so its Wald statistic is undefined."
                    )
                else:
                    try:
                        test = result.wald_test(restriction, scalar=True)
                        test_name = "Wald F-test" if test.distribution == "F" else "Wald chi-square test"
                        row.update(
                            statistic=float(test.statistic),
                            p=float(test.pvalue),
                            p_method=f"{test_name} ({covariance_label})",
                        )
                    except (ValueError, np.linalg.LinAlgError):
                        row["unavailable_reason"] = "The library cannot provide a Wald test for this term."
                rows.append(row)
            tests = pd.DataFrame(rows)
            tests["p_holm"] = np.nan
            valid = tests.p.notna()
            if valid.any():
                tests.loc[valid, "p_holm"] = multipletests(tests.loc[valid, "p"], method="holm")[1]
            tables["tests"] = tests
        notes.extend(str(w.message) for w in caught)

    inferable = result is not None and usable and inference_reason is None
    labels = [
        ", ".join(f"{name}={row[a]}" for name, a in zip(factors, aliases, strict=True)) for _, row in cells.iterrows()
    ]
    cell_table = cells.rename(columns=dict(zip(aliases, factors, strict=True))).copy()
    cell_table["label"], cell_table["estimate"] = labels, means
    if inferable and family in ("gaussian", "gamma", "poisson", "binomial"):
        ci = np.asarray(result.get_prediction(grid).conf_int())
        cell_table["ci_low"], cell_table["ci_high"] = ci[:, 0], ci[:, 1]
    tables["cell_means"] = cell_table
    marginal, contrasts, comparison_weights = [], [], []
    ratio = family in ("gamma", "poisson", "negbin", "zip", "zinb", "lognormal", "tweedie")
    response_spread = float(np.std(y)) if len(y) else 0.0

    def summarize(weights, label, **meta):
        estimate = float(weights @ means)
        row = dict(label=label, estimate=estimate, **meta)
        if family == "gaussian" and inferable:
            vector = weights @ grid
            variance = float(vector @ covariance @ vector)
            floor = (1e-8 * max(response_spread, 1e-300)) ** 2
            if singular_directions(covariance, vector[None, :]) or variance <= floor:
                row["unavailable_reason"] = "The robust variance of this quantity is numerically zero."
                return row
            test = result.t_test(vector)
            ci = np.asarray(test.conf_int()).ravel()
            row.update(ci_low=float(ci[0]), ci_high=float(ci[1]), p=float(np.asarray(test.pvalue).item()))
            row["p_method"] = (
                "Student's t-test for a linear contrast"
                if test.distribution == "t"
                else "Wald z-test for a linear contrast"
            ) + f" ({covariance_label})"
            row["ci_method"] = (
                "Student's t confidence interval"
                if test.distribution == "t"
                else "Normal-approximation confidence interval"
            ) + f" ({covariance_label})"
        elif family in ("gamma", "poisson", "binomial") and inferable and np.all(weights >= 0):
            # statsmodels itself averages nonlinear predictions and propagates their covariance.
            ci = np.asarray(result.get_prediction(grid[weights > 0], which="mean", average=True).conf_int()).ravel()
            row.update(ci_low=float(ci[0]), ci_high=float(ci[1]))
        return row

    def response_interval(weights, estimate, ratio_scale=False):
        """Delta-method inference for the reported response-scale quantity.

        Perturb parameters through the native prediction API. Work on log ratios
        to keep ratio intervals positive; never substitute coefficient tests.
        """
        if not inferable:
            return {}
        params = np.asarray(result.params).copy()

        def quantity(theta):
            matrix = grid[:, 1:] if family == "ordinal_logit" else grid
            kwargs = {"exog_infl": np.ones((len(grid), 1))} if family in ("zip", "zinb") else {}
            values = np.asarray(result.model.predict(theta, matrix, **kwargs))
            if family == "ordinal_logit":
                values = values @ np.arange(1, len(config["ordinal_levels"]) + 1)
            elif family == "lognormal":
                values = np.exp(values + np.sum(result.resid**2) / len(y) / 2)
            pieces = np.asarray([w @ values for w in weights])
            if ratio_scale:
                if np.any(pieces <= 0):
                    return np.nan
                return (
                    np.log(pieces[0])
                    - np.log(pieces[1])
                    - (np.log(pieces[2]) - np.log(pieces[3]) if len(pieces) == 4 else 0)
                )
            return pieces[0] - pieces[1] - (pieces[2] - pieces[3] if len(pieces) == 4 else 0)

        gradient = np.empty(len(params))
        for k in range(len(params)):
            step = 1e-5 * (1 + abs(params[k]))
            delta = np.zeros(len(params))
            delta[k] = step
            gradient[k] = (quantity(params + delta) - quantity(params - delta)) / (2 * step)
        variance = float(gradient @ covariance @ gradient)
        # A log ratio is unit-free; a difference is compared with the outcome's spread.
        floor = 1e-8 if ratio_scale else 1e-8 * max(response_spread, 1e-300)
        if not np.isfinite(variance) or variance <= floor**2 or singular_directions(covariance, gradient[None, :]):
            return dict(
                unavailable_reason=f"The {covariance_label} gives this comparison a numerically zero variance, "
                "so its test statistic and interval are undefined."
            )
        se = np.sqrt(variance)
        center = np.log(estimate) if ratio_scale else estimate
        bounds = center + np.array([-1, 1]) * stats.norm.ppf(0.975) * se
        if ratio_scale:
            bounds = np.exp(bounds)
        return dict(
            ci_low=float(bounds[0]),
            ci_high=float(bounds[1]),
            se=float(se),
            statistic=float(center / se),
            p=float(2 * stats.norm.sf(abs(center / se))),
            p_method=f"Delta-method Wald z-test{' of the log ratio' if ratio_scale else ''} ({covariance_label})",
            ci_method=(
                "Log-scale delta-method confidence interval" if ratio_scale else "Delta-method confidence interval"
            )
            + f" ({covariance_label})",
        )

    def weights_for(conditions):
        mask = np.ones(len(cells), bool)
        for a, level in conditions.items():
            mask &= cells[a].to_numpy() == level
        return mask.astype(float) / mask.sum()

    def comparison(weights, label, family_name, **meta):
        comparison_weights.append(weights)
        pieces = np.asarray([w @ means for w in weights])
        if ratio:
            estimate = pieces[0] / pieces[1] if len(weights) == 2 else (pieces[0] / pieces[1]) / (pieces[2] / pieces[3])
            row = dict(
                label=label,
                family=family_name,
                estimate=float(estimate),
                scale="ratio" if len(weights) == 2 else "ratio_of_ratios",
                **meta,
            )
            if result is not None:
                row.update(response_interval(weights, estimate, True))
            return row
        combined = weights[0] - weights[1]
        if len(weights) == 4:
            combined = combined - weights[2] + weights[3]
        row = summarize(
            combined,
            label,
            family=family_name,
            scale="difference" if len(weights) == 2 else "difference_in_differences",
            **meta,
        )
        if result is not None and family != "gaussian":
            row.update(response_interval(weights, row["estimate"]))
        return row

    for name, a in zip(factors, aliases, strict=True):
        levels = sorted(data[a].unique())
        for level in levels:
            marginal.append(summarize(weights_for({a: level}), f"{name}={level}", factor=name))
        for left, right in itertools.combinations(levels, 2):
            contrasts.append(
                comparison(
                    [weights_for({a: left}), weights_for({a: right})],
                    f"{name}: {left} vs {right}",
                    f"main:{name}",
                    factors=[name],
                    levels=[[left, right]],
                    averaged_over=[f for f in factors if f != name],
                )
            )
    for i, j in itertools.combinations(range(len(factors)), 2):
        a, b = aliases[i], aliases[j]
        for a1, a2 in itertools.combinations(sorted(data[a].unique()), 2):
            for b1, b2 in itertools.combinations(sorted(data[b].unique()), 2):
                weights = [weights_for({a: av, b: bv}) for av, bv in ((a1, b1), (a2, b1), (a1, b2), (a2, b2))]
                contrasts.append(
                    comparison(
                        weights,
                        f"{factors[i]}({a1} vs {a2}) x {factors[j]}({b1} vs {b2})",
                        f"int:{factors[i]}x{factors[j]}",
                        factors=[factors[i], factors[j]],
                        levels=[[a1, a2], [b1, b2]],
                        averaged_over=[f for k, f in enumerate(factors) if k not in (i, j)],
                    )
                )
    tables["marginal_means"] = pd.DataFrame(marginal)
    con = pd.DataFrame(contrasts)
    if "p" in con:
        con["p_holm"] = np.nan
        valid = con.p.notna()
        con.loc[valid, "p_holm"] = (
            con[valid].groupby("family").p.transform(lambda p: multipletests(p, method="holm")[1])
        )
    tables["contrasts"] = con
    if config.get("_resample") and result is not None and usable:
        from asaree.stats.resampling import bootstrap_intervals, interaction_null_tests

        if inferable:
            boot, metadata = bootstrap_intervals(frame, x, y, grid, comparison_weights, family, config, ratio, trials)
            for column in boot:
                con[column] = boot[column]
        else:
            metadata = dict(
                kind="bootstrap",
                method="Cell-stratified case bootstrap; fixed selected model; percentile intervals",
                requested=config["n_boot"],
                attempted=0,
                successful=0,
                failed=0,
                status=f"Not run: {inference_reason.rstrip('.')}",
                seed=config["seed"],
                seed_stream=1101,
                minimum_success_fraction=0.9,
            )
        tables["resampling"] = pd.DataFrame([metadata])
        tables["interaction_resampling"] = pd.DataFrame(
            interaction_null_tests(
                frame, x, y, design, family, config, trials, result, covariance_record, inference_reason
            )
        )
    observed = frame["outcome"].to_numpy(float)
    if family == "binomial" and config.get("denominator"):
        observed = observed / frame["trials"].to_numpy(float)
    if family == "ordinal_logit":
        observed = y + 1
    tables["residuals"] = pd.DataFrame(dict(observed=observed, fitted=fitted, residual=observed - fitted))
    validity = "valid" if result is not None and usable else "unreliable"
    if validity != "valid":
        # Estimates are descriptive only; failed fits must never supply inference.
        for table in tables.values():
            for column in table:
                if column in {"p", "p_holm", "ci_low", "ci_high", "se"}:
                    table[column] = np.nan
    return {
        "tables": tables,
        "notes": list(dict.fromkeys(notes)),
        "family": family,
        "validity": validity,
        "covariance": covariance_record,
        "inference_reason": inference_reason,
    }
