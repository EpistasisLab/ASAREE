"""Orchestration: validation -> model selection -> GLM inference -> sensitivity analyses."""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .contrasts import QuantitySet, build_contrasts, default_families
from .design import DesignBuilder, DesignSpec, cell_index, full_factorial_terms, term_name
from .diagnostics import diagnose
from .inference import (
    BootLRT,
    Predictor,
    ci_mcse,
    coefficient_table,
    constraint_matrix,
    effective_jobs,
    holm,
    parametric_bootstrap,
    percentile_ci,
    response_contrast_wald,
    wald_test,
)
from .nonparametric import art_c, art_terms, permutation_tests
from .selection import akaike_weights, choose, fit_candidates, profile_outcome, response_arrays, working_values
from .validation import factor_registry, resolve_levels, validate

log = logging.getLogger("asaree.stats")


@dataclass
class OutcomeResult:
    name: str
    family: str | None = None
    model: object = None
    fit: object = None
    diagnostics: dict = field(default_factory=dict)
    tables: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    error: str | None = None
    cell_index: np.ndarray | None = None
    cell_labels: list | None = None


@dataclass
class AnalysisResults:
    config: AnalysisConfig
    ok: bool
    validation: pd.DataFrame
    factor_registry: pd.DataFrame | None = None
    outcomes: dict = field(default_factory=dict)
    tables: dict = field(default_factory=dict)
    runtime_s: float = 0.0

    def save(self, output_dir: str, plots: bool | None = None):
        from .report import save_results

        return save_results(self, output_dir, self.config.make_plots if plots is None else plots)


# ======================================================================
def run_analysis(df: pd.DataFrame, cfg: AnalysisConfig) -> AnalysisResults:
    t0 = time.time()
    val, ok = validate(df, cfg)
    res = AnalysisResults(cfg, ok, val)
    if not ok:
        log.error("validation failed; see results.validation")
        return res
    levels, block_levels = resolve_levels(df, cfg)
    res.factor_registry = factor_registry(df, cfg, levels, block_levels)
    cat = cfg.categorical
    terms = [tuple(t) for t in cfg.terms] if cfg.terms else full_factorial_terms(cat, cfg.max_interaction_order)
    cfams = (default_families(cat) if cfg.include_default_contrasts else []) + list(cfg.contrast_families)
    names = [f.name for f in cfams]
    if len(set(names)) != len(names):
        raise ValueError("contrast family names must be unique")
    seeds = np.random.SeedSequence(cfg.seed).spawn(len(cfg.outcomes))
    for o, ss in zip(cfg.outcomes, seeds, strict=True):
        log.info("outcome %s: start", o.name)
        try:
            res.outcomes[o.name] = analyze_outcome(df, cfg, o, levels, block_levels, terms, cfams, ss)
        except Exception as e:  # keep the run alive; report the failure
            log.exception("outcome %s failed", o.name)
            res.outcomes[o.name] = OutcomeResult(o.name, error=f"{e}\n{traceback.format_exc()}")
        log.info("outcome %s: done", o.name)
    res.tables = assemble(res)
    res.runtime_s = time.time() - t0
    return res


