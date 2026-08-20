"""Statistical-analysis computation (pure; the ``stats`` tool bucket).

Extracted from the monolith's Friedman / pairwise-posthoc / summarize / ART-ANOVA
tools. These take native Python objects (dicts, record lists) and return result
dicts — the JSON parse/serialize is the MCP boundary and stays in the server
wrapper (#1457). Invalid inputs raise :class:`ComputeError`; numeric behaviour
is byte-for-byte identical to the monolith.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .errors import ComputeError


def friedman_test(scores: dict[str, list[float]]) -> dict[str, Any]:
    """Non-parametric Friedman test across k conditions with repeated measures.

    *scores* maps condition name -> list of scores; all lists must be the same
    length (the replicate count). Requires at least 3 conditions.
    """
    from scipy.stats import friedmanchisquare

    conditions = list(scores.keys())
    groups = [scores[c] for c in conditions]

    if len(groups) < 3:
        raise ComputeError("Friedman test requires at least 3 conditions.")

    stat, p = friedmanchisquare(*groups)

    # Kendall's W effect size
    k = len(groups)
    n = len(groups[0])
    W = (
        stat / (k * (n + 1) - stat / (n - 1))
        if (k * (n + 1) - stat / (n - 1)) != 0
        else float("nan")
    )

    return {
        "test": "Friedman",
        "statistic": round(float(stat), 4),
        "p_value": round(float(p), 6),
        "kendalls_W": round(float(W), 4),
        "n_conditions": k,
        "n_replicates": n,
        "reject_null_0.05": bool(p < 0.05),
        "conditions": conditions,
        "condition_medians": {
            c: round(float(np.median(scores[c])), 4) for c in conditions
        },
    }


def pairwise_posthoc(
    scores: dict[str, list[float]],
    method: str = "dunn",
    correction: str = "benjamini-hochberg",
) -> dict[str, Any]:
    """Pairwise post-hoc comparisons with multiple-testing correction.

    *method* is ``"dunn"`` (Mann-Whitney per pair) or ``"wilcoxon"``; *correction*
    is ``"benjamini-hochberg"`` (BH FDR) or ``"bonferroni"``.
    """
    from itertools import combinations

    from scipy.stats import mannwhitneyu, wilcoxon
    from statsmodels.stats.multitest import multipletests

    conditions = list(scores.keys())
    pairs = list(combinations(conditions, 2))

    raw_p: list[float] = []
    pair_labels: list[str] = []

    for a, b in pairs:
        xa = np.array(scores[a])
        xb = np.array(scores[b])
        pair_labels.append(f"{a} vs {b}")
        if method == "wilcoxon":
            try:
                _, p = wilcoxon(xa, xb)
            except Exception:  # noqa: BLE001
                p = 1.0
        else:
            _, p = mannwhitneyu(xa, xb, alternative="two-sided")
        raw_p.append(float(p))

    mc_method = "fdr_bh" if correction == "benjamini-hochberg" else "bonferroni"
    reject, p_adj, _, _ = multipletests(raw_p, method=mc_method)

    results = []
    for i, (a, b) in enumerate(pairs):
        xa = np.array(scores[a])
        xb = np.array(scores[b])
        results.append(
            {
                "condition_a": a,
                "condition_b": b,
                "median_a": round(float(np.median(xa)), 4),
                "median_b": round(float(np.median(xb)), 4),
                "median_diff": round(float(np.median(xa) - np.median(xb)), 4),
                "p_raw": round(raw_p[i], 6),
                "p_adjusted": round(float(p_adj[i]), 6),
                "significant_0.05": bool(reject[i]),
            }
        )

    results.sort(key=lambda x: x["p_adjusted"])  # type: ignore[arg-type,return-value]

    return {
        "method": method,
        "correction": correction,
        "n_comparisons": len(pairs),
        "n_significant": int(sum(r["significant_0.05"] for r in results)),  # type: ignore[misc]
        "comparisons": results,
    }


def summarize_experiment(
    records: list[dict[str, Any]],
    factors: list[str],
    response_col: str = "balanced_accuracy",
) -> dict[str, Any]:
    """Per-condition descriptive statistics + median ranking, for reporting."""
    df = pd.DataFrame(records)

    if response_col not in df.columns:
        raise ComputeError(
            f"Response column '{response_col}' not found. Available: {list(df.columns)}"
        )

    summary = []
    for keys, group in df.groupby(factors):
        key_dict = dict(zip(factors, keys if isinstance(keys, tuple) else [keys]))
        scores = group[response_col].values
        summary.append(
            {
                **key_dict,
                "n": int(len(scores)),
                "mean": round(float(np.mean(scores)), 4),
                "std": round(float(np.std(scores)), 4),
                "median": round(float(np.median(scores)), 4),
                "iqr": round(
                    float(np.percentile(scores, 75) - np.percentile(scores, 25)), 4
                ),
                "min": round(float(np.min(scores)), 4),
                "max": round(float(np.max(scores)), 4),
            }
        )

    summary.sort(key=lambda x: x["median"], reverse=True)
    for i, row in enumerate(summary):
        row["rank"] = i + 1

    return {
        "response_col": response_col,
        "factors": factors,
        "n_conditions": len(summary),
        "best_condition": summary[0] if summary else None,
        "worst_condition": summary[-1] if summary else None,
        "summary": summary,
    }


def art_anova(
    records: list[dict[str, Any]],
    factors: list[str],
    response_col: str = "balanced_accuracy",
) -> dict[str, Any]:
    """Aligned Rank Transform ANOVA for factorial repeated-measures designs.

    Implements the ART procedure (Wobbrock et al., 2011): aligns each response by
    stripping main-effect and interaction contributions, ranks, then runs a
    standard ANOVA on the aligned-ranked values.
    """
    df = pd.DataFrame(records)

    from itertools import combinations

    from scipy.stats import f_oneway, rankdata

    if response_col not in df.columns:
        raise ComputeError(
            f"Column '{response_col}' not found. Available: {list(df.columns)}"
        )
    for f in factors:
        if f not in df.columns:
            raise ComputeError(f"Factor column '{f}' not found.")

    results = {}

    # ART per factor: align by stripping all other effects, then rank
    for factor in factors:
        # Cell means for alignment
        group_means = df.groupby(factor)[response_col].transform("mean")
        grand_mean = df[response_col].mean()

        # For each other factor, compute its cell mean contribution
        aligned = df[response_col] - group_means + grand_mean

        ranked = rankdata(aligned)
        groups = [ranked[df[factor] == level] for level in df[factor].unique()]
        if len(groups) >= 2:
            stat, p = f_oneway(*groups)
        else:
            stat, p = 0.0, 1.0

        results[factor] = {
            "factor": factor,
            "F_statistic": round(float(stat), 4),
            "p_value": round(float(p), 6),
            "significant_0.05": bool(p < 0.05),
        }

    # Pairwise interactions
    for fa, fb in combinations(factors, 2):
        interaction_key = f"{fa}:{fb}"
        df_copy = df.copy()
        df_copy["_cell"] = df_copy[fa].astype(str) + "×" + df_copy[fb].astype(str)
        cell_means = df_copy.groupby("_cell")[response_col].transform("mean")
        main_a = df_copy.groupby(fa)[response_col].transform("mean")
        main_b = df_copy.groupby(fb)[response_col].transform("mean")
        grand_mean = df_copy[response_col].mean()
        aligned = (
            df_copy[response_col]
            - cell_means
            - main_a
            - main_b
            + 2 * grand_mean
            + cell_means
        )
        ranked = rankdata(aligned)
        groups = [ranked[df_copy["_cell"] == c] for c in df_copy["_cell"].unique()]
        if len(groups) >= 2:
            stat, p = f_oneway(*groups)
        else:
            stat, p = 0.0, 1.0
        results[interaction_key] = {
            "factor": interaction_key,
            "F_statistic": round(float(stat), 4),
            "p_value": round(float(p), 6),
            "significant_0.05": bool(p < 0.05),
        }

    return {
        "test": "ART ANOVA",
        "reference": "Wobbrock et al. (2011) — Aligned Rank Transform for nonparametric factorial ANOVAs",
        "factors_tested": factors,
        "n_observations": len(df),
        "results": list(results.values()),
    }
