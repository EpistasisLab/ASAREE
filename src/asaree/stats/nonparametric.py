"""Rank-based (ART / ART-C) and permutation sensitivity analyses.

ART: Wobbrock, Findlater, Gergle & Higgins (2011), CHI '11, doi:10.1145/1978942.1978963
ART-C: Elkin, Kay, Higgins & Wobbrock (2021), UIST '21, doi:10.1145/3472749.3474784
Freedman-Lane residual permutation: Freedman & Lane (1983), J Bus Econ Stat 1:292;
Anderson & Legendre (1999), J Stat Comput Simul 62:271; Winkler et al. (2014),
NeuroImage 92:381.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
from scipy import stats

from .contrasts import Contrast, ContrastFamily
from .design import DesignBuilder, DesignSpec, cell_index, full_factorial_terms, stratum_index, term_name


# =================================================================== OLS
class OLS:
    def __init__(self, X: np.ndarray):
        self.X = X
        u, s, vt = np.linalg.svd(X, full_matrices=False)
        tol = s.max() * max(X.shape) * np.finfo(float).eps if s.size else 0
        self.rank = int(np.sum(s > tol))
        self.Q = u[:, : self.rank]
        sinv = np.where(s > tol, 1.0 / np.where(s > tol, s, 1.0), 0.0)
        self.pinv = (vt.T * sinv) @ u.T
        self.xtx_pinv = (vt.T * sinv**2) @ vt
        self.n = X.shape[0]

    def rss(self, Y):
        """Residual sum of squares for one response or a batch (n, B)."""
        proj = self.Q.T @ Y
        return np.sum(Y * Y, axis=0) - np.sum(proj * proj, axis=0)

    def fit(self, y):
        b = self.pinv @ y
        resid = y - self.X @ b
        df = self.n - self.rank
        s2 = float(resid @ resid) / df if df > 0 else np.nan
        return b, s2, df


def _drop(X, cols):
    keep = np.setdiff1d(np.arange(X.shape[1]), cols)
    return X[:, keep]


def f_statistic(full: OLS, red: OLS, Y):
    df1 = full.rank - red.rank
    df2 = full.n - full.rank
    rf, rr = full.rss(Y), red.rss(Y)
    return ((rr - rf) / df1) / (rf / df2), df1, df2


# ============================================================ alignment
def _cell_means(v, df, factors, levels, blocks):
    """Cell means of the experimental factorial.

    Residuals are taken from the experimental-factor cells only (as ARTool does when
    blocks are not fixed factors in the full factorial). Block effects stay in the
    aligned responses and are absorbed by the block main effect in the ANOVA on
    ranks; this keeps the residual degrees of freedom consistent with that ANOVA.
    The effect estimates use cell means averaged with equal weight over blocks.
    """
    cidx = cell_index(df, factors, levels)
    n_cells = int(np.prod([len(levels[f]) for f in factors])) if factors else 1
    frame = pd.DataFrame(dict(c=cidx, v=v))
    full_mean = frame.groupby("c").v.transform("mean").to_numpy()
    if blocks:
        frame["b"] = stratum_index(df, blocks)
        mu = frame.groupby(["c", "b"]).v.mean().groupby(level="c").mean()
    else:
        mu = frame.groupby("c").v.mean()
    return cidx, full_mean, mu.reindex(range(n_cells)).to_numpy()


def _subset_means(mu, cells: pd.DataFrame, subset: tuple) -> np.ndarray:
    if not subset:
        return np.full(len(cells), np.nanmean(mu))
    g = pd.Series(mu).groupby([cells[f].to_numpy() for f in subset]).transform(lambda s: np.nanmean(s))
    return g.to_numpy()


def art_align(v, df, factors, levels, blocks, target: tuple, combined: bool = False) -> np.ndarray:
    """Aligned responses: cell residuals + estimated target effect.

    combined=False: classic ART alignment for the term ``target`` (inclusion-exclusion
    over marginal means). combined=True: ART-C alignment for the main effect of the
    single factor formed by combining the ``target`` factors.
    """
    cidx, full_mean, mu = _cell_means(v, df, factors, levels, blocks)
    cells = DesignBuilder(DesignSpec(factors, levels, [])).cell_table()
    resid = v - full_mean
    grand = np.nanmean(mu)
    if combined:
        eff = _subset_means(mu, cells, target) - grand
    else:
        eff = np.zeros(len(cells))
        for k in range(len(target) + 1):
            for u in itertools.combinations(target, k):
                eff += (-1) ** (len(target) - k) * _subset_means(mu, cells, u)
    return resid + eff[cidx]


def _art_builder(factors, levels, blocks, block_levels):
    return DesignBuilder(
        DesignSpec(factors, levels, full_factorial_terms(factors), blocks=blocks, block_levels=block_levels)
    )


def art_terms(v, df, factors, levels, blocks, block_levels) -> pd.DataFrame:
    """ART omnibus tests for every term of the complete factorial."""
    b = _art_builder(factors, levels, blocks, block_levels)
    X = b.build(df)
    full = OLS(X)
    rows = []
    for t in b.spec.terms:
        name = term_name(t)
        al = art_align(v, df, factors, levels, blocks, t)
        r = stats.rankdata(al)
        red = OLS(_drop(X, b.term_columns[name]))
        F, d1, d2 = f_statistic(full, red, r)
        # alignment check: non-target effects should be ~0 in the aligned (unranked) data
        others = []
        for t2 in b.spec.terms:
            if t2 == t:
                continue
            f2, _, _ = f_statistic(full, OLS(_drop(X, b.term_columns[term_name(t2)])), al)
            others.append(f2)
        rows.append(
            dict(
                term=name,
                F=float(F),
                df1=d1,
                df2=d2,
                p=float(stats.f.sf(F, d1, d2)),
                alignment_check_max_F_nontarget=float(np.max(others)) if others else 0.0,
            )
        )
    return pd.DataFrame(rows)


def _contrast_combo(c: Contrast, w: np.ndarray) -> np.ndarray:
    return w[0] - w[1] if c.kind == "pairwise" else (w[0] - w[1]) - (w[2] - w[3])


def _ols_contrasts(r, X, cell_rows, contrasts, weights_fn):
    ols = OLS(X)
    b, s2, dfr = ols.fit(r)
    out = []
    for c in contrasts:
        l = _contrast_combo(c, weights_fn(c)) @ cell_rows
        est = float(l @ b)
        se = float(np.sqrt(s2 * l @ ols.xtx_pinv @ l))
        t = est / se if se > 0 else np.nan
        out.append(
            dict(
                family=c.family,
                label=c.label,
                estimate_rank_scale=est,
                se=se,
                t=t,
                df=dfr,
                p=float(2 * stats.t.sf(abs(t), dfr)) if np.isfinite(t) else np.nan,
            )
        )
    return out


def art_c(v, df, factors, levels, blocks, block_levels, fam: ContrastFamily, contrasts: list[Contrast]):
    """ART-C contrasts for one prespecified family (rank scale)."""
    rows = []
    if fam.kind == "pairwise" or not fam.by:
        if fam.kind == "pairwise":
            al = art_align(v, df, factors, levels, blocks, fam.target + fam.by, combined=True)
            method = f"ART-C: aligned for combined factor {'*'.join(fam.target + fam.by)}"
        else:
            al = art_align(v, df, factors, levels, blocks, fam.target, combined=False)
            method = f"ART interaction contrasts: aligned for {':'.join(fam.target)}"
        r = stats.rankdata(al)
        b = _art_builder(factors, levels, blocks, block_levels)
        res = _ols_contrasts(r, b.build(df), b.cell_rows(), contrasts, lambda c: c.weights)
        for x in res:
            x["method"] = method
        rows += res
    else:
        # interaction contrasts separately within each level combination of the by factors
        sub_factors = [f for f in factors if f not in fam.by]
        cells = DesignBuilder(DesignSpec(factors, levels, [])).cell_table()
        groups: dict = {}
        for c in contrasts:
            groups.setdefault(tuple(sorted(c.by_levels.items())), []).append(c)
        for key, cs in groups.items():
            byd = dict(key)
            mask = np.ones(len(df), bool)
            cmask = np.ones(len(cells), bool)
            for f, lv in byd.items():
                mask &= (df[f] == lv).to_numpy()
                cmask &= (cells[f] == lv).to_numpy()
            sub = df.loc[mask].reset_index(drop=True)
            vs = v[mask]
            al = art_align(vs, sub, sub_factors, levels, blocks, fam.target, combined=False)
            r = stats.rankdata(al)
            bsub = _art_builder(sub_factors, levels, blocks, block_levels)
            res = _ols_contrasts(r, bsub.build(sub), bsub.cell_rows(), cs, lambda c, cmask=cmask: c.weights[:, cmask])
            for x in res:
                x["method"] = (
                    f"ART interaction contrasts within {', '.join(f'{k}={v_}' for k, v_ in byd.items())}: "
                    f"aligned for {':'.join(fam.target)}, ranked within subset"
                )
            rows += res
    return pd.DataFrame(rows)


# =========================================================== permutation
def _within_strata_perms(strata: np.ndarray, n_perm: int, rng, base_values: np.ndarray):
    """Columns of base_values permuted within strata (labels permuted within strata)."""
    n = len(strata)
    order0 = np.argsort(strata, kind="stable")
    keys = strata[:, None] + rng.random((n, n_perm))
    perm = np.argsort(keys, axis=0, kind="stable")
    out = np.empty((n, n_perm))
    out[order0, :] = base_values[perm]
    return out


def log10_n_permutations(strata: np.ndarray, groups: np.ndarray) -> float:
    """log10 of the number of distinct label arrangements within strata."""
    from scipy.special import gammaln

    tot = 0.0
    for s in np.unique(strata):
        g = groups[strata == s]
        tot += gammaln(len(g) + 1) - sum(gammaln(np.sum(g == k) + 1) for k in np.unique(g))
    return float(tot / np.log(10))


def permutation_tests(
    w, df, builder: DesignBuilder, n_perm: int, rng, batch: int = 1000, skip_terms=None
) -> pd.DataFrame:
    """Design-preserving permutation tests for every model term (OLS Type III F on working scale).

    Main effects (no continuous covariates): the factor's labels are permuted within
    strata formed by all other categorical factors and blocks, which keeps every cell
    count fixed. Interactions, and any term when continuous covariates are present:
    Freedman-Lane permutation of reduced-model residuals within blocks.
    """
    s = builder.spec
    X = builder.build(df)
    full = OLS(X)
    blocks_idx = stratum_index(df, s.blocks)
    rows = []
    for t in s.terms + [(c,) for c in s.covariates]:
        name = term_name(t)
        if skip_terms and name in skip_terms:
            rows.append(
                dict(
                    term=name,
                    statistic="Type III F (OLS, working scale)",
                    F_obs=np.nan,
                    df1=np.nan,
                    df2=np.nan,
                    n_perm=0,
                    p_perm=np.nan,
                    method="Permutation withheld",
                    reason=skip_terms[name],
                )
            )
            continue
        cols = builder.term_columns[name]
        red = OLS(_drop(X, cols))
        F_obs, d1, d2 = f_statistic(full, red, w)
        if len(t) == 1 and t[0] in s.factors and not s.covariates:
            others = [f for f in s.factors if f != t[0]] + s.blocks
            strata = stratum_index(df, others)
            method = f"labels permuted within strata of {' x '.join(others) if others else '(none)'}"

            def gen(k, strata=strata):
                return _within_strata_perms(strata, k, rng, w)

            npos = log10_n_permutations(strata, df[t[0]].to_numpy())
        else:
            fitted = red.X @ (red.pinv @ w)
            e = w - fitted
            method = "Freedman-Lane residual permutation" + (f" within {' x '.join(s.blocks)}" if s.blocks else "")

            def gen(k, e=e, fitted=fitted):
                return fitted[:, None] + _within_strata_perms(blocks_idx, k, rng, e)

            npos = log10_n_permutations(blocks_idx, np.arange(len(w)))
        count, done = 0, 0
        while done < n_perm:
            k = min(batch, n_perm - done)
            Fb, _, _ = f_statistic(full, red, gen(k))
            count += int(np.sum(Fb >= F_obs * (1 - 1e-10)))
            done += k
        rows.append(
            dict(
                term=name,
                statistic="Type III F (OLS, working scale)",
                F_obs=float(F_obs),
                df1=d1,
                df2=d2,
                n_perm=n_perm,
                p_perm=(1 + count) / (n_perm + 1),
                method=method,
                log10_distinct_permutations=npos,
            )
        )
    return pd.DataFrame(rows)
