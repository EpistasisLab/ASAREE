"""Custom likelihood fits for models with no published-package equivalent.

Zero/one-inflated beta, hurdle Gamma and beta-binomial regression have no
statsmodels implementation. They use the likelihood engine supplied with the
original statistics package (engine.py, families.py): Newton fits, generalized
HC3 covariance, parametric-bootstrap intervals and working-scale permutation
checks. Only the outputs the automatic report reads are produced.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .contrasts import QuantitySet, build_contrasts, default_families
from .design import DesignBuilder, DesignSpec, cell_index, full_factorial_terms, term_name
from .diagnostics import diagnose
from .engine import Model
from .families import make_family
from .inference import (
    Predictor,
    constraint_matrix,
    holm,
    parametric_bootstrap,
    percentile_ci,
    response_contrast_wald,
    wald_test,
)
from .nonparametric import permutation_tests

CUSTOM_FAMILIES = ("zoib", "gamma_hurdle", "betabinomial")
N_SIM_DIAGNOSTICS = 250
Z_975 = 1.959963984540054


def permutation_scale(values, effect_scale):
    if effect_scale != "ratio":
        return values, "response scale"
    if np.all(values > 0):
        return np.log(values), "log scale (matches the log-link ratio effects)"
    shift = np.min(values[values > 0]) / 2.0
    return np.log(values + shift), f"log(y + {shift:.4g}) because of exact zeros (approximate match to ratio effects)"


def analyze_custom(frame: pd.DataFrame, config: dict) -> dict:
    """Fit one custom family and return the tables the automatic report reads.

    ``frame`` comes from ``prepare_frame``: columns factor_0.., outcome and,
    for beta-binomial, trials.
    """
    family = config["family"]
    if family not in CUSTOM_FAMILIES:
        raise ValueError(f"{family} is not a custom-engine family.")
    factors = [f"factor_{i}" for i in range(len(config["factors"]))]
    frame = frame.reset_index(drop=True)
    y = frame.outcome.to_numpy(float)
    aux = {"trials": frame.trials.to_numpy(float)} if config.get("denominator") else {}
    if np.ptp(y) == 0:
        raise ValueError("constant outcome")
    levels = {name: sorted(pd.unique(frame[name]), key=str) for name in factors}
    terms = full_factorial_terms(factors, config["max_order"])
    builder = DesignBuilder(DesignSpec(factors, levels, terms))
    x = builder.build(frame)
    if np.linalg.matrix_rank(x) < x.shape[1] or len(frame) <= x.shape[1] + 2:
        raise ValueError("Design is rank deficient or has too few residual observations; lower interaction order.")
    cells = builder.cell_table()
    cell_idx = cell_index(frame, factors, levels)
    labels = [", ".join(f"{f}={row[f]}" for f in factors) for _, row in cells.iterrows()]
    # Same seed streams as the original pipeline (selection, diagnostics, bootstrap, LRT, permutation).
    streams = np.random.SeedSequence(config["seed"]).spawn(1)[0].spawn(5)
    rng_diagnostics, boot_seed = np.random.default_rng(streams[1]), streams[2]
    rng_permutation = np.random.default_rng(streams[4])

    model_family = make_family(family)
    unsupported = model_family.check_support(y, aux)
    if unsupported:
        raise ValueError(f"{family} {unsupported}")
    model_family.configure(y, aux)
    model = Model.from_base(model_family, y, x, aux)
    fit = model.fit()
    if not fit.converged or not fit.hessian_pd:
        raise ValueError("the requested model did not converge with usable information")
    diagnostic = diagnose(model, fit, cell_idx, labels, N_SIM_DIAGNOSTICS, rng_diagnostics)
    diagnostics = {k: v for k, v in diagnostic.items() if not k.startswith("_") and k not in ("ppc", "flags")}
    diagnostics.update(family=family, flags="; ".join(diagnostic["flags"]))

    contrast_families = default_families(factors)
    contrasts = [c for fam in contrast_families for c in build_contrasts(fam, cells, levels)]
    quantity_set = QuantitySet(
        cells, factors, levels, contrasts, model.family.effect_scale, model.family.logit_link_effects
    )
    predictor = Predictor(model, builder)
    estimates = quantity_set.evaluate(*predictor.cells(fit.theta))
    draws, failures = parametric_bootstrap(model, fit, predictor, quantity_set, config["n_boot"], boot_seed, 1, True)
    quantities = quantity_set.meta.copy()
    quantities["estimate"] = estimates
    quantities["ci_low"], quantities["ci_high"] = percentile_ci(draws, 0.95)
    quantities["n_boot_ok"] = int(np.sum(~np.isnan(draws).any(axis=1)))
    quantities["n_boot_failed"] = failures

    # Response-scale delta-method HC3 tests and intervals for every comparison.
    contrast_estimate, contrast_se, contrast_p = response_contrast_wald(model, fit, predictor, quantity_set)
    bounds = contrast_estimate[None, :] + np.array([-Z_975, Z_975])[:, None] * contrast_se
    if model.family.effect_scale == "ratio":
        bounds = np.exp(bounds)
    for column, values in (("analytic_ci_low", bounds[0]), ("analytic_ci_high", bounds[1])):
        quantities[column] = np.nan
        quantities.loc[quantities.index[quantity_set.contrast_slice()], column] = values

    multi_part = len(model.regression_blocks) > 1
    eye = np.eye(x.shape[1])
    rows = []
    for term in terms:
        name = term_name(term)
        statistic, df, p = wald_test(fit.theta, fit.cov_hc3, constraint_matrix(model, eye[builder.term_columns[name]]))
        rows.append(
            dict(
                family="terms",
                hypothesis=name,
                wald_hc3_chi2=statistic,
                df=df,
                p_wald_hc3=p,
                null_hypothesis="term coefficients = 0"
                + (" in every model part (joint)" if multi_part else "")
                + " (Type III, sum-to-zero coding)",
            )
        )
    for k, contrast in enumerate(contrasts):
        rows.append(
            dict(
                family=contrast.family,
                hypothesis=contrast.label,
                response_scale_se=contrast_se[k],
                p_wald_response_hc3=contrast_p[k],
                null_hypothesis="response-scale contrast = 0" if multi_part else "link-scale contrast = 0",
            )
        )
    tests = pd.DataFrame(rows)
    tests["p_response_holm"] = np.nan
    for name, index in tests.groupby("family").groups.items():
        if name != "terms":
            tests.loc[index, "p_response_holm"] = holm(tests.loc[index, "p_wald_response_hc3"].to_numpy())
    comparisons = quantities[quantities.quantity == "contrast"].reset_index(drop=True)
    comparisons = comparisons.merge(tests.rename(columns=dict(hypothesis="label")), on=["family", "label"], how="left")

    # Main effects only: interaction permutations are not justified for these multipart models.
    working = y / aux["trials"] if "trials" in aux else y
    working, scale_note = permutation_scale(working, model.family.effect_scale)
    permutations = permutation_tests(working, frame, builder, config["n_perm"], rng_permutation)
    permutations["p_holm"] = holm(permutations.p_perm.to_numpy())
    withheld = [
        dict(term=term_name(term), statistic="Type III F (OLS, working scale)", n_perm=0, method="Permutation withheld")
        for term in terms
        if len(term) > 1
    ]
    permutations = pd.concat([permutations, pd.DataFrame(withheld)], ignore_index=True)
    permutations["working_scale"] = scale_note
    return dict(
        family=family,
        tables=dict(
            diagnostics=pd.DataFrame([diagnostics]),
            quantities=quantities,
            tests=tests,
            contrasts=comparisons,
            permutation_tests=permutations,
        ),
    )
