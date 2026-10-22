"""Write tables, diagnostic plots and a plain-language markdown report."""

from __future__ import annotations

import json
import os
import re

import numpy as np
import pandas as pd

from .diagnostics import diagnostic_plot

TABLE_ORDER = [
    ("validation", "Data-validation report"),
    ("factor_registry", "Factor registry"),
    ("outcome_profile", "Outcome-distribution report"),
    ("model_candidates", "Model-selection report (all candidates)"),
    ("diagnostics", "Diagnostics of selected models"),
    ("diagnostics_ppc", "Observed vs simulated summaries"),
    ("diagnostics_by_cell", "Residuals by factorial cell"),
    ("coefficients", "Sum-coded coefficients (model and HC3 SE)"),
    ("cell_means", "Predicted means for the complete factorial"),
    ("marginal_means", "Marginal means"),
    ("contrasts", "Marginal effects and prespecified contrasts"),
    ("link_interactions", "Link-scale (log-odds) interaction contrasts"),
    ("tests", "Primary p-values and Holm-adjusted p-values"),
    ("art_terms", "ART omnibus tests"),
    ("artc_contrasts", "ART-C contrasts"),
    ("permutation_tests", "Factorial permutation tests"),
    ("effect_stability", "Effect stability across candidate models"),
    ("comparison_terms", "Comparison of conclusions: factorial terms"),
    ("comparison_contrasts", "Comparison of conclusions: contrasts"),
    ("comparison_summary", "Comparison summary"),
    ("errors", "Outcomes that failed"),
]


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


def save_results(res, output_dir: str, plots: bool = True) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    written = {}
    tabs = dict(res.tables)
    tabs["validation"] = res.validation
    if res.factor_registry is not None:
        tabs["factor_registry"] = res.factor_registry
    for key, _ in TABLE_ORDER:
        if key in tabs and tabs[key] is not None and len(tabs[key]):
            p = os.path.join(output_dir, f"{key}.csv")
            tabs[key].to_csv(p, index=False)
            written[key] = p
    if plots:
        pdir = os.path.join(output_dir, "diagnostic_plots")
        os.makedirs(pdir, exist_ok=True)
        for name, o in res.outcomes.items():
            if o.error or not o.diagnostics:
                continue
            p = os.path.join(pdir, f"{_slug(name)}_{o.family}.png")
            diagnostic_plot(p, f"{name}: {o.family}", o.model.y, o.diagnostics, o.cell_index, o.cell_labels)
            written[f"plot:{name}"] = p
    md = os.path.join(output_dir, "report.md")
    with open(md, "w") as fh:
        fh.write(markdown_report(res))
    written["report"] = md
    meta = dict(
        ok=res.ok,
        runtime_s=res.runtime_s,
        n_boot=res.config.n_boot,
        n_boot_lrt=res.config.n_boot_lrt,
        n_perm=res.config.n_perm,
        seed=res.config.seed,
        ci_level=res.config.ci_level,
        alpha=res.config.alpha,
        outcomes={k: (v.family if not v.error else "ERROR") for k, v in res.outcomes.items()},
        files=written,
    )
    with open(os.path.join(output_dir, "summary.json"), "w") as fh:
        json.dump(meta, fh, indent=2, default=str)
    return written


def _fmt_p(p):
    if p is None or not np.isfinite(p):
        return "NA"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _fmt(x):
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x:.3g}"