# ======================================================================
def analyze_outcome(df, cfg: AnalysisConfig, o, levels, block_levels, terms, cfams, ss) -> OutcomeResult:
    out = OutcomeResult(o.name)
    keep = df[o.name].notna()
    if o.denominator:
        keep &= df[o.denominator].notna()
    sub = df.loc[keep].reset_index(drop=True)
    y, aux = response_arrays(sub, o)
    cat, cont, blocks = cfg.categorical, cfg.continuous, cfg.blocks
    spec = DesignSpec(
        cat,
        levels,
        terms,
        covariates=cont,
        covariate_means={c: float(sub[c].mean()) for c in cont},
        blocks=blocks,
        block_levels=block_levels,
    )
    builder = DesignBuilder(spec)
    X = builder.build(sub)
    cells = builder.cell_table()
    cidx = cell_index(sub, cat, levels)
    labels = [", ".join(f"{f}={r[f]}" for f in cat) for _, r in cells.iterrows()]
    out.cell_index, out.cell_labels = cidx, labels
    rng_sel, rng_diag, s_boot, s_lrt, rng_perm = [
        np.random.default_rng(s) if i in (0, 1, 4) else s for i, s in enumerate(ss.spawn(5))
    ]
    clustered = any(sub[b].nunique() > 1 for b in blocks)
    prof = profile_outcome(y, aux, o, clustered, " x ".join(blocks))

    # ---------------------------------------------------- model selection
    cand, fitted, notes, mtype = fit_candidates(y, aux, X, o, cidx, labels, min(cfg.n_sim_diagnostics, 200), rng_sel)
    cand = akaike_weights(cand)
    chosen, rationale = choose(cand, fitted, o)
    if chosen is None:
        raise RuntimeError(f"no model could be fitted: {cand.to_dict('records')}")
    model, fit, _ = fitted[chosen]
    out.family, out.model, out.fit = chosen, model, fit
    out.notes = notes
    cand["selected"] = cand.family == chosen
    prof.update(
        measurement_type_used=mtype,
        candidates=", ".join(cand.family),
        selected_family=chosen,
        selection_rationale=rationale,
        notes=" | ".join(notes),
    )
    out.tables["outcome_profile"] = pd.DataFrame([prof])
    out.tables["model_candidates"] = cand

    log.info("  %s selected %s", o.name, chosen)
    # --------------------------------------------------------- diagnostics
    diag = diagnose(model, fit, cidx, labels, cfg.n_sim_diagnostics, rng_diag)
    out.diagnostics = diag
    out.tables["diagnostics"] = pd.DataFrame(
        [
            {k: v for k, v in diag.items() if not k.startswith("_") and k not in ("ppc", "flags")}
            | dict(family=chosen, flags="; ".join(diag["flags"]))
        ]
    )
    out.tables["diagnostics_ppc"] = pd.DataFrame([dict(statistic=k, **v) for k, v in diag["ppc"].items()])
    out.tables["diagnostics_by_cell"] = diag["_cell_table"]
    out.tables["coefficients"] = coefficient_table(model, fit, builder.columns)

    # ------------------------------------------------------- quantities
    contrasts = [c for fam in cfams for c in build_contrasts(fam, cells, levels)]
    qset = QuantitySet(cells, cat, levels, contrasts, model.family.effect_scale, model.family.logit_link_effects)
    pred = Predictor(model, builder)
    cm, cl = pred.cells(fit.theta)
    est = qset.evaluate(cm, cl)
    t = time.time()
    draws, fails = parametric_bootstrap(
        model, fit, pred, qset, cfg.n_boot, s_boot, cfg.n_jobs, cfg.boot_dispersion_correction
    )
    log.info("  %s bootstrap: %d draws in %.1fs (%d failed refits)", o.name, cfg.n_boot, time.time() - t, fails)
    lo, hi = percentile_ci(draws, cfg.ci_level)
    mlo, mhi = ci_mcse(draws, cfg.ci_level)
    q = qset.meta.copy()
    q["estimate"] = est
    q["ci_low"], q["ci_high"] = lo, hi
    q["boot_se"] = np.nanstd(draws, axis=0, ddof=1)
    q["ci_low_mcse"], q["ci_high_mcse"] = mlo, mhi
    q["n_boot_ok"] = int(np.sum(~np.isnan(draws).any(axis=1)))
    q["n_boot_failed"] = fails
    n_per_cell = np.bincount(cidx, minlength=len(cells))
    out.tables["quantities"] = q

    # ------------------------------------------------- hypothesis tests
    p_base = X.shape[1]
    eye = np.eye(p_base)
    hyp_fams: list[tuple[str, list]] = [
        ("terms", [(term_name(tt), eye[builder.term_columns[term_name(tt)]]) for tt in terms])
    ]
    if cont:
        hyp_fams.append(("covariates", [(c, eye[builder.term_columns[c]]) for c in cont]))
    cell_rows = builder.cell_rows()
    for fam in cfams:
        hyp_fams.append(
            (fam.name, [(c.label, c.link_vector(cell_rows)[None, :]) for c in contrasts if c.family == fam.name])
        )
    multi_part = len(model.regression_blocks) > 1
    con_est, con_se, con_p = response_contrast_wald(model, fit, pred, qset)
    con_lookup = {(c.family, c.label): k for k, c in enumerate(contrasts)}
    tests = []
    for fam_name, hyps in hyp_fams:
        for label, L in hyps:
            W, dfw, pw = wald_test(fit.theta, fit.cov_hc3, constraint_matrix(model, L))
            row = dict(family=fam_name, hypothesis=label, wald_hc3_chi2=W, df=dfw, p_wald_hc3=pw)
            if fam_name in ("terms", "covariates"):
                row["null_hypothesis"] = (
                    "term coefficients = 0"
                    + (" in every model part (joint)" if multi_part else "")
                    + " (Type III, sum-to-zero coding)"
                )
            else:
                mean_cols = slice(1, None) if not model.family.mean_intercept else slice(None)
                row["link_scale_estimate"] = float(L[0, mean_cols] @ fit.theta[model.slices[0]])
                k = con_lookup[(fam_name, label)]
                row["response_scale_se"] = con_se[k]
                row["p_wald_response_hc3"] = con_p[k]
                row["null_hypothesis"] = (
                    "response-scale contrast = 0 (primary); p_wald_hc3 / p_lr_chi2 test the "
                    "joint null of no difference in either model part"
                    if multi_part
                    else "link-scale contrast = 0"
                )
            tests.append(row)
    tests = pd.DataFrame(tests)
    skip = {f.name for f in cfams} if multi_part else set()
    tests = _bootstrap_lrt(model, fit, hyp_fams, tests, cfg, s_lrt, o.name, skip)
    if multi_part:
        m = tests.family.isin(skip)
        tests.loc[m, "p_primary"] = tests.loc[m, "p_wald_response_hc3"]
        tests.loc[m, "p_method"] = (
            "HC3 delta-method Wald on the response-scale contrast (two-part model: the "
            "equal-overall-mean null is nonlinear in the parameters)"
        )
    tests["p_primary_holm"] = np.nan
    for _fam_name, idx in tests.groupby("family").groups.items():
        tests.loc[idx, "p_primary_holm"] = holm(tests.loc[idx, "p_primary"].to_numpy())
    tests["significant_primary"] = tests.p_primary_holm < cfg.alpha
    out.tables["tests"] = tests

    # contrast table = estimates + intervals + tests
    con = q[q.quantity == "contrast"].reset_index(drop=True)
    con = con.merge(tests.rename(columns=dict(hypothesis="label")), on=["family", "label"], how="left")
    out.tables["contrasts"] = con
    cellq = q[q.quantity == "cell_mean"].reset_index(drop=True)
    cellq = pd.concat([cells.reset_index(drop=True), cellq], axis=1)
    cellq["n_runs"] = n_per_cell
    cellq["observed_cell"] = n_per_cell > 0
    out.tables["cell_means"] = cellq
    out.tables["marginal_means"] = q[q.quantity == "marginal_mean"].reset_index(drop=True)
    if model.family.logit_link_effects:
        out.tables["link_interactions"] = q[q.quantity == "link_interaction"].reset_index(drop=True)

    log.info("  %s tests done", o.name)
    # ----------------------------------------------------------- ART / ART-C
    w = working_values(y, aux, o)
    wp, scale_note = _permutation_scale(w, model.family.effect_scale)
    if cat:
        art = art_terms(wp, sub, cat, levels, blocks, block_levels)
        art["working_scale"] = scale_note
        art["p_holm"] = holm(art.p.to_numpy())
        out.tables["art_terms"] = art
        artc = []
        for fam in cfams:
            cs = [c for c in contrasts if c.family == fam.name]
            tab = art_c(wp, sub, cat, levels, blocks, block_levels, fam, cs)
            tab["p_holm"] = holm(tab.p.to_numpy())
            artc.append(tab)
        out.tables["artc_contrasts"] = pd.concat(artc, ignore_index=True)

    log.info("  %s ART/ART-C done", o.name)
    # ---------------------------------------------------------- permutation
    perm = permutation_tests(wp, sub, builder, cfg.n_perm, rng_perm)
    perm["working_scale"] = scale_note
    is_cat = perm.term.isin([term_name(tt) for tt in terms])
    perm["p_holm"] = np.nan
    perm.loc[is_cat, "p_holm"] = holm(perm.loc[is_cat, "p_perm"].to_numpy())
    out.tables["permutation_tests"] = perm

    # ------------------------------------------------------ effect stability
    out.tables["effect_stability"] = _effect_stability(fitted, chosen, builder, qset, q, model.family.effect_scale)
    return out


