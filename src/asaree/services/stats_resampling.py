"""Resampling orchestration and auditable sensitivity checks shared by reports."""

from __future__ import annotations

import numpy as np
from scipy import stats
from statsmodels.stats.multitest import multipletests


def native_permutations(frame, config):
    """Use the established design-preserving working-scale sensitivity procedure.

    This is not a permutation of GLM Wald statistics. The report identifies the
    different null and exchangeability assumptions, rather than equating the tests.
    """
    from asaree.stats.design import DesignBuilder, DesignSpec, full_factorial_terms
    from asaree.stats.nonparametric import permutation_tests

    factors = [f"factor_{i}" for i in range(len(config["factors"]))]
    builder = DesignBuilder(
        DesignSpec(
            factors=factors,
            levels={factor: sorted(frame[factor].unique()) for factor in factors},
            terms=full_factorial_terms(factors, config["max_order"]),
        )
    )
    values = frame.outcome.to_numpy(float)
    if config.get("denominator"):
        # Unequal denominators create unequal sampling variance, even under the null.
        if frame.trials.nunique() != 1:
            return [], "Permutation check unavailable: unequal trial totals do not justify exchanging proportions."
        values = values / frame.trials.to_numpy(float)
    if config["family"] == "ordinal_logit":
        values = stats.rankdata(values)
        scale = "pooled ranks of ordered categories (not equally spaced category scores)"
    elif config["family"] == "lognormal":
        values = np.log(values)
        scale = "log response"
    else:
        scale = "response"
    interactions = [":".join(term) for term in builder.spec.terms if len(term) > 1]
    skipped = {}
    if config["family"] not in {"gaussian", "lognormal"}:
        skipped = {
            term: "Interaction permutation unavailable: exchangeable additive errors are not established "
            "for this non-Gaussian model."
            for term in interactions
        }
    elif interactions:
        groups = [values[indices] for indices in frame.groupby(factors).indices.values()]
        if stats.levene(*groups, center="median").pvalue < 0.01:
            skipped = {
                term: "Interaction permutation unavailable: unequal cell variances undermine residual exchangeability."
                for term in interactions
            }
    rng = np.random.default_rng(np.random.SeedSequence([config["seed"], 2202]))
    result = permutation_tests(values, frame, builder, config["n_perm"], rng, batch=250, skip_terms=skipped)
    valid = np.isfinite(result.F_obs) & (result.df1 > 0) & (result.df2 > 0)
    result.loc[~valid, "p_perm"] = np.nan
    result["p_holm"] = np.nan
    if valid.any():
        result.loc[valid, "p_holm"] = multipletests(result.loc[valid, "p_perm"], method="holm")[1]
    result["working_scale"] = scale
    if "reason" in result:
        result["reason"] = result.reason.fillna("")
    result["seed"] = config["seed"]
    result["seed_stream"] = 2202
    return result.to_dict(orient="records"), None
