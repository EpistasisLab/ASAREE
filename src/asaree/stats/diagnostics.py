"""Simulation-based model diagnostics.

Residuals are simulation-based randomised quantile (PIT) residuals: for each
observation, the share of simulated outcomes below the observed value, with
ties randomised. Under a correctly specified model they are U(0, 1) for every
family, continuous or discrete (Dunn & Smyth 1996, J Comput Graph Stat 5:236;
the simulation form follows the DHARMa approach of Hartig).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .engine import FitResult, Model

SEVERE_P = 0.01


def simulate_matrix(model: Model, theta, n_sim: int, rng) -> np.ndarray:
    etas = model.etas(theta)
    return np.column_stack([model.family.simulate(etas, model.aux, rng) for _ in range(n_sim)])


def pit_residuals(y, sims, rng) -> np.ndarray:
    below = (sims < y[:, None]).sum(axis=1)
    equal = (sims == y[:, None]).sum(axis=1)
    s = sims.shape[1]
    return (below + rng.random(len(y)) * (equal + 1)) / (s + 1)


def _two_sided(obs, simvals):
    simvals = simvals[np.isfinite(simvals)]
    if len(simvals) == 0 or not np.isfinite(obs):
        return np.nan
    lo = (1 + np.sum(simvals <= obs)) / (len(simvals) + 1)
    hi = (1 + np.sum(simvals >= obs)) / (len(simvals) + 1)
    return float(min(1.0, 2 * min(lo, hi)))


def boundary_flags(model: Model, fit: FitResult) -> list[str]:
    flags = []
    fam = model.family
    for b, spec in enumerate(model.specs):
        th = fit.theta[model.slices[b]]
        if spec.kind == "scalar":
            if np.any(np.abs(th) > 12):
                flags.append(f"{spec.name} at boundary ({th[0]:.1f} on its transformed scale)")
        elif fam.link in ("log", "logit", "cumlogit") or spec.kind == "regression":
            big = np.abs(th[1:] if (spec.kind != "mean" or fam.mean_intercept) else th)
            if big.size and np.max(big) > 10:
                flags.append(
                    f"{spec.name}: coefficient |{np.max(big):.1f}| > 10 on link scale "
                    "(quasi-separation or empty/degenerate cell)"
                )
    mu = fam.mean(model.etas(fit.theta), model.aux)
    if (
        fam.link == "logit"
        and fam.name in ("binomial", "betabinomial", "beta")
        and np.any((mu < 1e-6) | (mu > 1 - 1e-6))
    ):
        flags.append("fitted probabilities at 0 or 1")
    return flags


def diagnose(
    model: Model,
    fit: FitResult,
    cell_idx: np.ndarray,
    cell_labels: list[str],
    n_sim: int,
    rng,
    observed_y_for_stats: np.ndarray | None = None,
) -> dict:
    """Return scalar diagnostics, a per-cell table, flags and arrays for plotting."""
    y = model.y
    sims = simulate_matrix(model, fit.theta, n_sim, rng)
    pit = pit_residuals(y, sims, rng)
    mu = model.family.mean(model.etas(fit.theta), model.aux)
    if model.family.name in ("binomial", "betabinomial"):
        scale = model.aux["trials"]
        mu_y = mu * scale
    else:
        mu_y = mu
    out: dict = {}
    out["converged"] = fit.converged
    out["iterations"] = fit.n_iter
    out["max_abs_gradient"] = fit.max_abs_grad
    out["hessian_positive_definite"] = fit.hessian_pd
    out["hessian_condition_number"] = fit.hessian_cond
    out["loglik"] = fit.loglik
    out["aic"] = fit.aic
    out["bic"] = fit.bic
    out["n_params"] = fit.n_params
    # uniformity
    out["ks_uniform_p"] = float(stats.kstest(pit, "uniform").pvalue)
    # dispersion: variance of y - mu, observed vs simulated
    obs_disp = np.var(y - mu_y)
    sim_disp = np.var(sims - mu_y[:, None], axis=0)
    out["dispersion_ratio"] = float(obs_disp / np.mean(sim_disp)) if np.mean(sim_disp) > 0 else np.nan
    out["dispersion_p"] = _two_sided(obs_disp, sim_disp)
    # zero inflation
    obs_z = np.sum(y == 0)
    sim_z = np.sum(sims == 0, axis=0)
    if obs_z > 0 or np.any(sim_z > 0):
        out["zeros_observed"] = int(obs_z)
        out["zeros_expected"] = float(np.mean(sim_z))
        out["zero_inflation_p"] = _two_sided(obs_z, sim_z)
    # outliers: observations outside the whole simulation envelope
    outside = int(np.sum((sims < y[:, None]).all(axis=1) | (sims > y[:, None]).all(axis=1)))
    out["n_outside_envelope"] = outside
    out["outside_envelope_p"] = float(stats.binomtest(outside, len(y), 2.0 / (n_sim + 1), alternative="greater").pvalue)
    # residual vs fitted
    rho, p_rho = stats.spearmanr(pit, mu)
    out["resid_vs_fitted_spearman"] = float(rho) if np.isfinite(rho) else np.nan
    out["resid_vs_fitted_p"] = float(p_rho) if np.isfinite(p_rho) else np.nan
    # residuals by factorial cell
    z = stats.norm.ppf(np.clip(pit, 1e-4, 1 - 1e-4))
    groups = [z[cell_idx == c] for c in np.unique(cell_idx)]
    groups = [g for g in groups if len(g) >= 2]
    if len(groups) >= 2:
        out["cell_mean_resid_anova_p"] = float(stats.f_oneway(*groups).pvalue)
        out["cell_var_resid_levene_p"] = float(stats.levene(*groups, center="median").pvalue)
    cell_tab = (
        pd.DataFrame(dict(cell=[cell_labels[c] for c in cell_idx], pit=pit, z=z))
        .groupby("cell", sort=False)
        .agg(n=("pit", "size"), mean_pit=("pit", "mean"), mean_z=("z", "mean"), sd_z=("z", "std"))
        .reset_index()
    )
    # influence
    if fit.cooks is not None:
        out["max_cooks_distance"] = float(np.max(fit.cooks))
        out["n_cooks_gt_4_over_n"] = int(np.sum(fit.cooks > 4.0 / len(y)))
        out["n_cooks_gt_1"] = int(np.sum(fit.cooks > 1.0))
        out["max_leverage"] = float(np.max(fit.leverage))
    # observed vs simulated distribution summaries
    ppc = {}
    funcs = {
        "mean": np.mean,
        "sd": np.std,
        "q10": lambda a: np.quantile(a, 0.1),
        "median": np.median,
        "q90": lambda a: np.quantile(a, 0.9),
        "max": np.max,
        "min": np.min,
    }
    for k, f in funcs.items():
        o = f(y)
        s = np.apply_along_axis(f, 0, sims)
        ppc[k] = dict(
            observed=float(o),
            sim_mean=float(np.mean(s)),
            sim_lo=float(np.quantile(s, 0.025)),
            sim_hi=float(np.quantile(s, 0.975)),
            p=_two_sided(o, s),
        )
    out["ppc"] = ppc
    # flags
    flags = []
    if not fit.converged:
        flags.append("did not converge")
    if fit.hessian_pd is False:
        flags.append("information matrix not positive definite (non-identifiable or boundary)")
    flags += boundary_flags(model, fit)
    if out["ks_uniform_p"] < SEVERE_P:
        flags.append(f"PIT residuals not uniform (KS p={out['ks_uniform_p']:.3g})")
    if out["dispersion_p"] < SEVERE_P:
        kind = "over" if out["dispersion_ratio"] > 1 else "under"
        flags.append(f"{kind}dispersion (ratio={out['dispersion_ratio']:.2f}, p={out['dispersion_p']:.3g})")
    if out.get("zero_inflation_p", 1) < SEVERE_P:
        flags.append(f"zero count mismatch (obs={out['zeros_observed']}, exp={out['zeros_expected']:.1f})")
    if out["resid_vs_fitted_p"] < SEVERE_P:
        flags.append("residual trend with fitted values (mean or variance function misspecified)")
    if out.get("cell_mean_resid_anova_p", 1) < SEVERE_P:
        flags.append("residual means differ by cell (mean structure misspecified)")
    if out.get("cell_var_resid_levene_p", 1) < SEVERE_P:
        flags.append("residual spread differs by cell (dispersion differs by cell)")
    if out.get("n_cooks_gt_1", 0) > 0:
        flags.append(f"{out['n_cooks_gt_1']} highly influential observation(s) (Cook's D > 1)")
    if out["outside_envelope_p"] < SEVERE_P:
        flags.append(f"{outside} observations outside the simulation envelope")
    bad_ppc = [k for k, v in ppc.items() if v["p"] < SEVERE_P]
    if bad_ppc:
        flags.append("observed vs simulated mismatch in: " + ", ".join(bad_ppc))
    out["flags"] = flags
    out["severe"] = bool(
        (not fit.converged)
        or fit.hessian_pd is False
        or out["ks_uniform_p"] < SEVERE_P
        or out["dispersion_p"] < SEVERE_P
        or out.get("zero_inflation_p", 1) < SEVERE_P
    )
    out["_pit"] = pit
    out["_mu"] = mu
    out["_sims"] = sims
    out["_cell_table"] = cell_tab
    return out


def diagnostic_plot(path, title, y, diag, cell_idx, cell_labels):
    """Four-panel PNG: PIT QQ, PIT vs fitted, PIT by cell, observed vs simulated ECDF."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    blue, orange, ink, muted, grid = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df"
    pit, mu, sims = diag["_pit"], diag["_mu"], diag["_sims"]
    fig, ax = plt.subplots(2, 2, figsize=(11, 8), dpi=110)
    for a in ax.ravel():
        a.grid(True, color=grid, lw=0.6)
        a.set_axisbelow(True)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        a.tick_params(colors=muted, labelsize=8)
    n = len(pit)
    a = ax[0, 0]
    a.plot([0, 1], [0, 1], color=muted, lw=1, ls="--")
    a.plot((np.arange(1, n + 1) - 0.5) / n, np.sort(pit), color=blue, lw=2)
    a.set_title(f"PIT residual QQ vs U(0,1)  (KS p={diag['ks_uniform_p']:.3g})", fontsize=10, color=ink)
    a.set_xlabel("expected quantile", color=muted, fontsize=9)
    a.set_ylabel("observed PIT", color=muted, fontsize=9)
    a = ax[0, 1]
    r = stats.rankdata(mu) / n
    a.scatter(r, pit, s=12, color=blue, alpha=0.6, lw=0)
    for q in (0.25, 0.5, 0.75):
        a.axhline(q, color=muted, lw=0.8, ls=":")
    a.set_title(f"PIT vs fitted rank  (Spearman p={diag['resid_vs_fitted_p']:.3g})", fontsize=10, color=ink)
    a.set_xlabel("fitted mean (rank-transformed)", color=muted, fontsize=9)
    a = ax[1, 0]
    cells = np.unique(cell_idx)
    if len(cells) <= 64:
        a.boxplot(
            [pit[cell_idx == c] for c in cells],
            widths=0.6,
            showfliers=False,
            medianprops=dict(color=blue, lw=2),
            boxprops=dict(color=muted),
            whiskerprops=dict(color=muted),
            capprops=dict(color=muted),
        )
        a.set_xticks(range(1, len(cells) + 1))
        a.set_xticklabels([cell_labels[c] for c in cells], rotation=90, fontsize=6)
    a.axhline(0.5, color=muted, lw=0.8, ls="--")
    a.set_title("PIT by factorial cell", fontsize=10, color=ink)
    a = ax[1, 1]
    xs = np.sort(y)
    for j in range(min(sims.shape[1], 60)):
        s = np.sort(sims[:, j])
        a.step(s, np.arange(1, n + 1) / n, color=orange, alpha=0.08, lw=1, where="post")
    a.step(xs, np.arange(1, n + 1) / n, color=blue, lw=2, where="post", label="observed")
    a.plot([], [], color=orange, lw=2, label="simulated from fit")
    a.legend(frameon=False, fontsize=8)
    a.set_title("Observed vs simulated ECDF", fontsize=10, color=ink)
    fig.suptitle(title, fontsize=12, color=ink)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