def _permutation_scale(w, effect_scale):
    if effect_scale != "ratio":
        return w, "response scale"
    if np.all(w > 0):
        return np.log(w), "log scale (matches the log-link ratio effects)"
    c = np.min(w[w > 0]) / 2.0
    return np.log(w + c), f"log(y + {c:.4g}) because of exact zeros (approximate match to ratio effects)"


def _bootstrap_lrt(model, fit, hyp_fams, tests, cfg, seed_seq, name, skip=frozenset()):
    """Run null-model bootstrap LR tests family by family within the time budget."""
    tests["lr_stat"] = np.nan
    tests["p_lr_chi2"] = np.nan
    tests["p_boot_lrt"] = np.nan
    tests["n_boot_lrt"] = 0
    tests["p_method"] = ""
    budget = cfg.boot_lrt_time_budget_s
    jobs = effective_jobs(cfg.n_jobs)
    start = time.time()
    per_hyp = None
    fam_seeds = seed_seq.spawn(len(hyp_fams))
    for (fam_name, hyps), fs in zip(hyp_fams, fam_seeds, strict=True):
        idx = tests.index[tests.family == fam_name]
        blrts = []
        for (_label, L), i in zip(hyps, idx, strict=True):
            b = BootLRT(model, fit, L)
            tests.loc[i, "lr_stat"] = b.lr_obs
            tests.loc[i, "p_lr_chi2"] = b.p_chi2
            blrts.append((i, b))
        if cfg.n_boot_lrt <= 0 or fam_name in skip:
            continue
        if per_hyp is None and blrts:
            per_pair = blrts[0][1].time_one(seed=0, n=4)
            per_hyp = per_pair * cfg.n_boot_lrt / (0.85 * jobs)
        remaining = budget - (time.time() - start)
        projected = per_hyp * len(blrts)
        if projected > remaining:
            log.info(
                "  %s: bootstrap LRT for family %s skipped (projected %.0fs > remaining %.0fs)",
                name,
                fam_name,
                projected,
                remaining,
            )
            tests.loc[idx, "p_method"] = (
                f"HC3 Wald (bootstrap LRT skipped: projected {projected:.0f}s "
                f"exceeds remaining budget {max(remaining, 0):.0f}s)"
            )
            continue
        t = time.time()
        for (i, b), s in zip(blrts, fs.spawn(len(blrts)), strict=True):
            p, nok, nfail = b.run(cfg.n_boot_lrt, s, cfg.n_jobs)
            tests.loc[i, "p_boot_lrt"] = p
            tests.loc[i, "n_boot_lrt"] = nok
            tests.loc[i, "p_method"] = "parametric bootstrap LRT (null model)" + (
                f"; {nfail} failed refits" if nfail else ""
            )
        if blrts:
            per_hyp = (time.time() - t) / len(blrts)
    tests["p_primary"] = tests.p_boot_lrt.where(tests.p_boot_lrt.notna(), tests.p_wald_hc3)
    tests.loc[tests.p_boot_lrt.isna() & (tests.p_method == ""), "p_method"] = "HC3 Wald"
    return tests


