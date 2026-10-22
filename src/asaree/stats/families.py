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
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats
from scipy.special import betaln, expit, gammaln, log_expit, logit, logsumexp

LOG2PI = np.log(2.0 * np.pi)


@dataclass(frozen=True)
class BlockSpec:
    name: str
    kind: str  # 'mean' | 'regression' | 'scalar'


def _lstsq(x: np.ndarray, z: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(x, z, rcond=None)[0]


def _log1mexp(a: np.ndarray) -> np.ndarray:
    """log(1 - exp(a)) for a <= 0, numerically stable."""
    a = np.minimum(a, -1e-300)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(a > -np.log(2.0), np.log(-np.expm1(a)), np.log1p(-np.exp(a)))


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


def _zeros_init(designs, start=0, values=None):
    out = []
    for i, d in enumerate(designs[start:]):
        v = np.zeros(d.shape[1])
        if values is not None:
            v[0] = values[i]
        out.append(v)
    return out


# =============================================================== continuous
class Gaussian(Family):
    name = "gaussian"
    dispersion = {"log_sigma": "log_sd"}
    description = "Gaussian, identity link"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_sigma", "scalar")]

    def init_params(self, y, designs, aux):
        b = _lstsq(designs[0], y)
        r = y - designs[0] @ b
        return [b, np.array([np.log(max(r.std(), 1e-8))])]

    def loglik(self, y, etas, aux):
        mu, ls = etas
        return -0.5 * LOG2PI - ls - 0.5 * ((y - mu) * np.exp(-ls)) ** 2

    def mean(self, etas, aux):
        return etas[0]

    def simulate(self, etas, aux, rng):
        mu, ls = etas
        return mu + np.exp(ls) * rng.standard_normal(mu.shape)


class LogNormal(Family):
    name = "lognormal"
    dispersion = {"log_sigma": "log_sd"}
    link = "log"
    effect_scale = "ratio"
    description = "Log-normal (Gaussian on log y), log link for the median"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_sigma", "scalar")]

    def check_support(self, y, aux):
        return None if np.all(y > 0) else "requires y > 0"

    def init_params(self, y, designs, aux):
        z = np.log(y)
        b = _lstsq(designs[0], z)
        return [b, np.array([np.log(max((z - designs[0] @ b).std(), 1e-8))])]

    def loglik(self, y, etas, aux):
        mu, ls = etas
        z = np.log(y)
        return -0.5 * LOG2PI - ls - 0.5 * ((z - mu) * np.exp(-ls)) ** 2 - z

    def mean(self, etas, aux):
        mu, ls = etas
        return np.exp(mu + 0.5 * np.exp(2 * ls))

    def simulate(self, etas, aux, rng):
        mu, ls = etas
        return np.exp(mu + np.exp(ls) * rng.standard_normal(mu.shape))


class GammaLog(Family):
    name = "gamma"
    dispersion = {"log_shape": "log_precision"}
    link = "log"
    effect_scale = "ratio"
    description = "Gamma, log link"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_shape", "scalar")]

    def check_support(self, y, aux):
        return None if np.all(y > 0) else "requires y > 0"

    def init_params(self, y, designs, aux):
        b = _lstsq(designs[0], np.log(y))
        r = y / np.exp(designs[0] @ b)
        r = r / r.mean()
        b[0] += np.log(np.mean(y / np.exp(designs[0] @ b)))
        k = 1.0 / max(r.var(), 1e-6)
        return [b, np.array([np.log(np.clip(k, 1e-3, 1e6))])]

    def loglik(self, y, etas, aux):
        eta, lk = etas
        k = np.exp(lk)
        return k * lk - gammaln(k) + (k - 1.0) * np.log(y) - k * eta - k * y * np.exp(-eta)

    def mean(self, etas, aux):
        return np.exp(etas[0])

    def simulate(self, etas, aux, rng):
        eta, lk = etas
        k = np.exp(lk)
        return rng.gamma(k, np.exp(eta) / k)


class TweedieLog(Family):
    """Compound Poisson-gamma Tweedie (1 < p < 2), log link, power estimated.

    Density evaluated with the series of Dunn & Smyth (2005, Stat Comput 15:267).
    """

    name = "tweedie"
    dispersion = {"log_phi": "log_dispersion"}
    link = "log"
    effect_scale = "ratio"
    description = "Tweedie compound Poisson-gamma (1<p<2), log link, p estimated"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_phi", "scalar"), BlockSpec("power_logit", "scalar")]

    def check_support(self, y, aux):
        return None if np.all(y >= 0) else "requires y >= 0"

    @staticmethod
    def _unpack(etas):
        eta, lphi, t = etas
        p = 1.0 + expit(t)
        p = np.clip(p, 1.0 + 1e-6, 2.0 - 1e-6)
        return np.exp(eta), eta, np.exp(lphi), lphi, p

    def init_params(self, y, designs, aux):
        off = y[y > 0].min() / 2.0 if np.any(y > 0) else 1.0
        b = _lstsq(designs[0], np.log(y + off))
        b[0] += np.log(max(y.mean(), 1e-8) / np.mean(np.exp(designs[0] @ b)))
        mu = np.exp(designs[0] @ b)
        p = 1.5
        phi = np.mean((y - mu) ** 2 / mu**p)
        return [b, np.array([np.log(np.clip(phi, 1e-4, 1e4))]), np.array([0.0])]

    def loglik(self, y, etas, aux):
        mu, eta, phi, lphi, p = self._unpack(etas)
        lam = np.exp((2.0 - p) * eta) / (phi * (2.0 - p))
        ll = -lam.copy()
        pos = y > 0
        if np.any(pos):
            yp, lamp, pp, phip, etap = y[pos], lam[pos], p[pos], phi[pos], eta[pos]
            alpha = (2.0 - pp) / (pp - 1.0)
            lg = np.log(phip * (pp - 1.0)) + (pp - 1.0) * etap  # log gamma scale
            jstar = np.exp((2.0 - pp) * np.log(yp)) / ((2.0 - pp) * phip)
            width = int(2 * np.ceil(6.0 * np.sqrt(np.max(jstar)) + 12.0))
            width = min(width, 6000)
            j0 = np.maximum(1.0, np.floor(jstar - width / 2.0))
            j = j0[:, None] + np.arange(width)[None, :]
            ja = j * alpha[:, None]
            logt = (
                j * np.log(lamp)[:, None]
                - gammaln(j + 1.0)
                + (ja - 1.0) * np.log(yp)[:, None]
                - (yp * np.exp(-lg))[:, None]
                - gammaln(ja)
                - ja * lg[:, None]
            )
            ll[pos] = -lamp + logsumexp(logt, axis=1)
        return ll

    def mean(self, etas, aux):
        return np.exp(etas[0])

    def simulate(self, etas, aux, rng):
        mu, eta, phi, lphi, p = self._unpack(etas)
        lam = mu ** (2.0 - p) / (phi * (2.0 - p))
        alpha = (2.0 - p) / (p - 1.0)
        scale = phi * (p - 1.0) * mu ** (p - 1.0)
        n = rng.poisson(lam)
        out = np.zeros_like(mu)
        pos = n > 0
        out[pos] = rng.gamma(n[pos] * alpha[pos], scale[pos])
        return out


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
class BetaLogit(Family):
    name = "beta"
    dispersion = {"log_phi": "log_beta_precision"}
    link = "logit"
    description = "Beta regression, logit link, constant precision"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_phi", "scalar")]

    def check_support(self, y, aux):
        return None if np.all((y > 0) & (y < 1)) else "requires 0 < y < 1"

    def init_params(self, y, designs, aux):
        b = _lstsq(designs[0], logit(y))
        mu = expit(designs[0] @ b)
        v = np.mean((y - mu) ** 2)
        phi = np.mean(mu * (1 - mu)) / max(v, 1e-8) - 1.0
        return [b, np.array([np.log(np.clip(phi, 0.5, 1e6))])]

    @staticmethod
    def _beta_ll(y, eta, lphi):
        phi = np.exp(lphi)
        mu = expit(eta)
        a, b = mu * phi, (1.0 - mu) * phi
        return -betaln(a, b) + (a - 1.0) * np.log(y) + (b - 1.0) * np.log1p(-y)

    def loglik(self, y, etas, aux):
        return self._beta_ll(y, etas[0], etas[1])

    def mean(self, etas, aux):
        return expit(etas[0])

    def simulate(self, etas, aux, rng):
        phi = np.exp(etas[1])
        mu = expit(etas[0])
        y = rng.beta(mu * phi, (1.0 - mu) * phi)
        return np.clip(y, 1e-12, 1.0 - 1e-12)


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
        inner = BetaLogit._beta_ll(yi, eta, lphi) + log_expit(-a)
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


# ===================================================================== counts
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


def _nb_logpmf(y, eta, la):
    """NB2 log pmf with size r = exp(-la) and mean exp(eta), stable as r -> infinity."""
    la = np.maximum(la, -40.0)  # alpha -> 0 (Poisson limit); flagged as a boundary estimate
    r = np.exp(-la)
    t = eta + la  # log(mu / r)
    log1p_ratio = np.logaddexp(0.0, t)  # log(1 + mu / r)
    return _log_rising(r, y) - gammaln(y + 1.0) - r * log1p_ratio + y * (t - log1p_ratio)


def _nb_logp0(eta, la):
    la = np.maximum(la, -40.0)
    r = np.exp(-la)
    return -r * np.logaddexp(0.0, eta + la)


def _count_init(y, x):
    b = _lstsq(x, np.log(y + 0.5))
    b[0] += np.log(max(y.mean(), 1e-8) / np.mean(np.exp(x @ b)))
    return b


class PoissonLog(Family):
    name = "poisson"
    link = "log"
    effect_scale = "ratio"
    discrete = True
    description = "Poisson, log link"

    def blocks(self):
        return [BlockSpec("mean", "mean")]

    def check_support(self, y, aux):
        return None if np.all((y >= 0) & (y == np.round(y))) else "requires non-negative integers"

    def init_params(self, y, designs, aux):
        return [_count_init(y, designs[0])]

    def loglik(self, y, etas, aux):
        eta = etas[0]
        return y * eta - np.exp(eta) - gammaln(y + 1.0)

    def mean(self, etas, aux):
        return np.exp(etas[0])

    def simulate(self, etas, aux, rng):
        return rng.poisson(np.exp(etas[0])).astype(float)


class NegBinLog(PoissonLog):
    name = "negbin"
    dispersion = {"log_alpha": "log_dispersion"}
    description = "Negative binomial (NB2, var = mu + alpha mu^2), log link"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_alpha", "scalar")]

    def init_params(self, y, designs, aux):
        b = _count_init(y, designs[0])
        mu = np.exp(designs[0] @ b)
        alpha = np.mean(((y - mu) ** 2 - mu) / mu**2)
        return [b, np.array([np.log(np.clip(alpha, 1e-3, 1e3))])]

    def loglik(self, y, etas, aux):
        return _nb_logpmf(y, etas[0], etas[1])

    def simulate(self, etas, aux, rng):
        mu = np.exp(etas[0])
        r = np.exp(-etas[1])
        lam = rng.gamma(r, mu / r)
        return rng.poisson(lam).astype(float)


class ZeroInflatedPoisson(PoissonLog):
    name = "zip"
    description = "Zero-inflated Poisson; inflation part intercept-only by default"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("inflation", "regression")]

    def init_params(self, y, designs, aux):
        z = np.zeros(designs[1].shape[1])
        z[0] = -1.0
        return [_count_init(y, designs[0]), z]

    def _logp(self, y, etas):
        return y * etas[0] - np.exp(etas[0]) - gammaln(y + 1.0), -np.exp(etas[0])

    def loglik(self, y, etas, aux):
        z = etas[-1]
        lp, lp0 = self._logp(y, etas)
        zero = np.logaddexp(log_expit(z), log_expit(-z) + lp0)
        return np.where(y == 0, zero, log_expit(-z) + lp)

    def mean(self, etas, aux):
        z = etas[-1]
        return expit(-z) * np.exp(etas[0])

    def _draw_count(self, etas, rng):
        return rng.poisson(np.exp(etas[0])).astype(float)

    def simulate(self, etas, aux, rng):
        z = etas[-1]
        structural = rng.random(etas[0].shape) < expit(z)
        return np.where(structural, 0.0, self._draw_count(etas, rng))


