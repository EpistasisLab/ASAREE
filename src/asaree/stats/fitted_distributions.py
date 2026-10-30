"""Support-preserving CDF/quantile operations for fitted native models."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats
from scipy.special import expit


@dataclass
class FittedDistribution:
    distribution: object | None = None
    discrete: bool = False
    zero_probability: np.ndarray | None = None
    category_probabilities: np.ndarray | None = None
    construction: str = ""

    def cdf(self, values):
        values = np.asarray(values)
        if self.category_probabilities is not None:
            categories = np.arange(self.category_probabilities.shape[1])
            result = np.sum(self.category_probabilities * (categories <= values[:, None]), axis=1)
            return np.where(values >= categories[-1], 1.0, np.where(values < 0, 0.0, result))
        result = np.asarray(self.distribution.cdf(values))
        if self.zero_probability is not None:
            result = np.where(values < 0, 0, self.zero_probability + (1 - self.zero_probability) * result)
        return result

    def ppf(self, probabilities):
        probabilities = np.asarray(probabilities)
        # Endpoints arise from floating-point CDF saturation, never from an
        # intended draw of infinite responses. Endpoint PIT observations are
        # rejected by permutation orchestration, not silently clipped there.
        if np.any((probabilities <= 0) | (probabilities >= 1)):
            raise ValueError("Quantile reconstruction requires probabilities strictly inside (0, 1)")
        if self.category_probabilities is not None:
            cumulative = np.cumsum(self.category_probabilities, axis=1)
            cumulative[:, -1] = 1.0  # Exact finite-support CDF endpoint, despite roundoff in summing probabilities.
            return (probabilities[:, None] > cumulative).sum(axis=1).astype(float)
        if self.zero_probability is not None:
            adjusted = (probabilities - self.zero_probability) / (1 - self.zero_probability)
            values = np.where(adjusted <= 0, 0, self.distribution.ppf(np.maximum(adjusted, np.finfo(float).tiny)))
        else:
            values = np.asarray(self.distribution.ppf(probabilities))
        low, high = self.distribution.support()
        supported = (values >= low) & (values <= high) if self.discrete else (values > low) & (values < high)
        if not np.isfinite(values).all() or not np.all(supported):
            raise ValueError("Inverse-CDF reconstruction produced an invalid or boundary outcome")
        return values

    def draw(self, rng, n):
        return self.ppf(rng.uniform(np.finfo(float).eps, 1 - np.finfo(float).eps, n))

    def pit(self, values, rng):
        upper = self.cdf(values)
        lower = self.cdf(np.asarray(values) - 1) if self.discrete else upper
        uniforms = lower + rng.random(len(upper)) * (upper - lower) if self.discrete else upper
        if not np.isfinite(uniforms).all() or np.any((uniforms <= 0) | (uniforms >= 1)):
            raise ValueError("Fitted CDF saturates at an endpoint; quantile residual reconstruction is unavailable")
        return uniforms


def fitted_distribution(result, family, config, trials=None):
    params, model = np.asarray(result.params), result.model
    mu = np.asarray(result.predict())
    if family in {"gaussian", "lognormal"}:
        sd = np.sqrt(result.scale)
        if not np.isfinite(sd) or sd <= 0:
            raise ValueError("A deterministic fitted distribution has no resampling variance")
        distribution = stats.norm(loc=mu, scale=sd) if family == "gaussian" else stats.lognorm(s=sd, scale=np.exp(mu))
        return FittedDistribution(
            distribution,
            construction="Fitted normal errors with residual-df variance estimate"
            + (" on log(outcome)" if family == "lognormal" else ""),
        )
    if family == "gamma":
        from asaree.stats.analysis import comparable_likelihood

        _, _, _, diagnostics = comparable_likelihood(result, family, np.asarray(model.endog), config)
        shape = diagnostics.get("gamma_shape_ml")
        if shape is None:
            raise ValueError("Gamma ML shape estimation failed")
        return FittedDistribution(
            stats.gamma(a=shape, scale=mu / shape), construction="Gamma with fitted mean and ML shape"
        )
    if family == "beta":
        phi = model.predict(params, which="precision")
        return FittedDistribution(
            stats.beta(mu * phi, (1 - mu) * phi), construction="Beta with fitted mean and precision"
        )
    if family == "poisson":
        return FittedDistribution(stats.poisson(mu), True, construction="Poisson with fitted mean")
    if family == "negbin":
        alpha = params[-1]
        return FittedDistribution(
            stats.nbinom(1 / alpha, 1 / (1 + alpha * mu)), True, construction="NB2 with fitted mean and alpha"
        )
    if family == "binomial":
        return FittedDistribution(
            stats.binom(1 if trials is None else np.asarray(trials).astype(int), mu),
            True,
            construction="Binomial; fitted probabilities and original trial totals",
        )
    if family in {"zip", "zinb"}:
        k = model.exog.shape[1]
        count_mean = np.exp(model.exog @ params[1 : 1 + k])
        inflation = np.full(len(mu), expit(params[0]))
        base = (
            stats.poisson(count_mean)
            if family == "zip"
            else stats.nbinom(1 / params[-1], 1 / (1 + params[-1] * count_mean))
        )
        return FittedDistribution(
            base,
            True,
            zero_probability=inflation,
            construction="Fitted count mixture and intercept-only structural-zero probability",
        )
    if family == "ordinal_logit":
        return FittedDistribution(
            discrete=True,
            category_probabilities=mu,
            construction="Ordered category probabilities from fitted thresholds and cumulative logit",
        )
    raise ValueError(f"No fitted CDF/quantile implementation for {family}")