def _effect_stability(fitted, chosen, builder, qset, q, scale):
    rows = []
    sel = q[q.quantity == "contrast"].reset_index(drop=True)
    for name, (m, f, _d) in fitted.items():
        if name == chosen or not f.converged or not f.hessian_pd:
            continue
        cm, cl = Predictor(m, builder).cells(f.theta)
        alt = qset.evaluate(cm, cl)[qset.contrast_slice()]
        for i, r in sel.iterrows():
            a, s = alt[i], r.estimate
            if scale == "ratio":  # log-ratio difference, SE via delta method on the log scale
                diff = np.log(a) - np.log(s) if a > 0 and s > 0 else np.nan
                se = r.boot_se / s if s > 0 else np.nan
            else:
                diff, se = a - s, r.boot_se
            rows.append(
                dict(
                    family=r.family,
                    label=r.label,
                    alternative_model=name,
                    selected_estimate=s,
                    alternative_estimate=a,
                    standardized_difference=diff / se if se else np.nan,
                )
            )
    return pd.DataFrame(rows)


# ======================================================================
def assemble(res: AnalysisResults) -> dict:
    """Stack per-outcome tables and build the cross-method comparison."""
    tables: dict[str, list] = {}
    errors = []
    for name, o in res.outcomes.items():
        if o.error:
            errors.append(dict(outcome=name, error=o.error.splitlines()[0]))
            continue
        for k, t in o.tables.items():
            t = t.copy()
            if "outcome" in t.columns:
                t = t.drop(columns="outcome")
            t.insert(0, "outcome", name)
            tables.setdefault(k, []).append(t)
    out = {k: pd.concat(v, ignore_index=True) for k, v in tables.items()}
    if errors:
        out["errors"] = pd.DataFrame(errors)
    out.update(compare_methods(out, res.config.alpha))
    return out


