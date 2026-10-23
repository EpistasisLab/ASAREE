"""Fixed-family, cell-stratified case bootstrap using native model refits."""

from __future__ import annotations

import time
import warnings

import numpy as np
import pandas as pd


def bootstrap_intervals(frame, x, y, grid, weights, family, config, ratio, trials=None):
    from asaree.stats_sm.analysis import fit_model, predict_model

    requested = config["n_boot"]
    rng = np.random.default_rng(np.random.SeedSequence([config["seed"], 1101]))
    groups = list(frame.groupby([f"factor_{i}" for i in range(len(config["factors"]))]).indices.values())
    draws, failures, attempted = [], 0, 0
    start = time.monotonic()
    # A bounded worker task must never silently present an incomplete simulation as complete.
    budget = config.get("_bootstrap_budget_s", 60)
    for _ in range(requested):
        if time.monotonic() - start >= budget:
            break
        attempted += 1
        indices = np.concatenate([rng.choice(group, size=len(group), replace=True) for group in groups])
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = fit_model(y[indices], x[indices], family, config, None if trials is None else trials[indices])
                converged = getattr(result, "converged", getattr(result, "mle_retvals", {}).get("converged", True))
                if not converged and hasattr(result, "mle_retvals"):
                    # BFGS can stop with precision loss near an optimum (notably beta
                    # regression). Ask an independent native optimizer to finish;
                    # never relabel an optimizer failure as convergence ourselves.
                    result = result.model.fit(start_params=result.params, method="lbfgs", disp=0, maxiter=500)
                    converged = result.mle_retvals.get("converged", False)
                if not converged or not np.isfinite(result.params).all():
                    raise ValueError("Nonconverged bootstrap refit.")
                covariance = np.asarray(result.cov_params())
                if not np.isfinite(covariance).all() or np.linalg.eigvalsh((covariance + covariance.T) / 2).min() <= 0:
                    raise ValueError("Unidentified bootstrap refit.")
                if family not in {"gaussian", "lognormal", "gamma"} and np.max(np.abs(result.params)) > 30:
                    raise ValueError("Boundary bootstrap refit.")
                means = np.asarray(predict_model(result, grid, family, config))
                quantities = []
                for pieces in weights:
                    values = np.asarray([w @ means for w in pieces])
                    if ratio:
                        if np.any(values <= 0):
                            raise ValueError("Undefined bootstrap ratio.")
                        value = values[0] / values[1]
                        if len(values) == 4:
                            value /= values[2] / values[3]
                    else:
                        value = values[0] - values[1]
                        if len(values) == 4:
                            value -= values[2] - values[3]
                    quantities.append(value)
                if not np.isfinite(quantities).all():
                    raise ValueError("Nonfinite bootstrap prediction.")
                draws.append(quantities)
        except (ValueError, RuntimeError, np.linalg.LinAlgError, FloatingPointError, OverflowError):
            failures += 1
    usable = attempted == requested and len(draws) >= 0.9 * requested
    bounds = np.quantile(draws, [0.025, 0.975], axis=0) if usable else np.full((2, len(weights)), np.nan)
    status = "completed" if usable else "Time budget exceeded" if attempted < requested else "Too many failed refits"
    metadata = dict(
        kind="bootstrap",
        method="Cell-stratified case bootstrap; fixed selected model; percentile intervals",
        requested=requested,
        attempted=attempted,
        successful=len(draws),
        failed=failures,
        status=status,
        seed=config["seed"],
        seed_stream=1101,
        minimum_success_fraction=0.9,
    )
    return pd.DataFrame(dict(bootstrap_ci_low=bounds[0], bootstrap_ci_high=bounds[1])), metadata
