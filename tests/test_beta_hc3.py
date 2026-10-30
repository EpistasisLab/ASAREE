"""Numerical verification of the specified joint beta expected-information HC3."""

import numpy as np
import pytest
from statsmodels.genmod.families.links import Probit
from statsmodels.othermod.betareg import BetaModel

from asaree.stats.beta_covariance import beta_parts
from asaree.stats.covariance import robust_covariance


@pytest.mark.parametrize("varying_precision", [False, True])
@pytest.mark.parametrize("probit", [False, True])
def test_joint_beta_scores_information_and_hc3_match_independent_references(varying_precision, probit):
    rng = np.random.default_rng(99)
    x = np.column_stack((np.ones(120), rng.normal(size=120)))
    z = np.column_stack((np.ones(120), rng.normal(size=120))) if varying_precision else np.ones((120, 1))
    y = rng.beta(3, 5, size=120)
    model = BetaModel(y, x, exog_precision=z, **({"link": Probit()} if probit else {}))
    result = model.fit(disp=0)
    scores, bread, leverage, info = beta_parts(result, observation_information=True)
    np.testing.assert_allclose(scores, model.score_obs(result.params), rtol=1e-10, atol=1e-10)
    numerical = np.empty_like(scores)
    for j in range(len(result.params)):
        step = np.zeros(len(result.params))
        step[j] = 1e-6
        numerical[:, j] = (model.loglikeobs(result.params + step) - model.loglikeobs(result.params - step)) / 2e-6
    np.testing.assert_allclose(scores, numerical, rtol=1e-5, atol=1e-7)
    _, factors = model.score_hessian_factor(result.params, return_hessian=True, observed=False)
    aa, ab, bb = factors
    expected = -np.block([[(x.T * aa) @ x, (x.T * ab) @ z], [(z.T * ab) @ x, (z.T * bb) @ z]])
    np.testing.assert_allclose(info.sum(axis=0), expected, rtol=1e-10, atol=1e-10)
    assert leverage.sum() == pytest.approx(len(result.params), rel=1e-12)
    covariance, record = robust_covariance(result, "beta")
    reference = bread @ sum(np.outer(s, s) / (1 - h) ** 2 for s, h in zip(scores, leverage, strict=True)) @ bread
    np.testing.assert_allclose(covariance, reference, rtol=1e-12, atol=1e-12)
    assert record["computed"] == "HC3"
    assert "mean/precision" in record["implementation"]
    # The precision information matters; dropping it changes the leverages.
    mean_only = np.einsum("ab,iba->i", np.linalg.inv(info[:, :2, :2].sum(axis=0)), info[:, :2, :2])
    assert not np.allclose(mean_only, leverage)
