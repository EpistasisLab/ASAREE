"""Maximum-likelihood engine shared by every family.

Derivatives are computed with respect to each observation's linear predictors
by central finite differences of the vectorised per-observation
log-likelihood. Because every parameter enters through a linear predictor
(eta_b = X_b theta_b), the exact gradient and Hessian follow by the chain
rule: g_b = X_b' d1_b and H_bc = X_b' diag(d2_bc) X_c. The cost of one
derivative evaluation is 1 + 2B + 2B(B-1) vectorised log-likelihood calls
for B blocks, independent of the number of regression coefficients.

The optimiser is a damped Newton / Levenberg-Marquardt scheme with step
halving. Covariances: model-based (inverse observed information) and HC3
sandwich (MacKinnon & White 1985; Long & Ervin 2000) generalised to
likelihood models through the per-observation scores and a generalised
leverage computed from the regression blocks.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import linalg

from .families import Family

_H_REL = 1e-4


@dataclass
class FitResult:
    theta: np.ndarray
    loglik: float
    converged: bool
    n_iter: int
    max_abs_grad: float
    n_params: int
    n_obs: int
    message: str = ""
    information: np.ndarray | None = None
    cov_model: np.ndarray | None = None
    cov_hc3: np.ndarray | None = None
    leverage: np.ndarray | None = None
    cooks: np.ndarray | None = None
    hessian_pd: bool | None = None
    hessian_cond: float | None = None

    @property
    def aic(self) -> float:
        return -2.0 * self.loglik + 2.0 * self.n_params

    @property
    def bic(self) -> float:
        return -2.0 * self.loglik + np.log(self.n_obs) * self.n_params


def block_designs(family: Family, x_base: np.ndarray) -> list[np.ndarray]:
    """Per-block design matrices from the base (sum-coded) design."""
    n = x_base.shape[0]
    designs = []
    for spec in family.blocks():
        if spec.kind == "mean" or (spec.kind == "regression" and family.aux_design == "full"):
            cols = slice(1, None) if (spec.kind == "mean" and not family.mean_intercept) else slice(None)
            designs.append(x_base[:, cols])
        else:
            designs.append(np.ones((n, 1)))
    return designs


class Model:
    """A family plus per-block designs, response and auxiliary data."""

    def __init__(
        self,
        family: Family,
        y: np.ndarray,
        designs: list[np.ndarray],
        aux: dict | None = None,
    ):
        self.family = family
        self.y = np.asarray(y, dtype=float)
        self.designs = designs
        self.aux = aux or {}
        self.x_base = None
        self.specs = family.blocks()
        sizes = [d.shape[1] for d in designs]
        edges = np.cumsum([0] + sizes)
        self.slices = [slice(edges[i], edges[i + 1]) for i in range(len(sizes))]
        self.n_params = int(edges[-1])
        self.n = len(self.y)
        self.regression_blocks = [
            i
            for i, s in enumerate(self.specs)
            if s.kind == "mean" or (s.kind == "regression" and designs[i].shape[1] > 1)
        ]

    @classmethod
    def from_base(cls, family, y, x_base, aux=None):
        m = cls(family, y, block_designs(family, x_base), aux)
        m.x_base = x_base
        return m

    def with_response(self, y: np.ndarray) -> Model:
        m = Model.__new__(Model)
        m.__dict__.update(self.__dict__)
        m.y = np.asarray(y, dtype=float)
        return m

    # ----------------------------------------------------------- parameters

    def etas(self, theta, designs=None):
        designs = self.designs if designs is None else designs
        return [d @ theta[s] for d, s in zip(designs, self.slices, strict=True)]

    def loglik_obs(self, theta):
        return self.family.loglik(self.y, self.etas(theta), self.aux)

    def loglik(self, theta) -> float:
        ll = self.loglik_obs(theta)
        return float(np.sum(ll)) if np.all(np.isfinite(ll)) else -np.inf

    def init_theta(self) -> np.ndarray:
        parts = self.family.init_params(self.y, self.designs, self.aux)
        return np.concatenate([np.asarray(p, float) for p in parts])

    def simulation_theta(self, theta: np.ndarray, correct: bool = True) -> np.ndarray:
        """Parameters used to simulate bootstrap data.

        With ``correct`` the ML dispersion is inflated by c = n / (n - p), p = number of
        mean-structure parameters, the usual degrees-of-freedom correction (exact for the
        Gaussian residual variance; a first-order analogue for other dispersion
        parameters). ML dispersion underestimates variability in small samples, which
        makes parametric-bootstrap intervals too narrow.
        """
        if not correct or not self.family.dispersion:
            return theta
        p = self.designs[0].shape[1]
        if self.n - p < 5:
            return theta
        c = self.n / (self.n - p)
        th = theta.copy()
        for b, spec in enumerate(self.specs):
            kind = self.family.dispersion.get(spec.name)
            if kind is None:
                continue
            sl = self.slices[b]
            if kind == "log_sd":
                th[sl] += 0.5 * np.log(c)
            elif kind == "log_precision":
                th[sl] -= np.log(c)
            elif kind == "log_dispersion":
                th[sl] += np.log(c)
            elif kind == "log_beta_precision":
                th[sl] = np.log(np.maximum((1.0 + np.exp(th[sl])) / c - 1.0, 1e-3))
        return th

    # ---------------------------------------------------------- derivatives
    def obs_derivs(self, theta, second=True):
        """Per-observation log-likelihood, first and second derivatives w.r.t. each eta."""
        f = self.family.loglik
        y, aux = self.y, self.aux
        etas = self.etas(theta)
        nb = len(etas)
        f0 = f(y, etas, aux)
        h = [_H_REL * (1.0 + np.abs(e)) for e in etas]
        d1 = np.empty((self.n, nb))
        d2 = np.empty((self.n, nb, nb)) if second else None

        def shifted(shifts):
            e = list(etas)
            for b, s in shifts:
                e[b] = etas[b] + s * h[b]
            return f(y, e, aux)

        for b in range(nb):
            fp, fm = shifted([(b, 1)]), shifted([(b, -1)])
            d1[:, b] = (fp - fm) / (2 * h[b])
            if second:
                d2[:, b, b] = (fp - 2 * f0 + fm) / h[b] ** 2
        if second:
            for b in range(nb):
                for c in range(b + 1, nb):
                    v = (
                        shifted([(b, 1), (c, 1)])
                        - shifted([(b, 1), (c, -1)])
                        - shifted([(b, -1), (c, 1)])
                        + shifted([(b, -1), (c, -1)])
                    ) / (4 * h[b] * h[c])
                    d2[:, b, c] = v
                    d2[:, c, b] = v
        return f0, d1, d2

    def gradient(self, theta):
        _, d1, _ = self.obs_derivs(theta, second=False)
        return np.concatenate([d.T @ d1[:, b] for b, d in enumerate(self.designs)])

    def _fixed_metric_ascent(self, theta, ll, info0, tol, gtol, max_iter, max_step):
        """Quasi-Newton (BFGS) ascent started from the inverse information at a nearby
        optimum (e.g. the original fit when refitting a bootstrap sample). Each
        iteration needs only the gradient (1 + 2B log-likelihood evaluations instead
        of 1 + 2B + 2B(B-1) for a full Hessian) and convergence is superlinear."""
        try:
            Hinv = linalg.cho_solve(
                linalg.cho_factor(info0, check_finite=False), np.eye(len(theta)), check_finite=False
            )
        except (linalg.LinAlgError, ValueError):
            return theta, ll, False, 0
        g = self.gradient(theta)
        eye = np.eye(len(theta))
        for it in range(1, max_iter + 1):
            step = Hinv @ g
            dec = float(g @ step)
            if dec < tol or np.max(np.abs(g)) < gtol:
                return theta, ll, True, it
            if dec <= 0:
                return theta, ll, False, it
            big = np.max(np.abs(step))
            if big > max_step:
                step *= max_step / big
            t = 1.0
            while t > 1e-6:
                cand = theta + t * step
                llc = self.loglik(cand)
                if np.isfinite(llc) and llc >= ll - 1e-12 * (abs(ll) + 1.0):
                    break
                t *= 0.5
            else:
                return theta, ll, False, it
            g_new = self.gradient(cand)
            sv, yv = cand - theta, g - g_new
            sy = float(sv @ yv)
            if sy > 1e-14:
                rho = 1.0 / sy
                A = eye - rho * np.outer(sv, yv)
                Hinv = A @ Hinv @ A.T + rho * np.outer(sv, sv)
            theta, ll, g = cand, llc, g_new
        return theta, ll, False, max_iter

    def grad_hess(self, theta):
        f0, d1, d2 = self.obs_derivs(theta)
        g = np.concatenate([d.T @ d1[:, b] for b, d in enumerate(self.designs)])
        H = np.empty((self.n_params, self.n_params))
        for b, db in enumerate(self.designs):
            for c, dc in enumerate(self.designs):
                if c < b:
                    continue
                blk = db.T @ (d2[:, b, c][:, None] * dc)
                H[self.slices[b], self.slices[c]] = blk
                H[self.slices[c], self.slices[b]] = blk.T
        return f0, d1, d2, g, H

    # ------------------------------------------------------------------ fit
    def fit(
        self,
        theta0=None,
        max_iter: int = 200,
        tol: float = 1e-10,
        compute_cov: bool = True,
        max_step: float = 5.0,
        gtol: float = 1e-7,
        info0: np.ndarray | None = None,
    ) -> FitResult:
        """Maximise the log-likelihood.

        info0: information matrix at a nearby optimum (e.g. the original fit when refitting
        a bootstrap sample). If given, cheap fixed-metric iterations are tried first and
        full Newton is used only if they do not converge.
        """
        theta = self.init_theta() if theta0 is None else np.array(theta0, float)
        ll = self.loglik(theta)
        if not np.isfinite(ll) and theta0 is not None:
            theta = self.init_theta()
            ll = self.loglik(theta)
        if not np.isfinite(ll):
            return FitResult(theta, -np.inf, False, 0, np.inf, self.n_params, self.n, "non-finite start")
        if info0 is not None:
            theta, ll, ok, k = self._fixed_metric_ascent(theta, ll, info0, tol, gtol, 40, max_step)
            if ok:
                res = FitResult(theta, ll, True, k, np.nan, self.n_params, self.n, "converged (fixed metric)")
                if compute_cov:
                    self.add_covariance(res)
                return res
        lam = 0.0
        converged, it, msg = False, 0, ""
        g = np.zeros_like(theta)
        for it in range(1, max_iter + 1):  # noqa: B007 -- iteration count is returned after the loop
            _, _, _, g, H = self.grad_hess(theta)
            negH = -H
            scale = max(np.mean(np.abs(np.diag(negH))), 1e-8)
            step = None
            for _ in range(30):
                try:
                    c = linalg.cho_factor(negH + lam * scale * np.eye(self.n_params), check_finite=False)
                    step = linalg.cho_solve(c, g, check_finite=False)
                    break
                except (linalg.LinAlgError, ValueError):
                    lam = max(lam * 10.0, 1e-8)
            if step is None or not np.all(np.isfinite(step)):
                msg = "could not form a Newton step"
                break
            decrement = float(g @ step)
            gmax = float(np.max(np.abs(g))) if g.size else 0.0
            if (decrement < tol and lam < 1e-6) or gmax < gtol:
                converged = True
                break
            big = np.max(np.abs(step))
            if big > max_step:
                step = step * (max_step / big)
            t, accepted = 1.0, False
            while t > 1e-9:
                cand = theta + t * step
                llc = self.loglik(cand)
                if np.isfinite(llc) and llc >= ll - 1e-12 * (abs(ll) + 1.0):
                    accepted = True
                    break
                t *= 0.5
            if not accepted:
                if decrement < 1e-6 or gmax < 1e3 * gtol:
                    # no ascent possible within floating-point noise: at the optimum
                    converged, msg = True, "converged (numerical precision floor)"
                    break
                if lam > 1e8:
                    msg = "line search failed"
                    break
                lam = max(lam * 10.0, 1e-6)
                continue
            improvement = llc - ll
            theta, ll = cand, llc
            lam = lam / 10.0 if lam > 1e-12 else 0.0
            if improvement < 1e-13 * (abs(ll) + 1.0) and np.max(np.abs(t * step)) < 1e-9:
                converged = lam < 1e-6
                if converged:
                    break
        res = FitResult(
            theta,
            ll,
            converged,
            it,
            float(np.max(np.abs(g))) if g.size else 0.0,
            self.n_params,
            self.n,
            msg or ("converged" if converged else "max iterations"),
        )
        if compute_cov:
            self.add_covariance(res)
        return res

    # ----------------------------------------------------------- covariance
    def add_covariance(self, res: FitResult) -> None:
        f0, d1, d2, g, H = self.grad_hess(res.theta)
        res.max_abs_grad = float(np.max(np.abs(g)))
        J = -0.5 * (H + H.T)
        ev = np.linalg.eigvalsh(J)
        res.information = J
        res.hessian_pd = bool(ev.min() > 1e-10 * max(ev.max(), 1e-300))
        res.hessian_cond = float(ev.max() / ev.min()) if ev.min() > 0 else np.inf
        Jinv = np.linalg.pinv(J) if not res.hessian_pd else linalg.inv(J)
        res.cov_model = Jinv
        # per-observation scores
        S = np.zeros((self.n, self.n_params))
        for b, d in enumerate(self.designs):
            S[:, self.slices[b]] = d1[:, b][:, None] * d
        # generalised leverage from the regression blocks
        R = self.regression_blocks
        h = np.zeros(self.n)
        if R:
            idx = np.concatenate([np.arange(self.n_params)[self.slices[b]] for b in R])
            JRR = J[np.ix_(idx, idx)]
            K = np.linalg.pinv(JRR)
            offs = np.cumsum([0] + [self.designs[b].shape[1] for b in R])
            for i, b in enumerate(R):
                for j, c in enumerate(R):
                    Kcb = K[offs[j] : offs[j + 1], offs[i] : offs[i + 1]]
                    h += -d2[:, b, c] * np.einsum("ij,jk,ik->i", self.designs[c], Kcb, self.designs[b])
        h = np.clip(h, 0.0, 0.999)
        res.leverage = h
        Sw = S / (1.0 - h)[:, None]
        meat = Sw.T @ Sw
        res.cov_hc3 = Jinv @ meat @ Jinv
        # one-step (infinitesimal jackknife) generalised Cook's distance
        q = np.einsum("ij,jk,ik->i", S, Jinv, S)
        res.cooks = q / (1.0 - h) ** 2 / max(self.n_params, 1)