def markdown_report(res) -> str:
    cfg = res.config
    L = ["# ASAREE statistical analysis report", ""]
    L.append(
        f"Runtime {res.runtime_s:.0f}s. Parametric bootstrap B={cfg.n_boot} (intervals), "
        f"null-model bootstrap B={cfg.n_boot_lrt} (LR p-values), permutations B={cfg.n_perm}, "
        f"seed={cfg.seed}, alpha={cfg.alpha} after Holm adjustment within each prespecified family."
    )
    if cfg.n_boot < 1000 or (0 < cfg.n_boot_lrt < 999) or cfg.n_perm < 1999:
        L.append("")
        L.append(
            "**Smoke-test resolution:** B is below the recommended values, so p-values have a floor "
            f"(bootstrap LRT {1 / (cfg.n_boot_lrt + 1):.3f}, permutation {1 / (cfg.n_perm + 1):.4f}) and "
            "method disagreements below are mostly resolution artefacts. Do not interpret."
        )
    L.append("")
    L.append("## Data validation")
    for r in res.validation.itertuples():
        L.append(f"- **{r.severity}** {r.check}: {r.detail}")
    if not res.ok:
        L.append("\nAnalysis stopped: fix the errors above.")
        return "\n".join(L)
    t = res.tables
    for name, o in res.outcomes.items():
        L += ["", f"## Outcome: {name}"]
        if o.error:
            L.append(f"Analysis failed: `{o.error.splitlines()[0]}`")
            continue
        prof = o.tables["outcome_profile"].iloc[0]
        L.append(f"**Model:** {o.family}. {prof.selection_rationale}.")
        if "art_terms" in o.tables and "working_scale" in o.tables["art_terms"]:
            L.append(
                f"\n*Sensitivity-analysis scale:* {o.tables['art_terms'].working_scale.iloc[0]} "
                "(interactions are scale dependent; ART and permutation tests use the scale on which the "
                "GLM effects are defined)."
            )
        if prof.notes:
            L.append(f"\n*Notes:* {prof.notes}")
        flags = o.diagnostics.get("flags", [])
        L.append("\n**Diagnostics:** " + ("no flags." if not flags else "; ".join(flags) + "."))
        q = o.tables["quantities"]
        if q.n_boot_failed.iloc[0]:
            L.append(f"\nBootstrap: {q.n_boot_failed.iloc[0]} of {cfg.n_boot} refits failed and were dropped.")
        tests = o.tables["tests"]
        terms = tests[tests.family == "terms"]
        L.append("\n| term | primary p | Holm p | method | ART Holm p | permutation Holm p |")
        L.append("|---|---|---|---|---|---|")
        art = o.tables.get("art_terms", pd.DataFrame(columns=["term", "p_holm"])).set_index("term")
        perm = o.tables["permutation_tests"].set_index("term")
        for r in terms.itertuples():
            ap = art.p_holm.get(r.hypothesis, np.nan) if len(art) else np.nan
            pp = perm.p_holm.get(r.hypothesis, np.nan)
            meth = "boot LRT" if "bootstrap LRT (null" in r.p_method else "HC3 Wald"
            L.append(
                f"| {r.hypothesis} | {_fmt_p(r.p_primary)} | {_fmt_p(r.p_primary_holm)} | {meth} | "
                f"{_fmt_p(ap)} | {_fmt_p(pp)} |"
            )
        con = o.tables["contrasts"]
        sig = con[con.p_primary_holm < cfg.alpha]
        L.append(f"\n**Contrasts significant after Holm ({len(sig)} of {len(con)}):**")
        for r in sig.head(25).itertuples():
            L.append(
                f"- [{r.family}] {r.label}: {r.scale} = {_fmt(r.estimate)} "
                f"({int(cfg.ci_level * 100)}% CI {_fmt(r.ci_low)} to {_fmt(r.ci_high)}), "
                f"Holm p={_fmt_p(r.p_primary_holm)}"
            )
        if len(sig) > 25:
            L.append(f"- ... {len(sig) - 25} more in contrasts.csv")
        stab = o.tables.get("effect_stability")
        if stab is not None and len(stab):
            worst = stab.standardized_difference.abs().max()
            L.append(
                f"\n**Effect stability:** largest standardized difference between the selected model and an "
                f"alternative candidate = {_fmt(worst)} bootstrap SEs."
            )
    if "comparison_summary" in t:
        L += ["", "## Comparison of conclusions across methods", ""]
        L.append(
            t["comparison_summary"].to_markdown(index=False)
            if _has_tabulate()
            else t["comparison_summary"].to_string(index=False)
        )
        if "comparison_terms" in t:
            dis = t["comparison_terms"][t["comparison_terms"].agreement == "methods disagree"]
            for r in dis.itertuples():
                L.append(
                    f"- {r.outcome} / {r.term}: GLM Holm p={_fmt_p(r.glm_p_holm)}, "
                    f"ART Holm p={_fmt_p(getattr(r, 'art_p_holm', np.nan))}, "
                    f"permutation Holm p={_fmt_p(getattr(r, 'perm_p_holm', np.nan))}"
                )
    L += ["", "## Methods (for the manuscript)", "", METHODS_TEXT]
    return "\n".join(L)


def _has_tabulate():
    try:
        import tabulate  # noqa: F401

        return True
    except ImportError:
        return False


METHODS_TEXT = """\
Each outcome was modelled with a generalized linear (or two-part / mixture) model whose response
distribution was chosen from the metric's construction (support, boundedness, count or proportion
structure) and, among the candidates this implied, by AIC after excluding fits with convergence
failures or severe simulation-based diagnostic failures. Categorical factors were sum-to-zero coded and
the complete prespecified factorial was fitted; train/test splits or datasets entered as fixed blocking
factors. Cell means were averaged with equal weights over blocks, and marginal means and contrasts were
equally weighted averages of cell means on the response scale (differences for identity/logit-link
outcomes, ratios and ratios of ratios for log-link outcomes). Uncertainty was quantified with a
parametric bootstrap that simulated new outcomes from the fitted model on the fixed design and refitted
it, using percentile intervals (dispersion parameters for simulation were inflated by n/(n - p), the
degrees-of-freedom correction, unless disabled). Primary p-values were parametric-bootstrap
likelihood-ratio tests in which
data were simulated from the fitted null model (Type III term deletion, or the link-scale linear
contrast constrained to zero); where the time budget did not allow this, HC3 sandwich Wald tests were
used, and the method is reported per family. Holm's procedure was applied within each prespecified
family. Sensitivity analyses used the aligned rank transform (ART) for factorial terms, ART-C for
contrasts, and design-preserving permutation tests (labels permuted within strata of the other factors
for main effects; Freedman-Lane residual permutation for interactions), each with Holm adjustment.
Inference is conditional on the observed splits."""
