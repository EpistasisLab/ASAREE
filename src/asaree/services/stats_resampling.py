"""Resampling orchestration and auditable sensitivity checks shared by reports."""

from __future__ import annotations

import numpy as np
from scipy import stats


def native_permutations(frame, config):
    """Use the established design-preserving working-scale sensitivity procedure.

    Main effects only: labels are exchanged within strata of the other factors,
    which is exact under the sharp null that the factor has no effect at any
    combination of the others. The statistic is a working-scale OLS F, not the
    fitted model's Wald statistic, so this is a sensitivity check whose null can
    differ from the analytic test. Holm adjustment happens after interaction
    resampling rows are merged in.
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
    interactions = {":".join(term) for term in builder.spec.terms if len(term) > 1}
    # Interactions are resampled with the fitted model's own statistic in
    # asaree.stats.resampling.interaction_null_tests; only main effects run here.
    skipped = dict.fromkeys(interactions, "")
    design = builder.build(frame)
    residual = values - design @ np.linalg.lstsq(design, values, rcond=None)[0]
    if np.max(np.abs(residual)) <= 1e-7 * max(float(np.max(np.abs(values))), np.finfo(float).tiny):
        reason = (
            f"Permutation check not run: on the {scale} scale every run equals its condition's fitted value, "
            "so the F statistic's residual mean square is zero and F is undefined."
        )
        return [
            dict(term=":".join(term), method="Permutation withheld", reason=reason, n_perm=0, working_scale=scale)
            for term in builder.spec.terms
            if len(term) == 1
        ], None
    rng = np.random.default_rng(np.random.SeedSequence([config["seed"], 2202]))
    result = permutation_tests(values, frame, builder, config["n_perm"], rng, batch=250, skip_terms=skipped)
    result = result[~result.term.isin(interactions)].copy()
    # F >= 0 by construction; a negative or non-finite value is cancellation error, not evidence.
    valid = np.isfinite(result.F_obs) & (result.F_obs >= 0) & (result.df1 > 0) & (result.df2 > 0)
    result.loc[~valid, "p_perm"] = np.nan
    result["reason"] = np.where(valid, "", "Permutation check unavailable: the observed F statistic is invalid.")
    result["working_scale"] = scale
    result["kind"] = "permutation"
    result["seed"] = config["seed"]
    result["seed_stream"] = 2202
    return result.to_dict(orient="records"), None
