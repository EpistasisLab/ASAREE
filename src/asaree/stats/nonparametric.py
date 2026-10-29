"""Design-preserving permutation sensitivity tests.

Freedman-Lane residual permutation: Freedman & Lane (1983), J Bus Econ Stat 1:292;
Anderson & Legendre (1999), J Stat Comput Simul 62:271; Winkler et al. (2014),
NeuroImage 92:381.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .design import DesignBuilder, stratum_index, term_name


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


def _drop(X, cols):
    keep = np.setdiff1d(np.arange(X.shape[1]), cols)
    return X[:, keep]


def f_statistic(full: OLS, red: OLS, Y):
    df1 = full.rank - red.rank
    df2 = full.n - full.rank
    rf, rr = full.rss(Y), red.rss(Y)
    return ((rr - rf) / df1) / (rf / df2), df1, df2


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


def permutation_tests(w, df, builder: DesignBuilder, n_perm: int, rng, batch: int = 1000) -> pd.DataFrame:
    """Design-preserving permutation tests for main effects (OLS Type III F on working scale).

    Each factor's labels are permuted within strata formed by all other factors
    and blocks, which keeps every cell count fixed. That is exact under the sharp
    null that the factor has no effect at any setting of the others. Interactions
    are not tested here: exchanging labels cannot hold nuisance main effects fixed.
    """
    s = builder.spec
    full = OLS(builder.build(df))
    rows = []
    for t in s.terms:
        if len(t) != 1:
            continue
        name = term_name(t)
        red = OLS(_drop(full.X, builder.term_columns[name]))
        F_obs, d1, d2 = f_statistic(full, red, w)
        others = [f for f in s.factors if f != t[0]] + s.blocks
        strata = stratum_index(df, others)
        count, done = 0, 0
        while done < n_perm:
            k = min(batch, n_perm - done)
            Fb, _, _ = f_statistic(full, red, _within_strata_perms(strata, k, rng, w))
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
                method=f"labels permuted within strata of {' x '.join(others) if others else '(none)'}",
                log10_distinct_permutations=log10_n_permutations(strata, df[t[0]].to_numpy()),
            )
        )
    return pd.DataFrame(rows)
