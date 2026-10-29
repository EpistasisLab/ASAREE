"""Cell predictions, parametric bootstrap, HC3 Wald tests, bootstrap LR tests, Holm."""

from __future__ import annotations

import numpy as np
from scipy import stats

from .contrasts import QuantitySet
from .design import DesignBuilder
from .engine import FitResult, Model, block_designs

try:  # joblib is optional; everything runs serially without it
    from joblib import Parallel, delayed
except ImportError:  # pragma: no cover
    Parallel = None


def run_parallel(func, arg_list, n_jobs: int):
    if n_jobs == 1 or Parallel is None or len(arg_list) == 1:
        return [func(*a) for a in arg_list]
    return Parallel(n_jobs=n_jobs)(delayed(func)(*a) for a in arg_list)


def _seedseq(seed) -> np.random.SeedSequence:
    return seed if isinstance(seed, np.random.SeedSequence) else np.random.SeedSequence(seed)


def _chunks(total: int, n_chunks: int) -> list[int]:
    n_chunks = max(1, min(n_chunks, total))
    base, extra = divmod(total, n_chunks)
    return [base + (1 if i < extra else 0) for i in range(n_chunks)]


def effective_jobs(n_jobs: int) -> int:
    import os

    if n_jobs is None or n_jobs == 0:
        return 1
    if n_jobs < 0:
        return max(1, (os.cpu_count() or 1) + 1 + n_jobs)
    return n_jobs


# ============================================================ predictions
class Predictor:
    """Response-scale cell means averaged (equal weights) over block levels.

    Continuous covariates are held at their sample means. The link-scale cell
    value is the block-averaged linear predictor of the mean block.
    """

    def __init__(self, model: Model, builder: DesignBuilder):
        _, xg, nb = builder.grid()
        self.designs = block_designs(model.family, xg)
        self.nb = nb
        self.n_cells = xg.shape[0] // nb
        self.slices = model.slices
        self.family = model.family

    def cells(self, theta):
        etas = [d @ theta[s] for d, s in zip(self.designs, self.slices, strict=True)]
        m = self.family.mean(etas, {}).reshape(self.n_cells, self.nb).mean(axis=1)
        l = etas[0].reshape(self.n_cells, self.nb).mean(axis=1)
        return m, l


# ============================================================ bootstrap CI
def _boot_chunk(model: Model, theta_hat, predictor: Predictor, qset: QuantitySet, seed, n, info0=None, correct=True):
    rng = np.random.default_rng(seed)
    etas = model.etas(model.simulation_theta(theta_hat, correct))
    nq = len(qset.meta)
    out = np.full((n, nq), np.nan)
    fails = 0
    for i in range(n):
        y = model.family.simulate(etas, model.aux, rng)
        r = model.with_response(y).fit(theta0=theta_hat, compute_cov=False, max_iter=100, info0=info0)
        if not r.converged:
            fails += 1
            continue
        cm, cl = predictor.cells(r.theta)
        out[i] = qset.evaluate(cm, cl)
    return out, fails


def parametric_bootstrap(
    model: Model,
    fit: FitResult,
    predictor: Predictor,
    qset: QuantitySet,
    n_boot: int,
    seed: int,
    n_jobs: int = 1,
    dispersion_correction: bool = True,
):
    """Simulate from the fitted model, refit on the fixed design, recompute quantities."""
    jobs = effective_jobs(n_jobs)
    sizes = _chunks(n_boot, jobs * 4)
    seeds = _seedseq(seed).spawn(len(sizes))
    res = run_parallel(
        _boot_chunk,
        [
            (model, fit.theta, predictor, qset, s, k, fit.information, dispersion_correction)
            for s, k in zip(seeds, sizes, strict=True)
        ],
        jobs,
    )
    draws = np.vstack([r[0] for r in res])
    fails = int(sum(r[1] for r in res))
    return draws, fails


def percentile_ci(draws: np.ndarray, level: float = 0.95):
    a = (1 - level) / 2
    lo = np.nanpercentile(draws, 100 * a, axis=0)
    hi = np.nanpercentile(draws, 100 * (1 - a), axis=0)
    return lo, hi


# ================================================================== Wald
def full_design_blocks(model: Model):
    """(block index, base-column slice) for blocks that carry the factorial design."""
    out = []
    for i, s in enumerate(model.specs):
        if s.kind == "mean":
            out.append((i, slice(1, None) if not model.family.mean_intercept else slice(None)))
        elif s.kind == "regression" and model.family.aux_design == "full":
            out.append((i, slice(None)))
    return out


def constraint_matrix(model: Model, l_base: np.ndarray) -> np.ndarray:
    """Expand a base-design constraint (q x p) to the stacked parameter vector."""
    l_base = np.atleast_2d(l_base)
    rows = []
    for b, cols in full_design_blocks(model):
        lb = l_base[:, cols]
        if not np.any(lb):
            continue
        r = np.zeros((lb.shape[0], model.n_params))
        r[:, model.slices[b]] = lb
        rows.append(r)
    return np.vstack(rows) if rows else np.zeros((0, model.n_params))


def wald_test(theta, cov, L):
    L = L[np.any(L != 0, axis=1)]
    if L.shape[0] == 0:
        return np.nan, 0, np.nan
    d = L @ theta
    V = L @ cov @ L.T
    rank = np.linalg.matrix_rank(V, tol=1e-10 * max(np.abs(V).max(), 1e-300))
    W = float(d @ np.linalg.pinv(V, rcond=1e-10) @ d)
    return W, int(rank), float(stats.chi2.sf(W, rank)) if rank > 0 else np.nan


def response_contrast_wald(model: Model, fit: FitResult, predictor: Predictor, qset: QuantitySet):
    """Delta-method HC3 Wald tests of the response-scale contrasts (log scale for ratios).

    Tests exactly the quantity that is estimated and bootstrapped, which matters when the
    link-scale null and the response-scale null differ (two-part models, nonlinear links
    averaged over cells).
    """
    sl = qset.contrast_slice()
    ratio = qset.effect_scale == "ratio"

    def g(theta):
        v = qset.evaluate(*predictor.cells(theta))[sl]
        return np.log(np.clip(v, 1e-300, None)) if ratio else v

    est = g(fit.theta)
    P = len(fit.theta)
    J = np.empty((len(est), P))
    for k in range(P):
        h = 1e-5 * (1.0 + abs(fit.theta[k]))
        e = np.zeros(P)
        e[k] = h
        J[:, k] = (g(fit.theta + e) - g(fit.theta - e)) / (2 * h)
    se = np.sqrt(np.clip(np.einsum("ij,jk,ik->i", J, fit.cov_hc3, J), 0, None))
    z = est / np.where(se > 0, se, np.nan)
    return est, se, 2 * stats.norm.sf(np.abs(z))


# =================================================== bootstrap LR tests


# =================================================================== Holm
def holm(p) -> np.ndarray:
    """Holm (1979) step-down adjusted p-values; NaNs are ignored and kept."""
    p = np.asarray(p, float)
    out = np.full_like(p, np.nan)
    ok = np.where(~np.isnan(p))[0]
    m = len(ok)
    if m == 0:
        return out
    order = ok[np.argsort(p[ok], kind="stable")]
    adj = np.maximum.accumulate((m - np.arange(m)) * p[order])
    out[order] = np.minimum(adj, 1.0)
    return out
