"""Outcome families with a common, likelihood-based interface.

Every family is written as a set of *blocks*. Each block owns one linear
predictor eta_b = X_b @ theta_b:

* ``mean``        location predictor, uses the full factorial design
* ``regression``  second predictor (zero / hurdle / boundary part); full design
                  or intercept-only, controlled by ``aux_design``
* ``scalar``      ancillary parameter (dispersion, shape, cut-point, power);
                  its design is a column of ones

A family only has to supply a vectorised per-observation log-likelihood, a
response-scale mean and a simulator. Derivatives are taken numerically with
respect to the per-observation linear predictors (see engine.py), which keeps
every family on the same Newton optimiser, sandwich estimator and bootstrap.

Only models without a published-package equivalent are kept: hurdle Gamma,
zero/one-inflated beta and beta-binomial.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import betaln, expit, gammaln, log_expit, logit


@dataclass(frozen=True)
class BlockSpec:
    name: str
    kind: str  # 'mean' | 'regression' | 'scalar'


def _lstsq(x: np.ndarray, z: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(x, z, rcond=None)[0]


class Family:
    name: str = "family"
    link: str = "identity"  # link of the mean block
    effect_scale: str = "difference"  # 'difference' or 'ratio' for contrasts of means
    discrete: bool = False
    mean_intercept: bool = True  # False for cumulative-link models
    default_aux_design: str = "intercept"
    description: str = ""
    # scalar blocks that carry dispersion, and how variance scales with them:
    # 'log_sd' (var ~ exp(2t)), 'log_precision' (var ~ exp(-t)), 'log_dispersion' (var ~ exp(t)),
    # 'log_beta_precision' (var ~ 1/(1 + exp(t))). Used for the small-sample dispersion correction.
    dispersion: dict = {}

    def __init__(self, aux_design: str | None = None):
        self.aux_design = aux_design or self.default_aux_design

    # --------------------------------------------------------------- structure
    def blocks(self) -> list[BlockSpec]:
        raise NotImplementedError

    def configure(self, y: np.ndarray, aux: dict) -> None:
        """Hook for data-dependent structure (e.g. which ZOIB boundaries exist)."""

    def check_support(self, y: np.ndarray, aux: dict) -> str | None:
        return None

    # -------------------------------------------------------------- likelihood
    def init_params(self, y, designs, aux) -> list[np.ndarray]:
        raise NotImplementedError

    def loglik(self, y, etas, aux) -> np.ndarray:
        raise NotImplementedError

    def mean(self, etas, aux) -> np.ndarray:
        raise NotImplementedError

    def simulate(self, etas, aux, rng: np.random.Generator) -> np.ndarray:
        raise NotImplementedError

    # helpers
    @property
    def logit_link_effects(self) -> bool:
        """Report link-scale (log-odds) interaction contrasts as well."""
        return self.link == "logit" and len([b for b in self.blocks() if b.kind != "scalar"]) == 1

    def __repr__(self):
        return f"{self.__class__.__name__}(aux_design={self.aux_design!r})"


# ===================================================== two-part continuous


class GammaHurdle(Family):
    """Two-part model: logit P(y>0) and Gamma-log for the positive part."""

    name = "gamma_hurdle"
    dispersion = {"log_shape": "log_precision"}
    link = "log"
    effect_scale = "ratio"
    default_aux_design = "full"
    description = "Two-part: logit P(y>0) + Gamma-log positive part"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("nonzero", "regression"), BlockSpec("log_shape", "scalar")]

    def check_support(self, y, aux):
        if np.any(y < 0):
            return "requires y >= 0"
        if not np.any(y > 0):
            return "no positive values"
        return None

    def init_params(self, y, designs, aux):
        pos = y > 0
        xm = designs[0]
        b = (
            _lstsq(xm[pos], np.log(y[pos]))
            if pos.sum() > xm.shape[1]
            else np.r_[np.log(y[pos].mean()), np.zeros(xm.shape[1] - 1)]
        )
        r = y[pos] / np.exp(xm[pos] @ b)
        k = 1.0 / max((r / r.mean()).var(), 1e-6)
        z = np.zeros(designs[1].shape[1])
        z[0] = logit(np.clip(pos.mean(), 0.02, 0.98))
        return [b, z, np.array([np.log(np.clip(k, 1e-3, 1e6))])]

    def loglik(self, y, etas, aux):
        eta, z, lk = etas
        k = np.exp(lk)
        pos = y > 0
        ll = log_expit(-z)
        yp = np.where(pos, y, 1.0)
        g = k * lk - gammaln(k) + (k - 1.0) * np.log(yp) - k * eta - k * yp * np.exp(-eta)
        return np.where(pos, log_expit(z) + g, ll)

    def mean(self, etas, aux):
        return expit(etas[1]) * np.exp(etas[0])

    def simulate(self, etas, aux, rng):
        eta, z, lk = etas
        k = np.exp(lk)
        nz = rng.random(eta.shape) < expit(z)
        return np.where(nz, rng.gamma(k, np.exp(eta) / k), 0.0)


# =============================================================== (0, 1) data


def beta_loglik(y, eta, lphi):
    phi = np.exp(lphi)
    mu = expit(eta)
    a, b = mu * phi, (1.0 - mu) * phi
    return -betaln(a, b) + (a - 1.0) * np.log(y) + (b - 1.0) * np.log1p(-y)


class ZeroOneInflatedBeta(Family):
    """ZOIB (Ospina & Ferrari 2010): P(boundary), P(1 | boundary), beta for (0, 1)."""

    name = "zoib"
    dispersion = {"log_phi": "log_beta_precision"}
    link = "logit"
    description = "Zero/one-inflated beta; boundary parts intercept-only by default"

    def __init__(self, aux_design=None):
        super().__init__(aux_design)
        self.has0, self.has1 = True, True

    def configure(self, y, aux):
        self.has0 = bool(np.any(y == 0))
        self.has1 = bool(np.any(y == 1))

    def blocks(self):
        b = [BlockSpec("mean", "mean"), BlockSpec("log_phi", "scalar"), BlockSpec("boundary", "regression")]
        if self.has0 and self.has1:
            b.append(BlockSpec("one_given_boundary", "scalar"))
        return b

    def check_support(self, y, aux):
        if not np.all((y >= 0) & (y <= 1)):
            return "requires 0 <= y <= 1"
        if not (np.any(y == 0) or np.any(y == 1)):
            return "no exact 0 or 1 values; use beta"
        if np.all((y == 0) | (y == 1)):
            return "no interior values"
        return None

    def _gamma_logit(self, etas):
        if self.has0 and self.has1:
            return etas[3]
        return np.full_like(etas[0], 50.0 if self.has1 else -50.0)

    def init_params(self, y, designs, aux):
        inner = (y > 0) & (y < 1)
        xm = designs[0]
        b = _lstsq(xm[inner], logit(y[inner])) if inner.sum() > xm.shape[1] else np.zeros(xm.shape[1])
        mu = expit(xm[inner] @ b)
        v = np.mean((y[inner] - mu) ** 2)
        phi = np.mean(mu * (1 - mu)) / max(v, 1e-8) - 1.0
        bd = np.zeros(designs[2].shape[1])
        bd[0] = logit(np.clip(1 - inner.mean(), 0.01, 0.99))
        out = [b, np.array([np.log(np.clip(phi, 0.5, 1e6))]), bd]
        if self.has0 and self.has1:
            out.append(np.array([logit(np.clip(np.mean(y[~inner] == 1), 0.01, 0.99))]))
        return out

    def loglik(self, y, etas, aux):
        eta, lphi, a = etas[0], etas[1], etas[2]
        g = self._gamma_logit(etas)
        yi = np.clip(y, 1e-12, 1 - 1e-12)
        inner = beta_loglik(yi, eta, lphi) + log_expit(-a)
        ll0 = log_expit(a) + log_expit(-g)
        ll1 = log_expit(a) + log_expit(g)
        return np.where(y == 0, ll0, np.where(y == 1, ll1, inner))

    def mean(self, etas, aux):
        a = expit(etas[2])
        g = expit(self._gamma_logit(etas))
        return a * g + (1.0 - a) * expit(etas[0])

    def simulate(self, etas, aux, rng):
        eta, lphi, a = etas[0], etas[1], etas[2]
        g = expit(self._gamma_logit(etas))
        phi = np.exp(lphi)
        mu = expit(eta)
        bnd = rng.random(eta.shape) < expit(a)
        one = rng.random(eta.shape) < g
        inner = np.clip(rng.beta(mu * phi, (1 - mu) * phi), 1e-12, 1 - 1e-12)
        return np.where(bnd, one.astype(float), inner)


def _log_rising(x, m, max_terms: int = 5000):
    """log Gamma(x + m) - log Gamma(x) for integer m >= 0.

    Computed as sum_{k<m} log(x + k) when m is small enough; this avoids the
    catastrophic cancellation of the gammaln difference when x is large (e.g. a
    negative-binomial size or beta-binomial precision drifting toward infinity),
    which otherwise makes finite-difference Hessians noisy.
    """
    x = np.asarray(x, float)
    m = np.asarray(m, float)
    out = gammaln(x + m) - gammaln(x)
    mmax = int(np.max(m)) if m.size else 0
    if mmax == 0:
        return np.where(m == 0, 0.0, out)
    use = (x > 1e3) & (m <= max_terms)
    if np.any(use) and mmax <= max_terms:
        xs, ms = x[use], m[use]
        k = np.arange(mmax)[None, :]
        terms = np.where(k < ms[:, None], np.log(xs[:, None] + k), 0.0)
        out = out.copy()
        out[use] = terms.sum(axis=1)
    return out


# ============================================================== binomial data
class BetaBinomialLogit(Family):
    """y = successes, aux['trials'] = denominator; beta-distributed success probability."""

    name = "betabinomial"
    link = "logit"
    discrete = True
    dispersion = {"log_phi": "log_beta_precision"}
    description = "Beta-binomial, logit link, constant precision"

    def check_support(self, y, aux):
        n = aux["trials"]
        ok = np.all((y >= 0) & (y <= n) & (y == np.round(y)) & (n >= 1))
        return None if ok else "requires integer 0 <= successes <= trials"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_phi", "scalar")]

    def init_params(self, y, designs, aux):
        n = aux["trials"]
        b = _lstsq(designs[0], logit((y + 0.5) / (n + 1.0)))
        p = expit(designs[0] @ b)
        ratio = np.mean((y - n * p) ** 2 / np.maximum(n * p * (1 - p), 1e-8))
        nbar = max(n.mean(), 2.0)
        rho = np.clip((ratio - 1.0) / (nbar - 1.0), 1e-3, 0.9)
        return [b, np.array([np.log(1.0 / rho - 1.0)])]

    def loglik(self, y, etas, aux):
        n = aux["trials"]
        mu, phi = expit(etas[0]), np.exp(etas[1])
        a, b = mu * phi, (1 - mu) * phi
        # betaln(y+a, n-y+b) - betaln(a, b) written with rising factorials (stable for large phi)
        return (
            gammaln(n + 1)
            - gammaln(y + 1)
            - gammaln(n - y + 1)
            + _log_rising(a, y)
            + _log_rising(b, n - y)
            - _log_rising(phi, n)
        )

    def mean(self, etas, aux):
        return expit(etas[0])

    def simulate(self, etas, aux, rng):
        mu, phi = expit(etas[0]), np.exp(etas[1])
        p = rng.beta(mu * phi, (1 - mu) * phi)
        return rng.binomial(aux["trials"].astype(int), p).astype(float)


FAMILIES = {cls.name: cls for cls in [GammaHurdle, ZeroOneInflatedBeta, BetaBinomialLogit]}


def make_family(name: str, **kwargs) -> Family:
    if name not in FAMILIES:
        raise KeyError(f"unknown family {name!r}; options: {sorted(FAMILIES)}")
    return FAMILIES[name](**kwargs)