def compare_methods(t: dict, alpha: float) -> dict:
    out = {}
    if "tests" in t:
        g = t["tests"]
        g = g[g.family == "terms"][["outcome", "hypothesis", "p_primary", "p_primary_holm", "p_method"]]
        g = g.rename(
            columns=dict(hypothesis="term", p_primary="glm_p", p_primary_holm="glm_p_holm", p_method="glm_p_method")
        )
        if "art_terms" in t:
            a = t["art_terms"][["outcome", "term", "p", "p_holm"]].rename(columns=dict(p="art_p", p_holm="art_p_holm"))
            g = g.merge(a, on=["outcome", "term"], how="left")
        if "permutation_tests" in t:
            p = t["permutation_tests"][["outcome", "term", "p_perm", "p_holm"]].rename(
                columns=dict(p_perm="perm_p", p_holm="perm_p_holm")
            )
            g = g.merge(p, on=["outcome", "term"], how="left")
        sig = [c for c in ("glm_p_holm", "art_p_holm", "perm_p_holm") if c in g]
        flags = g[sig] < alpha
        g["n_methods_significant"] = flags.sum(axis=1)
        g["agreement"] = np.where(
            flags.all(axis=1), "all significant", np.where(~flags.any(axis=1), "none significant", "methods disagree")
        )
        g.loc[g[sig].isna().any(axis=1), "agreement"] = "incomplete inference"
        out["comparison_terms"] = g
    if "contrasts" in t and "artc_contrasts" in t:
        c = t["contrasts"][
            ["outcome", "family", "label", "scale", "estimate", "ci_low", "ci_high", "p_primary_holm", "p_method"]
        ]
        a = t["artc_contrasts"][["outcome", "family", "label", "estimate_rank_scale", "p", "p_holm"]].rename(
            columns=dict(p="artc_p", p_holm="artc_p_holm")
        )
        m = c.merge(a, on=["outcome", "family", "label"], how="left")
        ratio = m.scale.str.startswith("ratio")
        glm_dir = np.sign(np.where(ratio, np.log(m.estimate.clip(lower=1e-300)), m.estimate))
        m["direction_agrees"] = glm_dir == np.sign(m.estimate_rank_scale)
        gs, as_ = m.p_primary_holm < alpha, m.artc_p_holm < alpha
        m["agreement"] = np.where(
            gs & as_ & m.direction_agrees,
            "all significant",
            np.where(
                ~gs & ~as_,
                "none significant",
                np.where(gs & as_, "both significant, OPPOSITE direction", "methods disagree"),
            ),
        )
        m.loc[m[["p_primary_holm", "artc_p_holm"]].isna().any(axis=1), "agreement"] = "incomplete inference"
        out["comparison_contrasts"] = m
    if "comparison_terms" in out or "comparison_contrasts" in out:
        rows = []
        for key, tab in (("terms", out.get("comparison_terms")), ("contrasts", out.get("comparison_contrasts"))):
            if tab is None:
                continue
            for oc, g in tab.groupby("outcome"):
                vc = g.agreement.value_counts()
                rows.append(dict(outcome=oc, level=key, n=len(g), **{k: int(v) for k, v in vc.items()}))
        out["comparison_summary"] = pd.DataFrame(rows).fillna(0)
    return out