class ZeroInflatedNegBin(ZeroInflatedPoisson):
    name = "zinb"
    dispersion = {"log_alpha": "log_dispersion"}
    description = "Zero-inflated NB2; inflation part intercept-only by default"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_alpha", "scalar"), BlockSpec("inflation", "regression")]

    def init_params(self, y, designs, aux):
        b = _count_init(y, designs[0])
        z = np.zeros(designs[2].shape[1])
        z[0] = -1.0
        return [b, np.array([np.log(0.5)]), z]

    def _logp(self, y, etas):
        return _nb_logpmf(y, etas[0], etas[1]), _nb_logp0(etas[0], etas[1])

    def _draw_count(self, etas, rng):
        mu, r = np.exp(etas[0]), np.exp(-etas[1])
        return rng.poisson(rng.gamma(r, mu / r)).astype(float)


class HurdlePoisson(PoissonLog):
    name = "hurdle_poisson"
    default_aux_design = "full"
    description = "Hurdle: logit P(y>0) + zero-truncated Poisson"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("nonzero", "regression")]

    def check_support(self, y, aux):
        msg = super().check_support(y, aux)
        if msg:
            return msg
        if not np.any(y == 0) or not np.any(y > 0):
            return "needs both zeros and positive counts"
        return None

    def _nz(self, etas):
        return etas[1]

    def _logp(self, y, etas):
        return y * etas[0] - np.exp(etas[0]) - gammaln(y + 1.0), -np.exp(etas[0])

    def init_params(self, y, designs, aux):
        pos = y > 0
        xm = designs[0]
        b = _count_init(y[pos], xm[pos]) if pos.sum() > xm.shape[1] else _count_init(y, xm)
        z = np.zeros(designs[1].shape[1])
        z[0] = logit(np.clip(pos.mean(), 0.02, 0.98))
        return [b, z]

    def loglik(self, y, etas, aux):
        z = self._nz(etas)
        lp, lp0 = self._logp(y, etas)
        return np.where(y == 0, log_expit(-z), log_expit(z) + lp - _log1mexp(lp0))

    def mean(self, etas, aux):
        z = self._nz(etas)
        _, lp0 = self._logp(np.zeros_like(etas[0]), etas)
        return expit(z) * np.exp(etas[0] - _log1mexp(lp0))

    def _truncated(self, etas, rng):
        mu = np.exp(etas[0])
        p0 = np.exp(-mu)
        u = p0 + rng.random(mu.shape) * (1 - p0)
        return np.maximum(stats.poisson.ppf(np.minimum(u, 1 - 1e-15), mu), 1.0)

    def simulate(self, etas, aux, rng):
        nz = rng.random(etas[0].shape) < expit(self._nz(etas))
        return np.where(nz, self._truncated(etas, rng), 0.0)


