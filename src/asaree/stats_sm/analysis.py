"""Fit established models; only descriptive summaries are assembled here.

No imports from asaree.stats: numerical fitting and inference belong to
statsmodels/scikit-learn, and design coding belongs to patsy.
"""

from __future__ import annotations

import itertools
import warnings

import numpy as np
import pandas as pd
import patsy
import statsmodels.api as sm
from sklearn.linear_model import TweedieRegressor
from statsmodels.discrete.count_model import ZeroInflatedNegativeBinomialP, ZeroInflatedPoisson
from statsmodels.miscmodels.ordinal_model import OrderedModel
from statsmodels.othermod.betareg import BetaModel
from statsmodels.stats.multitest import multipletests

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

UNAVAILABLE = [
    ("Automatic family selection", "The original construction-based selection and diagnostic filtering are custom."),
    ("Beta-binomial regression", "No direct mapping in the selected libraries."),
    ("Zero/one-inflated beta regression", "No direct mapping in the selected libraries."),
    ("Hurdle models", "The statsmodels count hurdle parameterization differs from the original logistic hurdle."),
    ("Estimated Tweedie power and density", "TweedieRegressor takes a fixed power and does not fit dispersion."),
    ("Parametric bootstrap intervals and bootstrap LRT", "The original simulation/refitting procedure is custom."),
    ("Generalized HC3 covariance", "The original multi-part likelihood HC3 construction has no direct mapping here."),
    ("PIT diagnostics and simulation envelopes", "The original simulation-based diagnostic pipeline is custom."),
    ("ART / ART-C", "No mapping in statsmodels or scikit-learn."),
    ("Design-preserving permutation tests", "The original stratified / Freedman–Lane orchestration is custom."),
    ("Cross-method agreement and effect stability", "These depend on the original custom sensitivity pipeline."),
    (
        "Response-scale contrast intervals",
        "Shown only for Gaussian linear contrasts. Marginal intervals also use native GLM prediction where supported.",
    ),
]


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
    notes = ["Model-based inference; no custom HC3 or bootstrap. Marginal means weight factorial cells equally."]
    notes.append(
        "Design columns use patsy sum coding: "
        + ", ".join(f"{alias} = {name}" for alias, name in zip(aliases, factors, strict=True))
    )
    tables = {}
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
            if family in ("gaussian", "lognormal"):
                if family == "lognormal" and np.any(y <= 0):
                    raise ValueError("Log-normal requires positive outcomes.")
                result = sm.OLS(np.log(y) if family == "lognormal" else y, x).fit()
            elif family == "negbin":
                result = sm.NegativeBinomial(y, x, loglike_method="nb2").fit(disp=0, maxiter=500)
            elif family in ("zip", "zinb"):
                cls = ZeroInflatedPoisson if family == "zip" else ZeroInflatedNegativeBinomialP
                result = cls(y, x, exog_infl=np.ones((len(y), 1))).fit(disp=0, maxiter=500)
            elif family == "beta":
                result = BetaModel(y, x).fit(method="bfgs", gtol=1e-9, disp=0, maxiter=500)
            elif family == "ordinal_logit":
                levels = config["ordinal_levels"]
                codes = pd.Series(frame["outcome_raw"]).map({v: i for i, v in enumerate(levels)})
                if codes.isna().any() or len(levels) < 2:
                    raise ValueError("Declare every ordinal level in order.")
                y = codes.to_numpy(float)
                result = OrderedModel(y, x[:, 1:], distr="logit").fit(method="bfgs", disp=0, maxiter=500)
            else:
                families = {
                    "gamma": sm.families.Gamma(sm.families.links.Log()),
                    "poisson": sm.families.Poisson(),
                    "binomial": sm.families.Binomial(),
                }
                response = y
                if family == "binomial" and config.get("denominator"):
                    trials = frame["trials"].to_numpy(float)
                    response = np.column_stack((y, trials - y))
                result = sm.GLM(response, x, family=families[family]).fit()

            def predict(matrix):
                if family in ("zip", "zinb"):
                    return result.predict(matrix, exog_infl=np.ones((len(matrix), 1)))
                if family == "ordinal_logit":
                    return result.model.predict(result.params, matrix[:, 1:]) @ np.arange(
                        1, len(config["ordinal_levels"]) + 1
                    )
                values = result.predict(matrix)
                if family == "lognormal":
                    # Gaussian ML variance on log y, with the response-scale mean correction.
                    return np.exp(values + np.sum(result.resid**2) / len(y) / 2)
                return values

            means, fitted = np.asarray(predict(grid)), np.asarray(predict(x))
            intervals = np.asarray(result.conf_int())
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
            tables["coefficients"] = pd.DataFrame(
                dict(
                    parameter=names,
                    estimate=np.asarray(result.params),
                    se=np.asarray(result.bse),
                    p=np.asarray(result.pvalues),
                    ci_low=intervals[:, 0],
                    ci_high=intervals[:, 1],
                )
            )
            converged = getattr(result, "converged", getattr(result, "mle_retvals", {}).get("converged", True))
            tables["model_fit"] = pd.DataFrame(
                [dict(family=family, n=len(y), loglik=result.llf, aic=result.aic, bic=result.bic, converged=converged)]
            )
            if family in ("gaussian", "lognormal"):
                notes.append(
                    "OLS reports t/F inference and counts regression coefficients in AIC. "
                    "The original engine estimates Gaussian dispersion by maximum likelihood "
                    "and uses different inference."
                )
            if family == "lognormal":
                notes.append(
                    "Log-normal model-fit likelihood/AIC are on log(y); they are not directly comparable "
                    "to the original response-scale likelihood/AIC. Predictions include the mean correction."
                )
            if family == "gamma":
                notes.append(
                    "Gamma uses statsmodels' native scale estimate; the original engine estimates Gamma shape by ML."
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
                try:
                    test = result.wald_test(restriction, scalar=True)
                    rows.append(dict(term=term, statistic=float(test.statistic), p=float(test.pvalue)))
                except (ValueError, np.linalg.LinAlgError):
                    notes.append(f"No usable covariance for {term}; the library cannot provide a Wald test.")
                    rows.append(dict(term=term, statistic=np.nan, p=np.nan))
            tests = pd.DataFrame(rows)
            tests["p_holm"] = np.nan
            valid = tests.p.notna()
            if valid.any():
                tests.loc[valid, "p_holm"] = multipletests(tests.loc[valid, "p"], method="holm")[1]
            tables["tests"] = tests
        notes.extend(str(w.message) for w in caught)

    labels = [
        ", ".join(f"{name}={row[a]}" for name, a in zip(factors, aliases, strict=True)) for _, row in cells.iterrows()
    ]
    cell_table = cells.rename(columns=dict(zip(aliases, factors, strict=True))).copy()
    cell_table["label"], cell_table["estimate"] = labels, means
    if result is not None and family in ("gaussian", "gamma", "poisson", "binomial"):
        ci = np.asarray(result.get_prediction(grid).conf_int())
        cell_table["ci_low"], cell_table["ci_high"] = ci[:, 0], ci[:, 1]
    tables["cell_means"] = cell_table
    marginal, contrasts = [], []
    ratio = family in ("gamma", "poisson", "negbin", "zip", "zinb", "lognormal", "tweedie")

    def summarize(weights, label, **meta):
        estimate = float(weights @ means)
        row = dict(label=label, estimate=estimate, **meta)
        if family == "gaussian":
            test = result.t_test(weights @ grid)
            ci = np.asarray(test.conf_int()).ravel()
            row.update(ci_low=float(ci[0]), ci_high=float(ci[1]), p=float(np.asarray(test.pvalue).item()))
        elif family in ("gamma", "poisson", "binomial") and np.all(weights >= 0):
            # statsmodels itself averages nonlinear predictions and propagates their covariance.
            ci = np.asarray(result.get_prediction(grid[weights > 0], which="mean", average=True).conf_int()).ravel()
            row.update(ci_low=float(ci[0]), ci_high=float(ci[1]))
        return row

    def weights_for(conditions):
        mask = np.ones(len(cells), bool)
        for a, level in conditions.items():
            mask &= cells[a].to_numpy() == level
        return mask.astype(float) / mask.sum()

    def comparison(weights, label, family_name):
        pieces = np.asarray([w @ means for w in weights])
        if ratio:
            estimate = pieces[0] / pieces[1] if len(weights) == 2 else (pieces[0] / pieces[1]) / (pieces[2] / pieces[3])
            return dict(
                label=label,
                family=family_name,
                estimate=float(estimate),
                scale="ratio" if len(weights) == 2 else "ratio_of_ratios",
            )
        combined = weights[0] - weights[1]
        if len(weights) == 4:
            combined = combined - weights[2] + weights[3]
        return summarize(
            combined,
            label,
            family=family_name,
            scale="difference" if len(weights) == 2 else "difference_in_differences",
        )

    for name, a in zip(factors, aliases, strict=True):
        levels = sorted(data[a].unique())
        for level in levels:
            marginal.append(summarize(weights_for({a: level}), f"{name}={level}", factor=name))
        for left, right in itertools.combinations(levels, 2):
            contrasts.append(
                comparison(
                    [weights_for({a: left}), weights_for({a: right})], f"{name}: {left} vs {right}", f"main:{name}"
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
                    )
                )
    tables["marginal_means"] = pd.DataFrame(marginal)
    con = pd.DataFrame(contrasts)
    if "p" in con:
        con["p_holm"] = con.groupby("family").p.transform(lambda p: multipletests(p, method="holm")[1])
    tables["contrasts"] = con
    observed = frame["outcome"].to_numpy(float)
    if family == "binomial" and config.get("denominator"):
        observed = observed / frame["trials"].to_numpy(float)
    if family == "ordinal_logit":
        observed = y + 1
    tables["residuals"] = pd.DataFrame(dict(observed=observed, fitted=fitted, residual=observed - fitted))
    return {
        "tables": tables,
        "notes": list(dict.fromkeys(notes)),
        "unavailable": [dict(title=title, reason=reason, reference="/stats") for title, reason in UNAVAILABLE],
        "family": family,
    }
