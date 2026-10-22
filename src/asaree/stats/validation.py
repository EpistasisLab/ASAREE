"""Input validation and the factor registry."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
from scipy import stats

from .config import AnalysisConfig


def _add(rows, severity, check, detail):
    rows.append(dict(severity=severity, check=check, detail=detail))


def resolve_levels(df: pd.DataFrame, cfg: AnalysisConfig) -> tuple[dict, dict]:
    levels = {}
    for f in cfg.factors:
        if f.kind != "categorical":
            continue
        observed = list(pd.unique(df[f.name].dropna()))
        levels[f.name] = list(f.levels) if f.levels is not None else sorted(observed, key=str)
    block_levels = {b: sorted(pd.unique(df[b].dropna()), key=str) for b in cfg.blocks}
    return levels, block_levels


def validate(df: pd.DataFrame, cfg: AnalysisConfig) -> tuple[pd.DataFrame, bool]:
    rows: list[dict] = []
    needed = [f.name for f in cfg.factors] + [o.name for o in cfg.outcomes] + cfg.blocks
    needed += [o.denominator for o in cfg.outcomes if o.denominator]
    if cfg.replicate_col:
        needed.append(cfg.replicate_col)
    missing = [c for c in dict.fromkeys(needed) if c not in df.columns]
    if missing:
        _add(rows, "error", "required columns", f"missing columns: {missing}")
        return pd.DataFrame(rows), False
    _add(rows, "info", "rows", f"{len(df)} runs")
    fatal = False
    # factors
    for f in cfg.factors:
        col = df[f.name]
        if col.isna().any():
            _add(rows, "error", f"factor {f.name}", f"{int(col.isna().sum())} missing values")
            fatal = True
        if f.kind == "categorical":
            obs = set(pd.unique(col.dropna()))
            if f.levels is not None:
                extra = obs - set(f.levels)
                unused = set(f.levels) - obs
                if extra:
                    _add(rows, "error", f"factor {f.name}", f"values not in declared levels: {sorted(extra, key=str)}")
                    fatal = True
                if unused:
                    _add(
                        rows, "error", f"factor {f.name}", f"declared levels never observed: {sorted(unused, key=str)}"
                    )
                    fatal = True
            if len(obs) < 2:
                _add(rows, "error", f"factor {f.name}", "fewer than 2 levels; cannot estimate an effect")
                fatal = True
        else:
            if not np.issubdtype(col.dtype, np.number):
                _add(rows, "error", f"factor {f.name}", "continuous factor is not numeric")
                fatal = True
    if fatal:
        return pd.DataFrame(rows), False
    cat = cfg.categorical
    # cells
    if cat:
        counts = df.groupby(cat, observed=True).size()
        n_possible = int(np.prod([df[c].nunique() for c in cat]))
        _add(
            rows,
            "info",
            "factorial cells",
            f"{len(counts)} of {n_possible} cells observed; runs per cell min={counts.min()}, max={counts.max()}",
        )
        if len(counts) < n_possible:
            _add(
                rows,
                "warning",
                "incomplete factorial",
                f"{n_possible - len(counts)} empty cells: their predicted means are model extrapolations "
                "and the highest-order interaction is not fully estimable",
            )
        if counts.max() > counts.min():
            _add(
                rows,
                "warning",
                "unbalanced design",
                "unequal runs per cell; marginal means remain equally weighted over cells",
            )
        if counts.min() < 3:
            _add(rows, "warning", "replication", "some cells have fewer than 3 runs; diagnostics are weak")
    # splits / clustering
    if cfg.split_col:
        k = df[cfg.split_col].nunique()
        if k == 1:
            _add(rows, "info", "train/test split", "single split: inference is conditional on that split")
        else:
            _add(
                rows,
                "info",
                "train/test split",
                f"{k} splits/datasets: entered as a fixed blocking factor; bootstrap and permutations stay within "
                "blocks; inference is conditional on these splits (not generalising to new splits)",
            )
            per = df.groupby(cat + [cfg.split_col], observed=True).size().unstack(fill_value=0)
            if (per == 0).any().any():
                _add(rows, "warning", "split coverage", "some cell x split combinations are empty")
    else:
        _add(
            rows,
            "info",
            "train/test split",
            "no split column: runs treated as one fixed split; inference is conditional on it",
        )
    # replicates
    if cfg.replicate_col:
        keys = cat + cfg.blocks + [cfg.replicate_col]
        keys = list(dict.fromkeys(keys))
        dup = df.duplicated(keys).sum()
        if dup:
            _add(
                rows,
                "error" if not cfg.replicate_as_block else "warning",
                "replicate identifiers",
                f"{dup} runs share cell, block and replicate id",
            )
        ids_per_cell = df.groupby(cat, observed=True)[cfg.replicate_col].apply(lambda s: frozenset(s))
        if cat and len(set(ids_per_cell)) == 1 and not cfg.replicate_as_block:
            _add(
                rows,
                "warning",
                "replicate pairing",
                "every cell uses the same replicate ids. If replicate r shares a random seed or test items "
                "across configurations, runs are paired and replicate_as_block=True is more efficient and "
                "more honest; if ids are just counters, ignore this",
            )
    # outcomes
    names = [o.name for o in cfg.outcomes]
    for o in cfg.outcomes:
        col = df[o.name]
        if o.measurement_type != "ordinal" and not np.issubdtype(col.dtype, np.number):
            _add(rows, "error", f"outcome {o.name}", "non-numeric values")
            continue
        if col.isna().any():
            _add(
                rows,
                "warning",
                f"outcome {o.name}",
                f"{int(col.isna().sum())} missing values dropped for this outcome (missingness assumed "
                "unrelated to the outcome; check failed runs separately)",
            )
        if o.measurement_type != "ordinal" and col.nunique(dropna=True) <= 1:
            _add(rows, "error", f"outcome {o.name}", "constant outcome")
        if o.measurement_type != "ordinal" and np.isinf(col.to_numpy(dtype=float)).any():
            _add(rows, "error", f"outcome {o.name}", "infinite values")
        if o.denominator:
            d = df[o.denominator]
            if (d <= 0).any() or (d != np.round(d)).any():
                _add(rows, "error", f"outcome {o.name}", "denominator must be positive integers")
            if (col > d).any():
                _add(rows, "error", f"outcome {o.name}", "numerator exceeds denominator")
        if not o.prespecified:
            _add(
                rows,
                "info",
                f"outcome {o.name}",
                "custom (user-defined) metric: model chosen from declared "
                "construction; review the outcome-distribution report",
            )
    # deterministic relations between outcomes
    num = [
        n
        for n, o in zip(names, cfg.outcomes, strict=True)
        if o.measurement_type != "ordinal" and np.issubdtype(df[n].dtype, np.number)
    ]
    for a, b in itertools.combinations(num, 2):
        sub = df[[a, b]].dropna()
        if len(sub) >= 10 and sub[a].nunique() > 2 and sub[b].nunique() > 2:
            rho = stats.spearmanr(sub[a], sub[b]).statistic
            if abs(rho) > 0.9999:
                declared = any(o.derived_from in (a, b) for o in cfg.outcomes if o.name in (a, b))
                _add(
                    rows,
                    "warning" if not declared else "info",
                    "deterministic outcomes",
                    f"{a} and {b} are monotone transformations of each other (Spearman {rho:.4f})"
                    + ("" if declared else "; declare derived_from and avoid double-counting evidence"),
                )
    ok = not any(r["severity"] == "error" for r in rows)
    return pd.DataFrame(rows), ok


def factor_registry(df: pd.DataFrame, cfg: AnalysisConfig, levels: dict, block_levels: dict) -> pd.DataFrame:
    rows = []
    for f in cfg.factors:
        if f.kind == "categorical":
            counts = df[f.name].value_counts()
            rows.append(
                dict(
                    factor=f.name,
                    role="experimental factor",
                    kind="categorical",
                    n_levels=len(levels[f.name]),
                    levels=" | ".join(map(str, levels[f.name])),
                    runs_per_level=" | ".join(str(int(counts.get(l, 0))) for l in levels[f.name]),
                    coding="sum-to-zero (effects) coding",
                    description=f.description,
                )
            )
        else:
            rows.append(
                dict(
                    factor=f.name,
                    role="experimental factor",
                    kind="continuous",
                    n_levels=np.nan,
                    levels=f"range {df[f.name].min():g} to {df[f.name].max():g}",
                    runs_per_level="",
                    coding="centered linear term",
                    description=f.description,
                )
            )
    for b in cfg.blocks:
        rows.append(
            dict(
                factor=b,
                role="blocking factor (fixed)",
                kind="categorical",
                n_levels=len(block_levels[b]),
                levels=" | ".join(map(str, block_levels[b])),
                runs_per_level="",
                coding="sum-to-zero; averaged over in predictions",
                description="split / dataset / paired replicate",
            )
        )
    if cfg.replicate_col and not cfg.replicate_as_block:
        rows.append(
            dict(
                factor=cfg.replicate_col,
                role="replicate identifier",
                kind="id",
                n_levels=df[cfg.replicate_col].nunique(),
                levels="",
                runs_per_level="",
                coding="not modelled (independent replicates)",
                description="",
            )
        )
    return pd.DataFrame(rows)