class HurdleNegBin(HurdlePoisson):
    name = "hurdle_negbin"
    dispersion = {"log_alpha": "log_dispersion"}
    description = "Hurdle: logit P(y>0) + zero-truncated NB2"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("nonzero", "regression"), BlockSpec("log_alpha", "scalar")]

    def _nz(self, etas):
        return etas[1]

    def _logp(self, y, etas):
        return _nb_logpmf(y, etas[0], etas[2]), _nb_logp0(etas[0], etas[2])

    def init_params(self, y, designs, aux):
        b, z = super().init_params(y, designs, aux)
        return [b, z, np.array([np.log(0.5)])]

    def _truncated(self, etas, rng):
        mu, r = np.exp(etas[0]), np.exp(-etas[2])
        p = r / (r + mu)
        p0 = p**r
        u = p0 + rng.random(mu.shape) * (1 - p0)
        return np.maximum(stats.nbinom.ppf(np.minimum(u, 1 - 1e-15), r, p), 1.0)


# ============================================================== binomial data
class BinomialLogit(Family):
    """y = successes, aux['trials'] = denominator (1 for binary outcomes)."""

    name = "binomial"
    link = "logit"
    discrete = True
    description = "Binomial, logit link (binary or successes/trials)"

    def blocks(self):
        return [BlockSpec("mean", "mean")]

    def check_support(self, y, aux):
        n = aux["trials"]
        ok = np.all((y >= 0) & (y <= n) & (y == np.round(y)) & (n >= 1))
        return None if ok else "requires integer 0 <= successes <= trials"

    def init_params(self, y, designs, aux):
        n = aux["trials"]
        return [_lstsq(designs[0], logit((y + 0.5) / (n + 1.0)))]

    def loglik(self, y, etas, aux):
        n = aux["trials"]
        eta = etas[0]
        return gammaln(n + 1) - gammaln(y + 1) - gammaln(n - y + 1) + y * log_expit(eta) + (n - y) * log_expit(-eta)

    def mean(self, etas, aux):
        return expit(etas[0])

    def simulate(self, etas, aux, rng):
        return rng.binomial(aux["trials"].astype(int), expit(etas[0])).astype(float)


