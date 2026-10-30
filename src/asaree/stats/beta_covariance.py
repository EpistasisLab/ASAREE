"""Reviewed joint mean/precision beta sandwich construction.

Scores use digamma derivatives of the beta likelihood. Expected information
uses trigamma derivatives, including the cross block. Leverage is the specified
trace(H^-1 I_i), not an OLS hat value or a mean-only approximation.
"""

from __future__ import annotations

import numpy as np
from scipy.special import digamma, polygamma


def beta_parts(result, *, observation_information=False, maximum_condition=1e10):
    """Return observation scores, expected-information bread, leverage and I_i.

    Precision may have its own multi-column design and link; an intercept-only
    precision is still estimated and contributes to every matrix here.
    """
    model, params = result.model, np.asarray(result.params)
    x, z, y = np.asarray(model.exog), np.asarray(model.exog_precision), np.asarray(model.endog)
    mu = np.asarray(model.link.inverse(x @ params[: x.shape[1]]))
    phi = np.asarray(model.link_precision.inverse(z @ params[x.shape[1] :]))
    if not (
        np.isfinite(mu).all()
        and np.isfinite(phi).all()
        and np.all((mu > 0) & (mu < 1))
        and np.all(phi > 0)
        and np.all((y > 0) & (y < 1))
    ):
        raise ValueError("Beta scores require finite interior means/outcomes and positive finite precision")
    a, b = mu * phi, (1 - mu) * phi
    log_y, log_complement = np.log(y), np.log1p(-y)
    score_mu = phi * (log_y - log_complement - digamma(a) + digamma(b))
    score_phi = digamma(phi) - mu * digamma(a) - (1 - mu) * digamma(b) + mu * log_y + (1 - mu) * log_complement
    dm, dp = 1 / model.link.deriv(mu), 1 / model.link_precision.deriv(phi)
    jm, jp = dm[:, None] * x, dp[:, None] * z
    scores = np.column_stack((score_mu[:, None] * jm, score_phi[:, None] * jp))
    ta, tb, tp = polygamma(1, a), polygamma(1, b), polygamma(1, phi)
    imm = phi**2 * (ta + tb)
    imp = phi * (mu * ta - (1 - mu) * tb)
    ipp = mu**2 * ta + (1 - mu) ** 2 * tb - tp
    k = x.shape[1]
    cross = (jm.T * imp) @ jp
    total = np.block([[(jm.T * imm) @ jm, cross], [cross.T, (jp.T * ipp) @ jp]])
    if not np.isfinite(total).all() or np.linalg.cond(total) > maximum_condition:
        raise ValueError("Joint beta expected information is numerically unstable")
    # Cholesky rejects non-positive-definite information; no pseudoinverse or clipping.
    chol = np.linalg.cholesky(total)
    bread = np.linalg.solve(chol.T, np.linalg.solve(chol, np.eye(len(params))))
    # trace(H^-1 I_i), evaluated by blocks without allocating an n × p × p tensor.
    leverage = (
        imm * np.einsum("ij,jk,ik->i", jm, bread[:k, :k], jm)
        + 2 * imp * np.einsum("ij,jk,ik->i", jm, bread[:k, k:], jp)
        + ipp * np.einsum("ij,jk,ik->i", jp, bread[k:, k:], jp)
    )
    if not (np.isfinite(scores).all() and np.isfinite(total).all() and np.isfinite(leverage).all()):
        raise ValueError("Non-finite beta score, expected information or generalized leverage")
    information = None
    if observation_information:
        information = np.empty((len(y), len(params), len(params)))
        information[:, :k, :k] = imm[:, None, None] * jm[:, :, None] * jm[:, None, :]
        information[:, :k, k:] = imp[:, None, None] * jm[:, :, None] * jp[:, None, :]
        information[:, k:, :k] = information[:, :k, k:].transpose(0, 2, 1)
        information[:, k:, k:] = ipp[:, None, None] * jp[:, :, None] * jp[:, None, :]
    return scores, bread, leverage, information
