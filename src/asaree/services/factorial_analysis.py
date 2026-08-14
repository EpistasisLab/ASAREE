"""The spinal_surgery use case's specific statistical methodology, ported from
`spinal_analysis.ipynb` — NOT the generic nonparametric-regression capability
ASAREE's vision describes (that's tracked separately, ASAREE#1). This one only
works for a full-factorial design where every condition factor has exactly two
levels: effect coding (-1/+1), Freedman-Lane residual permutation with
max-statistic FWER correction, and BCa-bootstrap non-inferiority with Holm
correction all assume it structurally, not incidentally.

The core math (``_design_matrix``, ``_freedman_lane_maxstat``,
``_bca_lower_bound``, ``_ni_pvalue``, ``_holm``) is close to a direct port of
the notebook's own functions — that methodology is already correct and
already what the study collaborator confirmed; the risk here is transcription
error, not the design of new statistics.

Two things the notebook read from a manifest are request parameters here
instead, deliberately not inferred: which level of each condition factor is
"+1" (the notebook's own comments explain why guessing — e.g. a substring
match on a model name — silently inverts an estimate everywhere and reads as
entirely plausible), and which combination of levels is the reference
condition R.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as st

from asaree.services.design_generation import cell_label_for


class FactorialAnalysisError(ValueError):
    """Bad input — wrong factor cardinality, an unmatched reference condition,
    too few replicates — caught before any statistic is computed."""


def _design_matrix(
    coded: dict[str, np.ndarray], effects: list[str], labels: dict[str, str]
) -> tuple[np.ndarray, list[str]]:
    """Intercept + one effect-coded column per non-empty subset of *effects*
    (saturated model): with k binary factors that's ``2**k - 1`` terms. Driven
    off ``len(effects)`` rather than a literal, so a design with more or fewer
    condition factors is never silently truncated.

    *coded* maps each effect name to its already-coded (-1/+1) array — kept
    separate from any DataFrame column of the same name, since the original
    (string-valued) factor column and its coded version would otherwise
    collide under one column label.
    """
    n = len(next(iter(coded.values())))
    names: list[str] = []
    cols = [np.ones(n)]
    for k in range(1, len(effects) + 1):
        for combo in itertools.combinations(effects, k):
            v = np.ones(n)
            for c in combo:
                v = v * coded[c]
            cols.append(v)
            names.append(":".join(labels[c] for c in combo))
    return np.column_stack(cols), names


def _freedman_lane_maxstat(
    y: np.ndarray, x: np.ndarray, term_names: list[str], b_resamples: int, rng: np.random.Generator
) -> pd.DataFrame:
    """Freedman-Lane residual permutation with max-statistic FWER correction.

    Column 0 of *x* is the intercept; columns 1.. are the single-df terms in
    *term_names* order. Statistic = |t| of the term in the full model. Each
    resample draws ONE shared permutation index, used to recompute every
    term's statistic — that shared stream is what makes the max-statistic
    correction valid across all terms simultaneously, not per-term resampling.
    """
    n, p = x.shape
    xtx_inv = np.linalg.inv(x.T @ x)
    proj = xtx_inv @ x.T
    d = np.diag(xtx_inv)

    def tstats(yv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        beta = proj @ yv
        resid = yv - x @ beta
        sigma2 = float(resid @ resid) / (n - p)
        return beta, beta / np.sqrt(d * sigma2)

    beta_obs, t_obs = tstats(y)
    obs = np.abs(t_obs[1:])

    reduced = []
    for i in range(len(term_names)):
        keep = [j for j in range(x.shape[1]) if j != (1 + i)]
        xr = x[:, keep]
        br, *_ = np.linalg.lstsq(xr, y, rcond=None)
        fit = xr @ br
        reduced.append((fit, y - fit))

    null_term = np.empty((b_resamples, len(term_names)))
    null_max = np.empty(b_resamples)
    for b in range(b_resamples):
        perm = rng.permutation(n)
        sb = np.empty(len(term_names))
        for i, (fit, res) in enumerate(reduced):
            _, t_star = tstats(fit + res[perm])
            sb[i] = abs(t_star[1 + i])
        null_term[b] = sb
        null_max[b] = sb.max()

    rows = []
    for i, name in enumerate(term_names):
        o = obs[i]
        p_unc = (np.sum(null_term[:, i] >= o) + 1) / (b_resamples + 1)
        p_fwe = (np.sum(null_max >= o) + 1) / (b_resamples + 1)
        rows.append(
            {
                "effect": name,
                "estimate_half_diff": 2 * beta_obs[1 + i],
                "t": t_obs[1 + i],
                "p_perm": p_unc,
                "p_maxstat_fwer": p_fwe,
                "mc_se_p": np.sqrt(p_unc * (1 - p_unc) / b_resamples),
            }
        )
    return pd.DataFrame(rows)


def _bca_lower_bound(
    xc: np.ndarray, xr: np.ndarray, alpha: float, b_resamples: int, rng: np.random.Generator
) -> tuple[float, float, np.ndarray]:
    """One-sided ``(1-alpha)`` BCa lower bound for ``mean(xc) - mean(xr)``.

    Returns ``(theta, lower_bound, boot)``. Acceleration is via jackknife over
    both samples pooled, matching the notebook exactly.
    """
    nc, nr = len(xc), len(xr)
    theta = xc.mean() - xr.mean()
    boot_c = rng.choice(xc, (b_resamples, nc), replace=True).mean(1)
    boot_r = rng.choice(xr, (b_resamples, nr), replace=True).mean(1)
    boot = boot_c - boot_r
    prop = np.clip(np.mean(boot < theta), 1 / (b_resamples + 1), 1 - 1 / (b_resamples + 1))
    z0 = st.norm.ppf(prop)
    jack = np.concatenate(
        [
            (xc.sum() - xc) / (nc - 1) - xr.mean(),
            xc.mean() - (xr.sum() - xr) / (nr - 1),
        ]
    )
    jbar = jack.mean()
    den = 6.0 * (np.sum((jbar - jack) ** 2) ** 1.5)
    a = float(np.sum((jbar - jack) ** 3) / den) if den != 0 else 0.0
    zl = st.norm.ppf(alpha)
    a1 = st.norm.cdf(z0 + (z0 + zl) / (1 - a * (z0 + zl)))
    return theta, float(np.quantile(boot, a1)), boot


def _ni_pvalue(boot: np.ndarray, theta: float, delta: float) -> float:
    """One-sided percentile-bootstrap p for H0: diff = -delta vs H1: diff > -delta."""
    null = boot - theta - delta
    return float((np.sum(null >= theta) + 1) / (len(boot) + 1))


def _holm(pvals: Sequence[float] | np.ndarray) -> np.ndarray:
    p = np.asarray(pvals, float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    run = 0.0
    for rank, idx in enumerate(order):
        run = max(run, (m - rank) * p[idx])
        adj[idx] = min(run, 1.0)
    return adj


def _cells_to_frame(cells: Sequence[Any]) -> pd.DataFrame:
    """One row per cell: flattened factor_values + metric_values, plus
    artifacts kept as a nested dict (accessed by key on demand, not flattened
    — artifacts is a use-case-owned bag, its keys aren't known in advance)."""
    rows = []
    for cell in cells:
        row: dict[str, Any] = dict(cell.factor_values or {})
        for k, v in (cell.metric_values or {}).items():
            row[f"metric__{k}"] = v
        row["_artifacts"] = cell.artifacts or {}
        row["_cell_label"] = cell.cell_label
        rows.append(row)
    return pd.DataFrame(rows)


def analyze_factorial(
    cells: Sequence[Any],
    *,
    condition_factors: list[str],
    positive_levels: dict[str, Any],
    reference_condition: dict[str, Any],
    primary_metric: str,
    alpha: float = 0.05,
    delta: float = 0.05,
    n_resamples: int = 10_000,
    seed: int = 42,
    failure_flag_key: str = "failure_flag",
    cost_keys: Sequence[str] = ("total_tokens", "usd", "wallclock_s"),
) -> dict[str, Any]:
    """The full analysis: failure homogeneity, factorial effects (raw + logit),
    estimated marginal means, non-inferiority vs. *reference_condition*, and
    heteroscedasticity diagnostics. Stateless — a deterministic function of
    *cells* and the given seed, not something that needs its own table.
    """
    if len(condition_factors) < 1:
        raise FactorialAnalysisError("condition_factors must be a non-empty list")
    if set(positive_levels) != set(condition_factors):
        raise FactorialAnalysisError("positive_levels must declare exactly one level per condition factor")
    if set(reference_condition) != set(condition_factors):
        raise FactorialAnalysisError("reference_condition must specify exactly the condition factors")

    rng = np.random.default_rng(seed)
    df_all = _cells_to_frame(cells)
    if df_all.empty:
        raise FactorialAnalysisError("no cells to analyze — generate the design and run it first")

    metric_col = f"metric__{primary_metric}"
    labels = {f: f for f in condition_factors}
    effects = condition_factors

    # A cell is attempted-and-known if it reports either a metric or an
    # explicit failure flag. Anything else hasn't been run yet and is
    # excluded entirely, rather than miscounted as a failure.
    def _failure_flag(artifacts: dict[str, Any]) -> bool:
        return bool(artifacts.get(failure_flag_key, False))

    has_metric = df_all[metric_col].notna() if metric_col in df_all.columns else pd.Series(False, index=df_all.index)
    has_failure_flag = df_all["_artifacts"].apply(lambda a: failure_flag_key in a)
    attempted = df_all[has_metric | has_failure_flag].copy()
    n_not_yet_run = len(df_all) - len(attempted)
    if attempted.empty:
        raise FactorialAnalysisError("no attempted cells (no metric values and no failure flags found)")

    attempted["_failed"] = attempted["_artifacts"].apply(_failure_flag)
    for factor in condition_factors:
        seen = set(attempted[factor].dropna().unique().tolist())
        if len(seen) > 2:
            raise FactorialAnalysisError(f"factor {factor!r} has {len(seen)} distinct levels; expected exactly 2")
        if positive_levels[factor] not in seen and seen:
            raise FactorialAnalysisError(
                f"positive_levels[{factor!r}]={positive_levels[factor]!r} was never observed "
                f"(observed: {sorted(seen, key=str)})"
            )
        attempted[f"_coded__{factor}"] = np.where(attempted[factor] == positive_levels[factor], 1, -1)

    attempted["_condition_label"] = attempted[condition_factors].apply(
        lambda row: cell_label_for({f: row[f] for f in condition_factors}), axis=1
    )

    # --- failures ------------------------------------------------------- #
    g = attempted.groupby("_condition_label")
    fail_tbl = pd.DataFrame({"n_attempted": g.size(), "n_failed": g["_failed"].sum()}).reset_index()
    fail_tbl["failure_rate"] = (fail_tbl["n_failed"] / fail_tbl["n_attempted"]).round(4)

    failure_homogeneity: dict[str, Any] | None = None
    if attempted["_failed"].any():
        tab = pd.crosstab(attempted["_condition_label"], attempted["_failed"])
        if tab.shape[1] == 2:
            chi2, p_chi, _, _ = st.chi2_contingency(tab)
            failure_homogeneity = {
                "chi2": float(chi2),
                "p_value": float(p_chi),
                "condition_dependent": bool(p_chi < alpha),
            }

    scored = attempted[(~attempted["_failed"]) & attempted[metric_col].notna()].copy()
    if scored.empty:
        raise FactorialAnalysisError("no scored rows after excluding failures — nothing to analyze")

    coded = {f: scored[f"_coded__{f}"].to_numpy(float) for f in effects}
    x, term_names = _design_matrix(coded, effects, labels)
    y = scored[metric_col].to_numpy(float)
    factorial_effects = _freedman_lane_maxstat(y, x, term_names, n_resamples, rng)

    eps = 1e-6
    y_logit = np.log(np.clip(y, eps, 1 - eps) / (1 - np.clip(y, eps, 1 - eps)))
    factorial_effects_logit = _freedman_lane_maxstat(y_logit, x, term_names, n_resamples, rng)

    # --- estimated marginal means ---------------------------------------- #
    cell_stats = scored.groupby("_condition_label")[metric_col].agg(["mean", "std", "count"]).reset_index()
    cell_stats["se"] = cell_stats["std"] / np.sqrt(cell_stats["count"])
    tcrit = st.t.ppf(1 - alpha / 2, np.clip(cell_stats["count"] - 1, 1, None))
    cell_stats["ci_lo"] = cell_stats["mean"] - tcrit * cell_stats["se"]
    cell_stats["ci_hi"] = cell_stats["mean"] + tcrit * cell_stats["se"]

    # --- non-inferiority vs. the reference condition --------------------- #
    ref_label = cell_label_for(reference_condition)
    ref = scored.loc[scored["_condition_label"] == ref_label, metric_col].to_numpy(float)
    if ref.size < 2:
        raise FactorialAnalysisError(f"reference condition {reference_condition!r} has fewer than 2 scored replicates")

    sd_by_cond = scored.groupby("_condition_label")[metric_col].std()
    sd_max = float(sd_by_cond.max())
    sd_ratio = (delta / sd_max) if sd_max > 0 else float("inf")
    ni_reportable = sd_ratio >= 2.0

    def _mean_artifact(sub: pd.DataFrame, key: str) -> float:
        return float(sub["_artifacts"].apply(lambda a, key=key: a.get(key)).astype(float).mean())

    def _cost_of(label: str) -> dict[str, float]:
        sub = attempted[attempted["_condition_label"] == label]
        return {k: _mean_artifact(sub, k) for k in cost_keys}

    ref_cost = _cost_of(ref_label)
    ni_rows = []
    for label in sorted(c for c in scored["_condition_label"].unique() if c != ref_label):
        xc = scored.loc[scored["_condition_label"] == label, metric_col].to_numpy(float)
        if xc.size < 2:
            continue
        theta, lb, boot = _bca_lower_bound(xc, ref, alpha, n_resamples, rng)
        cost = _cost_of(label)
        row: dict[str, Any] = {
            "condition": label,
            "contrast_vs_reference": theta,
            "lower_bound": lb,
            "neg_delta": -delta,
            "p_one_sided": _ni_pvalue(boot, theta, delta),
        }
        for k in cost_keys:
            if ref_cost.get(k) and cost.get(k) is not None:
                row[f"{k}_saving_pct"] = 100 * (1 - cost[k] / ref_cost[k])
        ni_rows.append(row)
    ni = pd.DataFrame(ni_rows)
    if not ni.empty:
        ni["p_holm"] = _holm(ni["p_one_sided"].to_numpy())
        ni["ni_decision"] = np.where(
            (ni["lower_bound"] > -delta) & (ni["p_holm"] < alpha), "non-inferior", "not concluded"
        )
        if not ni_reportable:
            ni["ni_decision"] = "not reportable (delta < 2x replicate SD)"

    # --- diagnostics ------------------------------------------------------ #
    resid_sd = scored.groupby("_condition_label")[metric_col].std().rename("resid_sd").reset_index()
    groups = [s[metric_col].to_numpy() for _, s in scored.groupby("_condition_label") if len(s) > 1]
    levene: dict[str, Any] = {}
    if len(groups) > 1:
        lev_w, lev_p = st.levene(*groups)
        levene = {"w": float(lev_w), "p_value": float(lev_p), "heteroscedastic": bool(lev_p < alpha)}

    # --- cost/time (descriptive) ------------------------------------------- #
    cost_time_rows = []
    for label in sorted(attempted["_condition_label"].unique()):
        row = {"condition": label, **_cost_of(label)}
        cost_time_rows.append(row)

    # --- metric summary (descriptive, every metric collected) -------------- #
    metric_cols = [c for c in scored.columns if c.startswith("metric__")]
    metric_summary_rows = []
    for label, sub in scored.groupby("_condition_label"):
        row = {"condition": label, "n": len(sub)}
        for mc in metric_cols:
            row[f"{mc.removeprefix('metric__')}_mean"] = float(sub[mc].mean())
            row[f"{mc.removeprefix('metric__')}_std"] = float(sub[mc].std())
        metric_summary_rows.append(row)

    return {
        "n_attempted": int(len(attempted)),
        "n_scored": int(len(scored)),
        "n_failed": int(attempted["_failed"].sum()),
        "n_not_yet_run": int(n_not_yet_run),
        "failure_summary": fail_tbl.to_dict(orient="records"),
        "failure_homogeneity": failure_homogeneity,
        "factorial_effects": factorial_effects.to_dict(orient="records"),
        "factorial_effects_logit": factorial_effects_logit.to_dict(orient="records"),
        "emm_cells": cell_stats.to_dict(orient="records"),
        "non_inferiority": ni.to_dict(orient="records") if not ni.empty else [],
        "ni_reportable": bool(ni_reportable),
        "replicate_sd_max": sd_max,
        "delta_over_sd": float(sd_ratio),
        "residual_sd_by_condition": resid_sd.to_dict(orient="records"),
        "levene": levene,
        "cost_time_summary": cost_time_rows,
        "metric_summary": metric_summary_rows,
        "footer": {
            "primary_metric": primary_metric,
            "condition_factors": condition_factors,
            "positive_levels": positive_levels,
            "reference_condition": reference_condition,
            "coding": "-1/+1 effect coding",
            "multiplicity": "Holm on one-sided NI p-values; max-statistic FWER for factorial effects",
            "permutation": "Freedman-Lane residual permutation, shared stream",
            "seed": seed,
            "n_resamples": n_resamples,
            "alpha": alpha,
            "delta": delta,
        },
    }


def analyze_experiment_design(design_spec: dict[str, Any] | None, cells: Sequence[Any]) -> dict[str, Any]:
    """The Results tab's own entry point -- a thin wrapper around
    ``analyze_factorial`` that needs no caller-supplied parameters at all.
    ``condition_factors``/``positive_levels``/``reference_condition``/
    ``primary_metric`` are all derived from the experiment's own Design tab
    declarations (``design_spec.factors``/``metrics``) instead of a second,
    separate configuration UI.

    ``analyze_factorial``'s own effect-coding methodology requires every
    condition factor to have exactly 2 levels (see this module's own
    docstring) -- the first declared level is treated as the reference/
    baseline, the second as "positive," a standard baseline-vs-treatment
    convention that needs no additional UI to declare.

    Always returns a plain dict, never raises -- ``available: False`` (with
    a human-readable ``reason``) covers every "nothing to show yet" case,
    whether that's a missing declaration or ``analyze_factorial`` itself
    raising ``FactorialAnalysisError`` (e.g. not enough scored replicates).
    """
    design_spec = design_spec or {}
    factors = design_spec.get("factors") or []
    metrics = design_spec.get("metrics") or []
    primary = next((m for m in metrics if m.get("primary")), None)

    if not factors:
        return {"available": False, "reason": "No factors declared yet -- add some on the Design tab.", "analysis": None, "best_condition": None}
    if primary is None:
        return {
            "available": False,
            "reason": "No primary metric declared yet -- set one on the Design tab.",
            "analysis": None,
            "best_condition": None,
        }
    non_binary = [f["name"] for f in factors if len(f.get("levels") or []) != 2]
    if non_binary:
        return {
            "available": False,
            "reason": f"This analysis requires every factor to have exactly 2 levels ({', '.join(non_binary)} doesn't).",
            "analysis": None,
            "best_condition": None,
        }

    condition_factors = [f["name"] for f in factors]
    reference_condition = {f["name"]: f["levels"][0] for f in factors}
    positive_levels = {f["name"]: f["levels"][1] for f in factors}

    try:
        analysis = analyze_factorial(
            cells,
            condition_factors=condition_factors,
            positive_levels=positive_levels,
            reference_condition=reference_condition,
            primary_metric=primary["name"],
        )
    except FactorialAnalysisError as exc:
        return {"available": False, "reason": str(exc), "analysis": None, "best_condition": None}
    except (ZeroDivisionError, ValueError, ArithmeticError) as exc:
        # analyze_factorial's saturated OLS model (intercept + one term per
        # non-empty factor subset) needs at least as many scored data points
        # as model terms -- too few hits a raw numeric error (e.g. division
        # by zero fitting with n <= p) before its own "fewer than 2 scored
        # replicates" check is ever reached. Same "not enough data yet" UI
        # state as a FactorialAnalysisError, just a different code path --
        # this function's whole contract is to never crash the Results tab,
        # regardless of which numeric edge case degenerate data hits.
        return {"available": False, "reason": f"Not enough scored data yet to analyze: {exc}", "analysis": None, "best_condition": None}

    emm_cells = analysis.get("emm_cells") or []
    best_condition = None
    if emm_cells:
        pick = max if primary.get("direction", "maximize") == "maximize" else min
        best_condition = pick(emm_cells, key=lambda c: c["mean"])

    return {"available": True, "reason": None, "analysis": analysis, "best_condition": best_condition}