class BetaBinomialLogit(BinomialLogit):
    name = "betabinomial"
    dispersion = {"log_phi": "log_beta_precision"}
    description = "Beta-binomial, logit link, constant precision"

    def blocks(self):
        return [BlockSpec("mean", "mean"), BlockSpec("log_phi", "scalar")]

    def init_params(self, y, designs, aux):
        n = aux["trials"]
        b = super().init_params(y, designs, aux)[0]
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

    def simulate(self, etas, aux, rng):
        mu, phi = expit(etas[0]), np.exp(etas[1])
        p = rng.beta(mu * phi, (1 - mu) * phi)
        return rng.binomial(aux["trials"].astype(int), p).astype(float)


# ==================================================================== ordinal
class CumulativeLogit(Family):
    """Proportional-odds model: P(Y <= k) = expit(c_k - eta), y coded 0..K-1.

    Contrasts are reported on the expected score sum_k s_k P(Y = k).
    """

    name = "ordinal_logit"
    link = "cumlogit"
    discrete = True
    mean_intercept = False
    description = "Cumulative-logit (proportional odds) ordinal model"

    def __init__(self, n_levels: int = 3, scores=None, aux_design=None):
        super().__init__(aux_design)
        self.n_levels = int(n_levels)
        self.scores = np.asarray(scores if scores is not None else np.arange(1, n_levels + 1), float)

    def blocks(self):
        b = [BlockSpec("mean", "mean"), BlockSpec("cut_1", "scalar")]
        b += [BlockSpec(f"log_cut_gap_{k}", "scalar") for k in range(2, self.n_levels)]
        return b

    def check_support(self, y, aux):
        ok = np.all((y >= 0) & (y <= self.n_levels - 1) & (y == np.round(y)))
        return None if ok else f"requires integer codes 0..{self.n_levels - 1}"

    def _cuts(self, etas):
        c = [etas[1]]
        for g in etas[2:]:
            c.append(c[-1] + np.exp(g))
        return np.column_stack(c)

    def init_params(self, y, designs, aux):
        cum = np.array([np.mean(y <= k) for k in range(self.n_levels - 1)])
        cum = np.clip(cum, 0.01, 0.99)
        c = np.maximum.accumulate(logit(cum))
        gaps = np.maximum(np.diff(c), 1e-2)
        return [np.zeros(designs[0].shape[1]), np.array([c[0]])] + [np.array([np.log(g)]) for g in gaps]

    def loglik(self, y, etas, aux):
        eta = etas[0]
        cuts = self._cuts(etas)
        k = y.astype(int)
        km = self.n_levels - 1
        rows = np.arange(len(y))
        # placeholders keep every branch finite; np.where picks the right one
        u = cuts[rows, np.minimum(k, km - 1)] - eta  # upper cut (valid if k < K-1)
        l = cuts[rows, np.maximum(k - 1, 0)] - eta  # lower cut (valid if k > 0)
        mid = u + _log1mexp(np.minimum(l - u, -1e-12)) + log_expit(-u) + log_expit(-l)
        return np.where(k == 0, log_expit(u), np.where(k == km, log_expit(-l), mid))

    def probs(self, etas):
        eta = etas[0]
        cdf = expit(self._cuts(etas) - eta[:, None])
        cdf = np.column_stack([np.zeros_like(eta), cdf, np.ones_like(eta)])
        return np.diff(cdf, axis=1)

    def mean(self, etas, aux):
        return self.probs(etas) @ self.scores

    def simulate(self, etas, aux, rng):
        cum = np.cumsum(self.probs(etas), axis=1)
        u = rng.random(len(etas[0]))
        return np.minimum((u[:, None] > cum).sum(axis=1), self.n_levels - 1).astype(float)


FAMILIES = {
    cls.name: cls
    for cls in [
        Gaussian,
        LogNormal,
        GammaLog,
        TweedieLog,
        GammaHurdle,
        BetaLogit,
        ZeroOneInflatedBeta,
        PoissonLog,
        NegBinLog,
        ZeroInflatedPoisson,
        ZeroInflatedNegBin,
        HurdlePoisson,
        HurdleNegBin,
        BinomialLogit,
        BetaBinomialLogit,
        CumulativeLogit,
    ]
}


def make_family(name: str, **kwargs) -> Family:
    if name not in FAMILIES:
        raise KeyError(f"unknown family {name!r}; options: {sorted(FAMILIES)}")
    return FAMILIES[name](**kwargs)
