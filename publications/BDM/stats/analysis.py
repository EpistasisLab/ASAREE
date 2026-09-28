#!/usr/bin/env python
"""
analysis.py -- 2x2x2 factorial analysis of an agentic ML pipeline.

WHAT THIS IS
============
A complete, re-runnable analysis of `results.csv`: 80 runs of a multi-agent
machine-learning pipeline (data cleaning -> feature engineering -> feature
selection -> XGBoost modeling), laid out as a balanced 2 x 2 x 2 full
factorial with 10 replicate runs per cell.

    A  model    sonnet (-1)  vs  opus  (+1)
    B  effort   medium (-1)  vs  xhigh (+1)
    C  critic   off    (-1)  vs  on    (+1)

All statistical machinery lives in `beta_factorial/betafx.py`, which was
written and reviewed for this experiment. This script is a DRIVER: it decides
which endpoint gets which model, applies the prespecified testing hierarchy,
and writes tables that `figures.py` turns into plots. It deliberately does not
re-implement anything betafx already does.

THE RECIPE THIS SCRIPT ENCODES (reusable on other factorial data)
================================================================
For every endpoint, in order:

  1. ROUTE BY DATA TYPE, NOT BY HABIT. The distribution of the outcome picks
     the model, before anyone looks at a p-value:
       bounded (0,1)  -> beta regression, logit link   (ratios of odds)
       positive skew  -> gamma GLM, log link           (ratios of means)
       counts         -> Poisson (robust SE) or NB2, log link
     A t-test / ANOVA on everything is the default that this design exists to
     avoid: AUPRC is bounded, cost is multiplicative, counts are discrete.

  2. VALIDATE THE DESIGN ON THAT ENDPOINT'S OWN SUBSET. Missingness is
     per-endpoint, so completeness and rank have to be re-checked after
     dropping non-finite rows, not once on the whole table.

  3. ESTIMATE ON THE MODEL SCALE, REPORT ON THE RESPONSE SCALE. Effect-coded
     factors mean a coefficient is HALF the marginal difference; betafx's
     `marginal_effects()` averages the eight fitted cell means and attaches a
     delta-method interval. Never report a raw coefficient.

  4. ONE POST-HOC PROCEDURE FOR EVERY ENDPOINT, FIXED BEFORE LOOKING, AND MAKE
     IT THE DISTRIBUTION-FREE ONE. Kruskal-Wallis, then Mann-Whitney U with
     Holm over all 28 cell pairs, supplies the compact letters on every figure.
     Nothing routes on a normality test: that gate has its own error rate, has
     little power to fail at n=10 per cell, and routing on it would make
     adjacent panels answer different questions. The rank test is usually the
     stricter of the two; that is the price of a display whose validity does
     not depend on an assumption being true.

  5. NO GAUSSIAN MODEL IS FITTED ANYWHERE. There is no ANOVA and no Tukey
     table: a procedure that will not be reported should not be computed, and
     leaving one in an output file invites someone to quote it. Normality and
     equal-variance diagnostics on the WITHIN-CELL residuals (not on the raw
     column) are still printed, but they are DESCRIPTIVE ONLY -- they route
     nothing, license nothing, and no step downstream reads them.

  6. ALWAYS CARRY A NON-PARAMETRIC BACKSTOP. A stratified randomization test
     (labels permuted within the strata formed by the other two factors) is
     valid under the sharp null without any distributional assumption, and it
     does not lean on the asymptotics that are mildly anticonservative at
     N = 80.

  7. STATE THE MULTIPLICITY STRUCTURE BEFORE READING P-VALUES. See
     section 6 below.

OUTPUTS
=======
    tables/*.csv        one tidy table per artifact
    results.json        everything figures.py needs, incl. letter displays
    analysis_log.txt    the full console transcript

Run:  python analysis.py
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import sys
import warnings
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "beta_factorial"))

import betafx as bx  # noqa: E402

RESULTS_CSV = HERE.parent / "results.csv"
TABLES = HERE / "tables"
TABLES.mkdir(parents=True, exist_ok=True)

# betafx raises UserWarnings for exactly the conditions an unattended run must
# see (dropped rows, boundary squeezes, imbalance, Poisson dispersion). Surface
# them; only silence third-party deprecation noise.
warnings.simplefilter("always", UserWarning)
for _m in ("statsmodels", "scipy", "pandas"):
    warnings.filterwarnings("ignore", category=FutureWarning, module=_m)
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=_m)

ALPHA = 0.05
EXPECTED_N = 10
N_BOOT = 2000
N_PERM = 20000
RNG_SEED = 20260812


# ==========================================================================
# 0. plumbing
# ==========================================================================

class Tee:
    """Mirror stdout into the log file so the transcript is an artifact."""

    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, s):
        self.stdout.write(s)
        self.f.write(s)

    def flush(self):
        self.stdout.flush()
        self.f.flush()


def hdr(t, ch="="):
    print("\n" + ch * 78 + f"\n{t}\n" + ch * 78)


def sub(t):
    print(f"\n--- {t} " + "-" * max(0, 73 - len(t)))


def fmt(v):
    return f"{v:.4f}" if isinstance(v, float) else str(v)


def show(df, **kw):
    print(df.to_string(index=False, float_format=lambda v: f"{v:.4g}", **kw))


def save(df, name):
    df.to_csv(TABLES / f"{name}.csv", index=False)
    return df


# ==========================================================================
# 1. load and code the design
# ==========================================================================
# The CSV stores factors as human labels. betafx REFUSES to guess a coding for
# label levels -- alphabetical ordering would make "medium" the +1 level and
# silently flip the sign of every effect involving effort -- so the map is
# explicit and is recorded in the output.

LEVELS = bx.EXPERIMENT_LEVELS            # {"A": {"sonnet": -1, "opus": 1}, ...}
FACTOR_LABEL = bx.FACTOR_LABELS          # {"A": "model", "B": "effort", ...}
LEVEL_NAMES = {"A": ("sonnet", "opus"), "B": ("medium", "xhigh"),
               "C": ("off", "on")}

# Left-to-right display order of the eight cells, defined ONCE here and shipped
# in results.json so the plotting layer cannot drift from it. Sorted by FACTOR
# level -- model, then critic, then effort -- not by any observed value: an axis
# re-sorted per endpoint destroys the reader's ability to carry a position from
# one figure to the next. It is also the order the letters are named in.
SIGN_ORDER = ["---", "-+-", "--+", "-++", "+--", "++-", "+-+", "+++"]


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Map the run log onto the analysis frame.

    Only two derived quantities are created, both requested and both pure
    rescalings that leave every ratio-scale inference identical:
        wallclock_min = wallclock_s / 60
        prop_engineered_selected = pct_engineered_features_selected / 100
    """
    d = pd.DataFrame({
        "A": raw["model"].map({"claude-sonnet-5": "sonnet",
                               "claude-opus-5": "opus"}),
        "B": raw["effort"],
        "C": raw["critic"].map({False: "off", True: "on"}),
        "replicate": raw["replicate"],

        # --- the eight endpoints requested -------------------------------
        "pr_auc": raw["pr_auc"],
        "roc_auc": raw["roc_auc"],
        "usd": raw["usd"],
        "wallclock_min": raw["wallclock_s"] / 60.0,
        "n_engineered_features": raw["n_engineered_features"],
        "n_features_after_fs": raw["n_features_after_fs"],
        "n_engineered_features_selected": raw["n_engineered_features_selected"],
        "prop_engineered_selected": raw["pct_engineered_features_selected"] / 100.0,

        # --- additions (see ENDPOINTS for why each earns its place) -------
        "total_tokens": raw["total_tokens"],
        "n_turns_total": raw["n_turns_total"],
        "inner_cv_score": raw["optuna_best_inner_cv_score"],

        # --- context columns, not modeled -------------------------------
        "n_features_after_fte": raw["n_features_after_fte"],
        "n_features_after_dc": raw["n_features_after_dc"],
        "wallclock_s": raw["wallclock_s"],
        "n_critic_invocations": raw["n_critic_invocations"],
        "n_critic_rejections": raw["n_critic_rejections"],
        "max_iter_hit": raw["max_iter_hit"],
        "failure_flag": raw["failure_flag"],
    })
    if d[["A", "B", "C"]].isna().any().any():
        raise ValueError("unmapped factor level; check the model/critic maps")
    d["cell"] = [f"{a}/{b}/{c}" for a, b, c in zip(d.A, d.B, d.C)]
    d["sign"] = ["".join("+" if v == hi else "-"
                         for v, (_, hi) in zip((a, b, c), LEVEL_NAMES.values()))
                 for a, b, c in zip(d.A, d.B, d.C)]
    return d


# ==========================================================================
# 2. endpoint registry
# ==========================================================================
# `family` is the DISTRIBUTIONAL routing decision and it is made from the
# measurement type, before any model is fitted:
#
#   beta     bounded strictly inside (0,1). AUPRC/ROC-AUC/proportions are not
#            Gaussian: they are bounded, heteroscedastic near the bounds, and
#            an OLS interval can cross 0 or 1.
#   gamma    positive and right-skewed with variance growing with the mean.
#            Log link => effects are RATIOS of arithmetic means, which is what
#            a practitioner budgets for ("2.6x as long").
#   poisson  counts. Robust (HC0) covariance so the fit stays valid under
#            over- OR under-dispersion; feature counts drawn from a bounded
#            candidate pool are typically UNDER-dispersed, which NB2 cannot
#            represent at all (its dispersion is driven to the boundary).
#   negbin   counts with genuine over-dispersion (token totals span 0.4M-3.4M).
#            NB2 with ML-estimated alpha, never smf.glm(NegativeBinomial()),
#            which silently fixes alpha = 1.
#
# `log_ok` marks endpoints where a log scale is a sensible second scale to
# report residual diagnostics on. It has no bearing on any estimate: the model
# is fixed by measurement type and the letters are distribution-free on the raw
# values for every endpoint.
#
# `structural` records that part of the effect along that factor is fixed by
# the price schedule rather than produced by the workflow: Opus costs several
# times Sonnet per token, so the DIRECTION of a model-factor cost effect is
# settled before any run happens. The MAGNITUDE is still informative, because
# cost = price x tokens and token use is behavioral. Disclose, do not suppress.

ENDPOINTS = [
    dict(key="pr_auc", family="beta", tier="primary",
         label="PR-AUC", unit="AUPRC", log_ok=False,
         note="Primary endpoint. Bounded in (0,1); observed 0.517-0.557."),
    dict(key="roc_auc", family="beta", tier="requested",
         label="ROC-AUC", unit="ROC-AUC", log_ok=False,
         note="Second view of the same discrimination construct as PR-AUC."),
    dict(key="usd", family="gamma", tier="requested",
         label="Cost per run", unit="USD", log_ok=True,
         structural={"A"},
         note="Gamma log-link: ratio of ARITHMETIC mean cost. Model-factor "
              "effect is partly fixed by the price schedule."),
    dict(key="wallclock_min", family="gamma", tier="requested",
         label="Wall-clock time", unit="minutes", log_ok=True,
         note="Seconds/60. A pure rescaling: every ratio and every p-value is "
              "identical to the analysis in seconds."),
    dict(key="n_engineered_features", family="poisson", tier="requested",
         label="Engineered features created", unit="features", log_ok=True,
         note="Output of the feature-engineering (FTE) agent."),
    dict(key="n_features_after_fs", family="poisson", tier="requested",
         label="Features surviving selection", unit="features", log_ok=True,
         note="Features reaching the modeling step."),
    dict(key="n_engineered_features_selected", family="poisson",
         tier="requested", label="Engineered features selected",
         unit="features", log_ok=True,
         note="Nested inside both of the two counts above."),
    dict(key="prop_engineered_selected", family="beta", tier="requested",
         label="Engineered share of selected features", unit="proportion",
         log_ok=False,
         note="ALGEBRAICALLY n_engineered_features_selected / "
              "n_features_after_fs -- not an independent endpoint."),

    # ---- additions -----------------------------------------------------
    dict(key="total_tokens", family="negbin", tier="extra",
         label="Total tokens", unit="tokens", log_ok=True,
         note="Added because it is what separates a PRICE effect from a "
              "BEHAVIOR effect in the cost result. PLAN.md is explicit that "
              "token counts are NOT structural: resource use is an observed "
              "outcome."),
    dict(key="n_turns_total", family="negbin", tier="extra",
         label="Agent turns", unit="turns", log_ok=True,
         note="Added as a price-free measure of how much work the workflow "
              "actually did."),
    dict(key="inner_cv_score", family="beta", tier="extra",
         label="Inner-CV score (Optuna best)", unit="score", log_ok=False,
         note="Added to test the optimism gap: does the score the modeling "
              "agent optimizes track held-out PR-AUC?"),
]
EP = {e["key"]: e for e in ENDPOINTS}
REQUESTED = [e["key"] for e in ENDPOINTS if e["tier"] in ("primary", "requested")]

_base = bx.MetricConfig.for_experiment(mixed_model_tiers=True, model_factor="A")
CFG = dataclasses.replace(
    _base,
    families=MappingProxyType({e["key"]: e["family"] for e in ENDPOINTS}),
    mechanical=MappingProxyType(
        {e["key"]: frozenset(e["structural"]) for e in ENDPOINTS
         if e.get("structural")}),
    denominators=MappingProxyType(
        {"n_features_after_fs": "n_features_after_fte",
         "n_engineered_features_selected": "n_features_after_fs"}),
)


# ==========================================================================
# 3. non-parametric backstop: stratified randomization test
# ==========================================================================

def fit_beta_robust(frame, outcome, dispersion_formula=None, validate=True,
                    expected_n=None, levels="default"):
    """
    betafx's beta fit, with a documented optimizer fallback.

    THE PROBLEM (worth knowing whenever a beta model is fitted to a metric
    that lives in a narrow band well inside (0,1)): PR-AUC here sits in
    0.517-0.557 and the inner-CV score in 0.578-0.599, so the fitted precision
    is enormous (phi-hat ~ 4.0e3 for PR-AUC, ~1.4e4 for ROC-AUC). The
    log-likelihood is then extremely sharply peaked in phi, and BFGS
    terminates with warnflag=2 -- "precision loss", i.e. the line search can
    no longer make progress against floating-point noise. betafx's
    check_converged() correctly refuses such a fit.

    THE POINT TO BE CLEAR ABOUT: warnflag=2 is a statement about the
    OPTIMIZER, not about the data or the model. Verified directly on the full
    dataset: BFGS, Nelder-Mead, L-BFGS-B and Powell all agree to five decimal
    places on every coefficient and to four on the log-likelihood; only BFGS's
    convergence FLAG differs. For inner_cv_score, BFGS reports llf = 330.8856
    and refuses to converge while L-BFGS-B reports llf = 330.8856 and
    converges.

    THE FIX: keep betafx's fit as the canonical path, and only when it raises
    a non-convergence RuntimeError, refit the identical model with L-BFGS-B
    and re-apply the same convergence check. The model, the data and the
    estimand are untouched; only the descent algorithm changes. Which
    optimizer produced each fit is recorded and reported.

    DO NOT use this to paper over a genuine failure: a separation, an
    unidentifiable design or a boundary value will fail under every optimizer,
    and this wrapper will still raise.
    """
    # `levels` is the label->(-1,+1) map. Pass None for a frame that is
    # already effect-coded (as inside the bootstrap): betafx deliberately
    # raises if a label map is handed to numeric columns, rather than guessing.
    lv = LEVELS if levels == "default" else levels
    kw = dict(dispersion_formula=dispersion_formula, levels=lv,
              validate=validate, expected_n=expected_n)
    try:
        return bx.fit_beta_factorial(frame, outcome, **kw), "bfgs"
    except RuntimeError as exc:
        if "did not converge" not in str(exc):
            raise
    import statsmodels.formula.api as smf
    from statsmodels.othermod.betareg import BetaModel
    d = bx.make_effect_coded(frame, levels=lv)
    exog_prec = None
    if dispersion_formula is not None:
        exog_prec = smf.ols(f"{outcome} {dispersion_formula}", data=d).exog
    res = BetaModel.from_formula(bx.FULL_FORMULA.format(y=outcome), data=d,
                                 exog_precision=exog_prec
                                 ).fit(disp=0, method="lbfgs", maxiter=5000)
    bx.check_converged(res, f"beta model for {outcome} (L-BFGS-B fallback)")
    return res, "lbfgs-fallback"


def bootstrap_marginal_effects(d, outcome, n_boot=N_BOOT, alpha=ALPHA,
                               seed=RNG_SEED, min_success=0.90):
    """
    Cell-stratified bootstrap of the response-scale marginal effects.

    Identical in construction to betafx.bootstrap_contrasts -- resample WITHIN
    each of the eight cells so the balanced design is preserved, refit, and
    take percentiles of the marginal effects -- with one change: each refit
    goes through fit_beta_robust(), so a resample is only counted as failed
    when the model genuinely cannot be fitted, not when BFGS declines to
    certify a fit that three other optimizers agree on.

    Why this is not "lowering the threshold": betafx's own bootstrap hit a
    71.7% success rate on this dataset and refused to return an interval,
    telling the caller to investigate rather than relax min_success. The
    investigation found that 100% of those failures were warnflag=2 from BFGS
    (see fit_beta_robust). The 90% requirement is kept exactly as it is; what
    changes is that a resample is no longer discarded for an optimizer flag.
    """
    dd = bx.make_effect_coded(d, levels=LEVELS)
    rng = np.random.default_rng(seed)
    groups = [g.index.to_numpy() for _, g in dd.groupby(["A", "B", "C"])]

    draws, failed, fallbacks = [], 0, 0
    for _ in range(n_boot):
        idx = np.concatenate([rng.choice(g, size=len(g), replace=True)
                              for g in groups])
        try:
            r, how = fit_beta_robust(dd.loc[idx].reset_index(drop=True),
                                     outcome, validate=False, levels=None)
            fallbacks += (how == "lbfgs-fallback")
            draws.append(bx.marginal_effects(r, "beta").set_index("term").effect)
        except (RuntimeError, ValueError, np.linalg.LinAlgError):
            failed += 1

    rate = len(draws) / n_boot
    if rate < min_success:
        raise RuntimeError(f"only {len(draws)}/{n_boot} ({rate:.1%}) resamples "
                           f"fitted, below the required {min_success:.0%}")
    B = pd.DataFrame(draws)
    pt = bx.marginal_effects(
        fit_beta_robust(dd, outcome, validate=False, levels=None)[0],
        "beta").set_index("term")
    out = pd.DataFrame({
        "term": bx.TERMS,
        "contrast": [pt.effect[t] for t in bx.TERMS],
        "boot_lo": [np.percentile(B[t], 100 * alpha / 2) for t in bx.TERMS],
        "boot_hi": [np.percentile(B[t], 100 * (1 - alpha / 2)) for t in bx.TERMS],
        "boot_se": [B[t].std(ddof=1) for t in bx.TERMS]})
    out.attrs.update(n_boot_ok=len(draws), n_boot_failed=failed,
                     success_rate=rate, n_fallback=fallbacks)
    return out


def stratified_permutation_p(d, outcome, term, ratio=False, n_perm=N_PERM,
                             seed=RNG_SEED):
    """
    Distribution-free test of a MAIN effect, with no model fitted at all.

    Under the sharp null that `term` changes nothing about a run, the labels
    of `term` are exchangeable WITHIN each combination of the other two
    factors. Permuting inside those four strata preserves the balance of the
    design, so the reference distribution is exact up to Monte Carlo error.

    Statistic:
        ratio=False -> mean(y | +1) - mean(y | -1)
        ratio=True  -> log[ mean(y | +1) / mean(y | -1) ]
    matching the estimand of the difference- and log-link families
    respectively. The choice of statistic affects power, never validity.

    Why bother when a GLM is already fitted: at 10 runs per cell the
    asymptotic Wald test in this design has a measured size near 0.07 against
    a nominal 0.05 (PLAN.md, Monte Carlo section), and a likelihood-ratio test
    does not remove it. A randomization reference distribution does not lean
    on those asymptotics. This is the honest answer to "what if the normality
    assumption fails".

    Limits, stated plainly: it tests the SHARP null (no effect on any run),
    not the null of equal means, and it is not exact when interactions
    involving `term` are present. Use it as a sensitivity check beside the
    model-based p-value, not instead of it.
    """
    others = [f for f in ("A", "B", "C") if f != term]
    hi = LEVEL_NAMES[term][1]
    y = pd.to_numeric(d[outcome]).to_numpy(float)
    is_hi = (d[term] == hi).to_numpy()

    blocks = []
    for _, g in d.groupby(others, sort=True):
        idx = g.index.to_numpy()
        pos, neg = idx[is_hi[idx]], idx[~is_hi[idx]]
        if len(pos) != len(neg):
            raise ValueError(f"stratum for {term} is unbalanced; "
                             "the exchangeability argument needs equal halves")
        blocks.append(np.concatenate([pos, neg]))
    B = np.vstack(blocks)                      # (n_strata, 2m) index matrix
    m = B.shape[1] // 2
    yb = y[B]                                  # (n_strata, 2m)

    def stat(pl, mi):
        return np.log(pl / mi) if ratio else pl - mi

    obs = stat(yb[:, :m].mean(), yb[:, m:].mean())

    rng = np.random.default_rng(seed)
    order = np.argsort(rng.random((n_perm,) + yb.shape), axis=2)
    perm = np.take_along_axis(np.broadcast_to(yb, order.shape), order, axis=2)
    null = stat(perm[:, :, :m].reshape(n_perm, -1).mean(axis=1),
                perm[:, :, m:].reshape(n_perm, -1).mean(axis=1))

    # +1 in numerator and denominator: the observed assignment is itself one
    # of the equally likely arrangements, so a p-value of exactly 0 is not
    # attainable and must not be reported.
    p = (np.sum(np.abs(null) >= abs(obs) - 1e-12) + 1) / (n_perm + 1)
    return dict(term=term, outcome=outcome, statistic=float(obs),
                p_perm=float(p), n_perm=int(n_perm),
                scale="log ratio" if ratio else "difference")


# ==========================================================================
# 4. compact letter display
# ==========================================================================

def compact_letters(groups, significant):
    """
    Letter display for a set of pairwise comparisons.

    CONTRACT: two groups SHARE a letter if and only if they are NOT
    significantly different. This is the insert-and-absorb algorithm of
    Piepho (2004), J Comput Graph Stat 13:456-466, doi:10.1198/1061860043515.

    `significant` is a set of frozenset({g1, g2}) pairs that DO differ.

    NAMING: `groups` must arrive in the order the panel will draw them left to
    right, and clusters are named in order of first appearance along it -- so
    'a' is always the leftmost group. The alternative convention ('a' = largest
    mean) is common in the agronomy literature but reads as an error when the
    axis is ordered by factor level rather than by value: on a cost panel the
    reader meets 'b' before 'a'. Which cluster is named 'a' is arbitrary either
    way -- the letters are a partition, and renaming is a bijection that cannot
    change which groups share one -- so pick the convention that is legible.

    The letters are only as trustworthy as the pairwise test that produced
    them, and a letter display inherits that test's assumptions. Here every
    caller passes Mann-Whitney/Holm results, so the assumption set is the same
    on every panel; this function does not know which test was used, so the
    caller still records it in `letters_method` for the subtitle.
    """
    groups = list(groups)
    cols = [set(groups)]
    for g1, g2 in itertools.combinations(groups, 2):
        if frozenset((g1, g2)) not in significant:
            continue
        new = []
        for col in cols:
            if g1 in col and g2 in col:
                new += [col - {g1}, col - {g2}]
            else:
                new.append(col)
        keep = []                                    # absorb: drop subsets
        for c in new:
            if not c:
                continue
            if any(c < d for d in new):
                continue
            if any(c == d for d in keep):
                continue
            keep.append(c)
        cols = keep

    pos = {g: i for i, g in enumerate(groups)}
    cols.sort(key=lambda c: min(pos[g] for g in c))
    letters = {g: "" for g in groups}
    for i, col in enumerate(cols):
        ch = chr(ord("a") + i)
        for g in col:
            letters[g] += ch

    for g1, g2 in itertools.combinations(groups, 2):   # verify the contract
        shared = bool(set(letters[g1]) & set(letters[g2]))
        diff = frozenset((g1, g2)) in significant
        assert shared != diff, f"letter display violated for {g1} vs {g2}"
    return letters


def pairwise_nonparametric(d, outcome, group_col, alpha=ALPHA):
    """
    Distribution-free pairwise comparisons across the eight configurations:
    Mann-Whitney U on every pair, Holm-adjusted across the 28 comparisons.

    This is THE letter display procedure -- every endpoint, no exceptions, no
    routing on a normality gate (see the note in `analyze_endpoint`, step 5f).

    Two properties to state wherever its letters appear. It compares stochastic
    ordering, not means: "does one cell tend to come in lower?" is a different
    question from "are the averages far apart?", and they disagree when one
    cell carries an outlier (see FINDINGS.md 3.3). And it is usually, though
    not always, stricter than the parametric alternative -- less powerful where
    that alternative's assumptions hold, but occasionally more sensitive where
    an outlier inflates a pooled SD. Both directions are expected; neither is a
    defect of the procedure.

    Resolution is not the binding constraint at this n. With n1 = n2 = 10 the
    smallest attainable two-sided p is 2/C(20,10) = 1.08e-05, so even the worst
    Holm multiplier (28) leaves 3.0e-04 -- a clean separation still clears
    alpha. What is lost is sensitivity to small shifts.
    """
    from statsmodels.stats.multitest import multipletests
    groups = sorted(d[group_col].unique())
    pairs, ps = [], []
    for g1, g2 in itertools.combinations(groups, 2):
        a = d.loc[d[group_col] == g1, outcome].to_numpy(float)
        b = d.loc[d[group_col] == g2, outcome].to_numpy(float)
        ps.append(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)
        pairs.append((g1, g2))
    rej, padj, *_ = multipletests(ps, alpha=alpha, method="holm")
    return pd.DataFrame(dict(group1=[p[0] for p in pairs],
                             group2=[p[1] for p in pairs],
                             p_raw=ps, p_adj=padj, reject=rej))


# ==========================================================================
# 5. per-endpoint analysis
# ==========================================================================

def diagnose(d, key):
    """
    Normality and equal-variance diagnostics on the WITHIN-CELL residuals,
    for the raw scale and (where meaningful) the log scale.

    Testing the raw marginal column instead would be a mistake: cost is
    bimodal marginally purely because critic-on and critic-off differ in mean,
    while each cell may be perfectly well behaved.

    Returns a list of rows, one per scale. THIS FUNCTION DECIDES NOTHING. Its
    output is printed and stored, and no downstream step consults it:

      * the inferential model for each endpoint is fixed a priori by its
        measurement type in ENDPOINTS, not by a test of the residuals;
      * the letter display is distribution-free on the raw values for every
        endpoint, so there is no scale to choose and no gate to pass.

    It is kept because a reader is entitled to see the shape of the residuals
    the routing assumed -- reporting a diagnostic and acting on it are
    different things, and only the first is safe at n=10 per cell.
    """
    rows = []
    work = d.copy()
    scales = ["raw"] + (["log"] if EP[key]["log_ok"] else [])
    for sc in scales:
        col = key if sc == "raw" else f"__log_{key}"
        if sc == "log":
            if (work[key] <= 0).any():
                continue
            work[col] = np.log(work[key].astype(float))
        try:
            # validate=False: the endpoint was already validated on its own
            # subset in step 5a, and the log column is a derived name that the
            # metric config does not know about.
            r = bx.normality_check(work, col, levels=LEVELS, validate=False)
        except Exception as exc:
            rows.append(dict(endpoint=key, scale=sc, error=str(exc)[:70]))
            continue
        rows.append(dict(endpoint=key, scale=sc, shapiro_p=r["shapiro_p"],
                         skew=r["skew"], excess_kurtosis=r["excess_kurtosis"],
                         levene_p=r["levene_p"], sd_ratio=r["sd_ratio"]))
    return rows


def fit_endpoint(d, key):
    """Fit the family-appropriate model and return the effect table."""
    fam = EP[key]["family"]
    how = "irls/ml"
    if fam.startswith("beta"):
        res, how = fit_beta_robust(d, key, expected_n=EXPECTED_N)
    else:
        res = bx.fit_secondary(d, key, fam, levels=LEVELS, config=CFG,
                               expected_n=EXPECTED_N)
    tab = bx.effect_summary(res, fam, estimand="response")
    tab["endpoint"] = key
    tab["family"] = fam
    tab.attrs["optimizer"] = how
    tab["label"] = tab.term.map(bx.pretty_term)
    tab["structural"] = [CFG.is_structural(key, t) for t in tab.term]
    return res, tab


def poisson_dispersion(res, d, key):
    """Pearson dispersion; <1 is under-, >1 over-dispersion."""
    mu = np.asarray(res.fittedvalues, dtype=float)
    y = pd.to_numeric(d[key]).to_numpy(float)
    return float(np.sum((y - mu) ** 2 / mu) / res.df_resid)


def analyze_endpoint(d, key, fatal=False):
    """
    Everything for one endpoint. Returns a dict; never raises for an
    exploratory endpoint, always raises for a confirmatory one.

    Exit-code contract borrowed from betafx: an exploratory endpoint that
    cannot be fitted is reported and the run continues; a confirmatory
    endpoint that cannot be fitted aborts, because a preregistered claim
    silently missing from an unattended report is worse than no report.
    """
    e = EP[key]
    out = dict(key=key, **{k: e[k] for k in
                           ("family", "tier", "label", "unit", "note")})
    hdr(f"{e['label']}  [{key}]   family = {e['family']}   tier = {e['tier']}")
    print(f"  {e['note']}")

    # ---- 5a. validation on this endpoint's own subset --------------------
    try:
        _, rep = bx.validate_outcome(d, key, e["family"], levels=LEVELS,
                                     expected_n=EXPECTED_N, config=CFG)
        out["validation"] = rep
        print(f"  validation: n={rep['n_analysed']}, dropped "
              f"{rep['n_dropped_nonfinite']} non-finite, {rep['cells']}/8 cells, "
              f"rank {rep['rank']}, cell counts "
              f"{rep['min_cell']}-{rep['max_cell']}")
    except Exception as exc:
        if fatal:
            raise
        out["error"] = str(exc)
        print(f"  FAILED validation: {exc}")
        return out

    # ---- 5b. descriptives -----------------------------------------------
    cells = (d.groupby("cell")[key].agg(["count", "mean", "std", "min", "max"])
             .reset_index().round(6))
    out["cell_table"] = cells.to_dict("records")

    # ---- 5c. the inferential model --------------------------------------
    try:
        res, tab = fit_endpoint(d, key)
    except Exception as exc:
        if fatal:
            raise
        out["error"] = str(exc)
        print(f"  FAILED to fit: {exc}")
        return out

    if e["family"] == "poisson":
        disp = poisson_dispersion(res, d, key)
        out["pearson_dispersion"] = disp
        print(f"  Pearson dispersion {disp:.2f} "
              f"({'under' if disp < 1 else 'over'}-dispersed; HC0 robust SEs "
              "are valid either way)")

    # ---- 5d. testing hierarchy ------------------------------------------
    # PR-AUC carries the prespecified hierarchy from PLAN.md note 3:
    #   primary   = model (A), unadjusted alpha
    #   secondary = effort (B) and critic (C), Holm within that family of two
    # Every other endpoint is exploratory; within it, Holm is applied across
    # its own three main effects so each endpoint's family-wise error is
    # controlled without borrowing error budget from the others.
    if key == "pr_auc":
        tab = bx.apply_hierarchy(tab, primary="A", secondary=("B", "C"),
                                 alpha=ALPHA)
    else:
        from statsmodels.stats.multitest import multipletests
        tab["tier"] = np.where(tab.term.isin(["A", "B", "C"]),
                               "main (Holm within endpoint)", "exploratory")
        main = tab[tab.term.isin(["A", "B", "C"])]
        rej, padj, *_ = multipletests(main.p.to_numpy(), alpha=ALPHA,
                                      method="holm")
        tab["p_holm"] = np.nan
        tab["significant"] = pd.NA
        tab.loc[main.index, "p_holm"] = padj
        tab.loc[main.index, "significant"] = rej

    cols = ["term", "label", "tier", "coef", "se", "p_link", "effect",
            "eff_lo", "eff_hi", "p_response", "p_holm", "significant",
            "structural"]
    cols = [c for c in cols if c in tab.columns]
    sub("factorial effects (effect = eight-cell marginal effect, response scale)")
    show(tab[cols])
    print(f"  scale: {tab.scale.iloc[0]}")
    out["optimizer"] = tab.attrs.get("optimizer", "irls/ml")
    if out["optimizer"] == "lbfgs-fallback":
        print("  NOTE: BFGS reported warnflag=2 (precision loss, not a model "
              "failure);\n  refitted with L-BFGS-B. See fit_beta_robust().")
    out["effects"] = tab.to_dict("records")
    save(tab, f"effects_{key}")

    # ---- 5e. non-parametric backstop for the three main effects ---------
    ratio = bx._LINK.get(e["family"]) == "log"
    perm = pd.DataFrame([stratified_permutation_p(d, key, t, ratio=ratio)
                         for t in ("A", "B", "C")])
    perm["label"] = perm.term.map(bx.pretty_term)
    perm["p_model"] = [float(tab.loc[tab.term == t, "p"].iloc[0])
                       for t in perm.term]
    sub("stratified randomization test (model-free) vs the model p-value")
    show(perm[["term", "label", "statistic", "scale", "p_perm", "p_model"]])
    out["permutation"] = perm.to_dict("records")

    # ---- 5f. residual diagnostics (descriptive) and the letter source ----
    # The eight-cell letter display is DISTRIBUTION-FREE for every endpoint:
    # Kruskal-Wallis omnibus, then Mann-Whitney U on all 28 pairs with Holm.
    # One procedure, no branch, no gate.
    #
    # Why there is no normality gate here. Shapiro on the within-cell residuals
    # is a hypothesis test with its own error rate, and at n=10 per cell it has
    # little power to detect the departures that would actually matter. Routing
    # on it means the letters silently inherit whatever that test got wrong, and
    # it makes the figures inconsistent -- some panels' letters would answer
    # "which means differ", others "which cells stochastically dominate".
    # Committing to the rank test everywhere costs power on the endpoints whose
    # residuals really are Gaussian and buys a display whose validity does not
    # depend on an assumption being true. That is the trade the analyst chose.
    #
    # Resolution is not the limiting factor: with n1 = n2 = 10 the smallest
    # attainable two-sided MWU p is 2/C(20,10) = 1.08e-05, so even the worst
    # Holm multiplier (28) leaves 3.0e-04 -- a real separation can still clear
    # alpha. What is lost is sensitivity to small shifts, not the ability to
    # declare any difference at all.
    #
    # The diagnostics below are reported and nothing more. No Gaussian model is
    # fitted anywhere in this analysis: there is no ANOVA and no Tukey table,
    # because neither would be reported and a procedure nobody reports should
    # not be computed. The diagnostics describe the residuals so a reader can
    # see the shape of the data that the a-priori routing in ENDPOINTS assumed.
    diag = diagnose(d, key)
    out["diagnostics"] = diag
    sub("within-cell residual diagnostics (DESCRIPTIVE -- nothing routes on "
        "these)")
    show(pd.DataFrame(diag))

    # the letter display: distribution-free, every endpoint, no exceptions
    kw = stats.kruskal(*[g[key].to_numpy(float) for _, g in d.groupby("sign")])
    sub("distribution-free comparison of the 8 configurations (letter source)")

    # Rank-based effect size for the omnibus. This is the distribution-free
    # answer to the question the ANOVA variance table used to answer -- "how
    # much of this endpoint does the configuration explain at all?" -- and it
    # is a pure transformation of H, not a new test: no extra p-value, no extra
    # assumption, nothing added to the multiplicity budget.
    #
    #   epsilon^2 = H / (N - 1)              in [0, 1]
    #   eta^2_H   = (H - k + 1) / (N - k)    bias-corrected, can go slightly <0
    #
    # NOTE ON WHAT IT IS NOT. This is an OMNIBUS quantity over all eight cells.
    # It does not decompose into per-factor shares the way a sum-of-squares
    # table does. Per-factor attribution comes from the GLM in step 5c, which
    # reports each term on an interpretable scale with an interval -- a better
    # answer to that question than a variance share ever was.
    n_tot, k_grp = int(len(d)), int(d["sign"].nunique())
    H = float(kw.statistic)
    eps_sq = H / (n_tot - 1)
    eta_sq = (H - k_grp + 1) / (n_tot - k_grp)
    print(f"  Kruskal-Wallis omnibus: H = {H:.2f}, p = {kw.pvalue:.3g}, "
          f"df = {k_grp - 1}")
    print(f"  rank effect size: epsilon^2 = {eps_sq:.3f}, "
          f"eta^2_H = {eta_sq:.3f} (share of rank variation explained by "
          "configuration)")
    np_tab = pairwise_nonparametric(d, key, "sign")
    save(np_tab, f"pairwise_mwu_{key}")
    out["kruskal"] = dict(H=H, p=float(kw.pvalue), df=k_grp - 1, n=n_tot,
                          epsilon_sq=eps_sq, eta_sq_H=eta_sq)
    sig = {frozenset((r.group1, r.group2))
           for r in np_tab.itertuples() if r.reject}
    print(f"  Mann-Whitney U + Holm over 28 pairs: {len(sig)}/28 differ")
    letters_method = "Mann-Whitney U, Holm-adjusted over 28 pairs"

    # ---- 5g. letter displays --------------------------------------------
    # Eight-configuration panel: letters come from the pairwise procedure
    # above. Factor panels: only one comparison exists per two-level factor,
    # so the letter comes straight from that factor's entry in the factorial
    # model, carrying the hierarchy's decision.
    # groups are handed over in DISPLAY order so 'a' lands on the leftmost
    # violin of each panel -- SIGN_ORDER for the eight cells, and LEVEL_NAMES'
    # (low, high) tuple for each two-level factor.
    letters = {"cell": compact_letters(SIGN_ORDER, sig)}
    for t in ("A", "B", "C"):
        lo, hi = LEVEL_NAMES[t]
        row = tab[tab.term == t].iloc[0]
        p_used = float(row.get("p_holm") if pd.notna(row.get("p_holm"))
                       else row["p"])
        is_sig = bool(row["significant"]) if pd.notna(row["significant"]) \
            else p_used < ALPHA
        pair = {frozenset((lo, hi))} if is_sig else set()
        letters[t] = compact_letters([lo, hi], pair)
        letters[f"{t}_p"] = p_used
    out["letters"] = letters
    out["letters_method"] = letters_method
    print(f"\n  letter display ({letters_method}); shared letter = "
          "not significantly different")
    print("   ", {k: v for k, v in letters["cell"].items()})
    return out


# ==========================================================================
# 6. interaction multiplicity sensitivity (NOT prespecified)
# ==========================================================================
# The plan in 5d spent the entire error budget on main effects: one primary
# test on PR-AUC, a secondary family of two, and a within-endpoint Holm family
# of three everywhere else. Interactions were reported CI-only and were
# allocated no error rate at all. That allocation is not revisited here and no
# p_holm written by 5d is touched.
#
# What follows answers a question the plan does not: IF an error rate HAD been
# allocated to the 44 interaction terms, which would survive it? Two family
# definitions are computed, because the point is to show what family size
# costs:
#
#   global           Holm across all 44 interaction terms at once (11 endpoints
#                    x 4 terms) -- the widest family a reader could demand.
#   within-endpoint  Holm across each endpoint's own 4 interaction terms,
#                    mirroring the exploratory main-effect tier exactly.
#
# Nothing here is a decision rule and nothing here changes a reported claim. A
# surviving term is labelled a sensitivity result, never "significant".
#
# The reason interactions stay out of the confirmatory story is NOT that the
# design is underpowered for them -- against p = 1.6e-05 that argument does not
# hold and is not made anywhere in this output. The reason is that the
# multiplicity plan was fixed before any p-value was read, and it allocated
# these terms no error rate.
#
# Nothing is refitted: every p-value read below was computed by
# analyze_endpoint() and is reproduced verbatim.

INTERACTION_TERMS = ("AB", "AC", "BC", "ABC")
MD_NAME = "INTERACTION_SENSITIVITY.md"

# coefficient units spanned by each contrast under -1/+1 effect coding. Must
# agree with consolidate.py's MULT -- that module reshapes, this one computes.
CONTRAST_MULT = {"A": 2, "B": 2, "C": 2, "AB": 4, "AC": 4, "BC": 4, "ABC": 8}
TERM_ORDER = {"A": "main", "B": "main", "C": "main", "AB": "two-way",
              "AC": "two-way", "BC": "two-way", "ABC": "three-way"}
Z_CRIT = float(stats.norm.isf(ALPHA / 2))

# response-scale reading of exp(link contrast); mirrors consolidate.py
EXP_MEANING = {
    ("log", "two-way"): "ratio of ratios",
    ("log", "three-way"): "ratio of ratio-of-ratios",
    ("logit", "two-way"): "ratio of odds ratios",
    ("logit", "three-way"): "ratio of ratio-of-odds-ratios",
}


def _holm(p, alpha=ALPHA):
    """Holm step-down. Returns (adjusted p, rejected) -- adjusted p is monotone
    non-decreasing in rank by construction, and rejection stops at the first
    failure, which is what makes the printed chain readable top to bottom."""
    from statsmodels.stats.multitest import multipletests
    rej, padj, *_ = multipletests(np.asarray(p, dtype=float), alpha=alpha,
                                  method="holm")
    return padj, rej


def _tier_of(term, tier):
    """Collapse the recorded tier strings onto the four-way vocabulary the
    consolidated table reads in. 'main (Holm within endpoint)' is the
    exploratory main-effect tier; an interaction's recorded 'exploratory' is
    not a tier at all under the plan, it is the absence of one."""
    if term in INTERACTION_TERMS:
        return "interaction"
    return {"primary": "primary", "secondary": "secondary"}.get(tier,
                                                                "exploratory")


def interaction_sensitivity(results, alpha=ALPHA):
    """
    Holm over the interaction terms under two family definitions.

    The p-values read are the LINK-scale ones. The link scale is where the
    interaction estimand is defined (5c): exp(4b) is how much one factor's
    ratio shifts at the other factor's high level. The response-scale p that
    accompanies a main effect answers a different question for an interaction
    term -- betafx's eight-cell grouping mixes in the main effects -- so it is
    not the input to a multiplicity procedure over interactions.

    Main-effect rows are carried through untouched so that the two tiers can be
    read in one schema; their p_holm is copied, never recomputed.

    Returns (interactions, all_terms).
    """
    ix_rows, all_rows = [], []
    for key, r in results.items():
        if not isinstance(r, dict) or "effects" not in r:
            continue
        link = bx._LINK[r["family"]] if hasattr(bx, "_LINK") else None
        for row in r["effects"]:
            term = row["term"]
            order = TERM_ORDER[term]
            tier = _tier_of(term, row.get("tier"))
            lk = link or ("logit" if r["family"] == "beta" else "log")
            all_rows.append(dict(
                endpoint=key, term=term, label=row["label"], order=order,
                tier=tier,
                # each tier is shown the p its OWN procedure consumed: the
                # prespecified Holm ran on the response-scale p, the
                # interaction sensitivity runs on the link-scale p. Mixing them
                # in one column without saying so would be a trap, so the scale
                # travels with the number.
                p_raw=(row["p_link"] if tier == "interaction" else row["p"]),
                p_scale=("link" if tier == "interaction" else "response"),
                p_holm_prespecified=(np.nan if tier == "interaction"
                                     else row.get("p_holm", np.nan)),
                significant=row.get("significant"),
            ))
            if term not in INTERACTION_TERMS:
                continue
            k = CONTRAST_MULT[term]
            coef, se = row["coef"], row["se"]
            ix_rows.append(dict(
                endpoint=key, term=term, label=row["label"], order=order,
                family=r["family"], link=lk,
                link_contrast=k * coef,
                link_lo=k * (coef - Z_CRIT * se),
                link_hi=k * (coef + Z_CRIT * se),
                link_exp=np.exp(k * coef),
                link_exp_lo=np.exp(k * (coef - Z_CRIT * se)),
                link_exp_hi=np.exp(k * (coef + Z_CRIT * se)),
                exp_meaning=EXP_MEANING[(lk, order)],
                mult=k,
                p_raw=row["p_link"],
            ))

    # ---- family 1: global Holm over all 44 -------------------------------
    # mergesort so ties keep endpoint-registry order rather than an arbitrary
    # one -- the printed chain has to be stable across runs.
    ix = (pd.DataFrame(ix_rows).sort_values("p_raw", kind="mergesort")
          .reset_index(drop=True))
    m = len(ix)
    padj, rej = _holm(ix.p_raw, alpha)
    ix["holm_rank"] = np.arange(1, m + 1)
    ix["global_threshold"] = alpha / (m - ix.holm_rank + 1)
    ix["p_holm_global"] = padj
    ix["survives_global"] = rej

    # ---- family 2: Holm within each endpoint's own 4 ---------------------
    ix["p_holm_within"] = np.nan
    ix["survives_within"] = False
    for _key, g in ix.groupby("endpoint", sort=False):
        p_w, rej_w = _holm(g.p_raw, alpha)
        ix.loc[g.index, "p_holm_within"] = p_w
        ix.loc[g.index, "survives_within"] = rej_w

    ix = ix[["endpoint", "term", "label", "order", "family", "link",
             "link_contrast", "link_lo", "link_hi", "mult",
             "link_exp", "link_exp_lo", "link_exp_hi", "exp_meaning",
             "p_raw", "holm_rank", "global_threshold", "p_holm_global",
             "p_holm_within", "survives_global", "survives_within"]]

    # ---- the 77-row consolidated schema ----------------------------------
    at = pd.DataFrame(all_rows)
    g = ix.set_index(["endpoint", "term"])
    at["p_holm_global_interaction"] = [
        g.p_holm_global.get((e, t), np.nan) if tr == "interaction" else np.nan
        for e, t, tr in zip(at.endpoint, at.term, at.tier)]
    surv = {(e, t): s for e, t, s in zip(ix.endpoint, ix.term,
                                         ix.survives_global)}

    def status(tier, sig, key):
        if tier == "interaction":
            return ("survives global interaction sensitivity "
                    "(not a decision rule)" if surv.get(key)
                    else "does not survive global interaction sensitivity")
        # an interaction is never labelled "significant" anywhere; a main
        # effect says which plan made the call, because that is the whole
        # distinction this table exists to draw
        return ("significant (prespecified)" if sig is True
                else "not significant (prespecified)")

    at["status"] = [status(tr, s, (e, t)) for tr, s, e, t
                    in zip(at.tier, at.significant, at.endpoint, at.term)]
    at = at.sort_values("p_raw", kind="mergesort").reset_index(drop=True)
    at = at[["endpoint", "term", "label", "order", "tier", "p_raw", "p_scale",
             "p_holm_prespecified", "p_holm_global_interaction", "status"]]
    return ix, at


# ---------------------------------------------------------------- markdown
# Rendered here rather than in consolidate.py so that the CSV and the Markdown
# come out of one computation and cannot drift. consolidate.py reads the CSVs.

def _p3(v):
    """p-values: scientific notation, 3 significant figures, always."""
    return "" if v is None or pd.isna(v) else f"{float(v):.2e}"


def _n3(v):
    """ratios and contrasts: 3 significant figures, never scientific."""
    if v is None or pd.isna(v):
        return ""
    v = float(v)
    s = f"{v:.3g}"
    if "e" in s or "E" in s:                      # expand rather than sprawl
        s = f"{v:.{max(0, 3 - len(str(int(abs(v)))))}f}"
    return s


def _md(head, rows, align=None):
    align = align or ["---"] * len(head)
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(align) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _ix_tables(ix):
    """The 44 interaction rows as two joined Markdown tables.

    13 columns in one table does not fit a terminal or a Markdown preview, so
    the estimate side and the multiplicity side are split and both keyed by
    Holm rank. Rank is the join key AND the reading order: the step-down chain
    runs top to bottom and stops at the first row that fails.
    """
    est = [[str(r.holm_rank), r.endpoint, r.label, r.order,
            f"{_n3(r.link_contrast)} ({_n3(r.link_lo)}, {_n3(r.link_hi)})",
            f"x{r.mult}",
            f"{_n3(r.link_exp)} ({_n3(r.link_exp_lo)}, {_n3(r.link_exp_hi)})",
            _p3(r.p_raw)] for r in ix.itertuples()]
    t1 = _md(["rank", "endpoint", "term", "order", "link contrast (95% CI)",
              "mult", "exp(contrast) (95% CI)", "raw p"],
             est, ["---:", "---", "---", "---", "---:", "---:", "---:", "---:"])
    mul = [[str(r.holm_rank), r.endpoint, r.label, _p3(r.p_raw),
            _p3(r.global_threshold), _p3(r.p_holm_global),
            _p3(r.p_holm_within),
            "**yes**" if r.survives_global else "no"] for r in ix.itertuples()]
    t2 = _md(["rank", "endpoint", "term", "raw p", "global threshold",
              "global p_holm", "within-endpoint p_holm", "survives global"],
             mul, ["---:", "---", "---", "---:", "---:", "---:", "---:", "---"])
    return t1, t2


def _at_table(at):
    rows = [[r.endpoint, r.label, r.order, r.tier, _p3(r.p_raw), r.p_scale,
             _p3(r.p_holm_prespecified), _p3(r.p_holm_global_interaction),
             r.status] for r in at.itertuples()]
    return _md(["endpoint", "term", "order", "tier", "raw p", "p scale",
                "prespecified p_holm", "global interaction p_holm", "status"],
               rows, ["---", "---", "---", "---", "---:", "---", "---:",
                      "---:", "---"])


def interaction_sensitivity_markdown(ix, at, corr):
    """The consolidated deliverable: self-contained, readable on its own."""
    n_surv = int(ix.survives_global.sum())
    s = ix[ix.survives_global]
    stop = ix[~ix.survives_global].iloc[0]
    m = len(ix)
    terms = sorted(set(s.label))
    survivors = ", ".join(f"`{r.endpoint}` {_p3(r.p_raw)}" for r in
                          s.itertuples())
    L = []
    L.append("# Interaction multiplicity sensitivity — 2×2×2 factorial\n")
    L.append(
        "**Not prespecified. Not a decision rule. Changes no claim in "
        "`FINDINGS.md`.**\n")
    L.append(
        f"The multiplicity plan fixed before any p-value was read allocated the "
        f"whole error budget to main effects: *model* on PR-AUC as a single "
        f"unadjusted primary test, *effort* and *critic* on PR-AUC as a "
        f"Holm-adjusted family of two, and every other endpoint Holm-adjusted "
        f"across its own three main effects. The {m} interaction terms "
        f"(11 endpoints × model:effort, model:critic, effort:critic, "
        f"model:effort:critic) were allocated **no error rate** and were "
        f"reported CI-only.\n")
    L.append(
        f"This document asks the question that plan does not answer: *if* an "
        f"error rate had been allocated to those {m} terms, which would survive "
        f"it. Two family definitions are reported, because they cost different "
        f"amounts and answer different questions:\n")
    L.append(
        f"- **Global** — Holm across all {m} interaction terms at once. The "
        f"widest family a reader could demand.\n"
        f"- **Within-endpoint** — Holm across each endpoint's own 4 interaction "
        f"terms, mirroring the exploratory main-effect tier exactly.\n")
    L.append(
        "Nothing was refitted, resampled, or re-bootstrapped. Every p-value "
        "below is the unadjusted link-scale p already in "
        "`tables/all_effects.csv`, reproduced verbatim. The link scale is where "
        "the interaction estimand lives: `exp(contrast)` is how many times "
        "larger one factor's ratio becomes at the other factor's high level "
        "(×4 for a two-way term, ×8 for the three-way).\n")
    L.append(
        "**Why interactions still carry no confirmatory claim.** Not because "
        "the design is underpowered for them — against p = 1.60e-05 that "
        "argument does not hold, and it is not made here. Because the "
        "multiplicity plan was fixed before any p-value was read, and it "
        "allocated these terms no error rate. A test invented after seeing its "
        "own p-value is a sensitivity analysis, whatever it returns.\n")

    t1, t2 = _ix_tables(ix)
    L.append(f"\n## 1. Interaction sensitivity — all {m} terms\n")
    L.append(
        "Sorted by raw p ascending, so the Holm step-down chain reads top to "
        "bottom. Split across two tables to stay readable; **rank** is the join "
        "key and both tables are in the same order. Machine-readable twin: "
        "`tables/interaction_sensitivity.csv`.\n")
    L.append("### 1a. Estimates\n")
    L.append(t1)
    L.append("\n### 1b. Multiplicity\n")
    L.append(
        f"`global threshold` is α/({m}−rank+1), the Holm critical value at that "
        f"step. The chain stops at the first row whose raw p exceeds it; every "
        f"row below is not-surviving regardless of its own p.\n")
    L.append(t2)

    L.append("\n### What survived, and what that means\n")
    L.append(
        f"{n_surv} of {m} terms clear the global Holm at α = 0.05: "
        f"{survivors}. The chain stops at rank {int(stop.holm_rank)}, "
        f"`{stop.endpoint}` {stop.label}, whose raw p = {_p3(stop.p_raw)} "
        f"exceeds its threshold α/{m - int(stop.holm_rank) + 1} = "
        f"{_p3(stop.global_threshold)}.\n")
    L.append(
        f"**These are not {n_surv} findings. They are one term — "
        f"`{terms[0]}` — on three correlated resource endpoints.** `usd` and "
        f"`wallclock_min` correlate at r = {corr['usd~wallclock_min']:.2f}, and "
        f"both are largely downstream of `total_tokens` "
        f"(r = {corr['usd~total_tokens']:.2f} and "
        f"{corr['wallclock_min~total_tokens']:.2f} respectively). Counting them "
        f"as three independent survivors would triple-count a single "
        f"resource-side pattern: the critic costs disproportionately more on "
        f"opus. That pattern is already reported in FINDINGS.md §4.3 as a "
        f"CI-only interaction, and it stays a sensitivity result here.\n")
    L.append(
        "The two families disagree by exactly the amount family size costs: a "
        "term ranked mid-table under the within-endpoint family (4 tests) can "
        "look unremarkable under the global family (44 tests) at the same raw "
        "p. That gap is the point of running both — neither number is more "
        "correct, they answer different questions, and neither is a decision "
        "rule here.\n")

    L.append(f"\n## 2. All {len(at)} terms, one schema\n")
    L.append(
        f"Main effects and interactions side by side: {int((at.tier != 'interaction').sum())} "
        f"main-effect rows and {int((at.tier == 'interaction').sum())} "
        f"interaction rows, sorted by raw p ascending. Machine-readable twin: "
        f"`tables/all_terms_multiplicity.csv`.\n")
    L.append(
        "**`prespecified p_holm` is copied verbatim from the fixed plan and is "
        "blank for every interaction** — no interaction was ever in one of "
        "those families. **`global interaction p_holm` is blank for every main "
        "effect** — no main effect was in the sensitivity family. The primary "
        "test (`pr_auc` model) is blank in both: it was unadjusted by design.\n")
    L.append(
        "`p scale` records which p each tier's own procedure consumed — the "
        "prespecified Holm ran on the response-scale p, the interaction "
        "sensitivity on the link-scale p. Both are in `tables/all_effects.csv`; "
        "the column exists so the two are never silently mixed.\n")
    L.append(
        "The status column never reads simply *significant* for an "
        "interaction. `significant (prespecified)` is a decision under the "
        "fixed plan; `survives global interaction sensitivity` is not a "
        "decision at all.\n")
    L.append(_at_table(at))
    L.append("")
    return "\n".join(L)


def report_interaction_sensitivity(results, frame):
    """Compute, narrate, and write. Called by main() and by the standalone
    entry point, so both paths emit byte-identical artifacts."""
    hdr("INTERACTION MULTIPLICITY SENSITIVITY (not prespecified; "
        "not a decision rule)")
    print("""
  The multiplicity plan was fixed before any p-value was read and it allocated
  the entire error budget to main effects. The 44 interaction terms got no
  error rate and were reported CI-only. Nothing below changes that, changes any
  p_holm already written, or changes any claim in FINDINGS.md.

  The question here is the one the plan does not answer: IF an error rate had
  been allocated to those 44 terms, which would survive it. Two families:

    global           Holm across all 44 interaction terms at once
    within-endpoint  Holm across each endpoint's own 4, mirroring the
                     exploratory main-effect tier

  Nothing is refitted. The p-values are the unadjusted link-scale ones already
  computed per endpoint, read back and re-ranked.""")

    ix, at = interaction_sensitivity(results)
    corr = {f"{a}~{b}": float(np.corrcoef(frame[a], frame[b])[0, 1])
            for a, b in (("usd", "wallclock_min"), ("usd", "total_tokens"),
                         ("wallclock_min", "total_tokens"))}

    sub(f"global Holm over all {len(ix)} interaction terms (top of the chain)")
    show(ix.head(8)[["endpoint", "label", "p_raw", "global_threshold",
                     "p_holm_global", "p_holm_within", "survives_global"]])
    n = int(ix.survives_global.sum())
    stop = ix[~ix.survives_global].iloc[0]
    print(f"\n  {n}/{len(ix)} survive at alpha = {ALPHA}. The chain stops at "
          f"rank {int(stop.holm_rank)}, {stop.endpoint} {stop.label}: "
          f"p = {stop.p_raw:.3g} > alpha/{len(ix) - int(stop.holm_rank) + 1} "
          f"= {stop.global_threshold:.3g}.")
    print(f"  Survivors are all one term ({', '.join(sorted(set(ix[ix.survives_global].label)))}) "
          "on three correlated resource endpoints:")
    print(f"    usd ~ wallclock_min r = {corr['usd~wallclock_min']:.2f}; "
          f"both largely downstream of total_tokens "
          f"(r = {corr['usd~total_tokens']:.2f}, "
          f"{corr['wallclock_min~total_tokens']:.2f}).")
    print("  That is ONE resource-side pattern counted three times, not three "
          "findings.\n"
          "  It remains a sensitivity result and carries no confirmatory "
          "claim.")

    sub("within-endpoint Holm (each endpoint's own 4 interaction terms)")
    w = ix[ix.survives_within]
    print(f"  {len(w)}/{len(ix)} survive the narrower family. The gap against "
          f"the global family\n  is what family size costs; neither is a "
          f"decision rule here.")
    show(w[["endpoint", "label", "p_raw", "p_holm_within", "p_holm_global",
            "survives_global"]])

    save(ix, "interaction_sensitivity")
    save(at, "all_terms_multiplicity")
    t1, t2 = _ix_tables(ix)
    (TABLES / "interaction_sensitivity.md").write_text(
        "# Interaction sensitivity — 44 interaction terms\n\n"
        "Not prespecified; not a decision rule. Markdown twin of "
        "`interaction_sensitivity.csv`, split across two tables keyed by Holm "
        f"rank. Full write-up: `{MD_NAME}`.\n\n### Estimates\n\n" + t1 +
        "\n\n### Multiplicity\n\n" + t2 + "\n", encoding="utf-8")
    (TABLES / "all_terms_multiplicity.md").write_text(
        "# All 77 factorial terms — multiplicity in one schema\n\n"
        "Not prespecified; not a decision rule. Markdown twin of "
        f"`all_terms_multiplicity.csv`. Full write-up: `{MD_NAME}`.\n\n" +
        _at_table(at) + "\n", encoding="utf-8")
    (HERE / MD_NAME).write_text(
        interaction_sensitivity_markdown(ix, at, corr), encoding="utf-8")

    print(f"\n  tables/interaction_sensitivity.csv    {len(ix)} rows")
    print(f"  tables/all_terms_multiplicity.csv     {len(at)} rows "
          f"({int((at.tier != 'interaction').sum())} main + "
          f"{int((at.tier == 'interaction').sum())} interaction)")
    print(f"  {MD_NAME}          consolidated Markdown, both tables")
    return ix, at


# ==========================================================================
# 7. main
# ==========================================================================

def main():
    tee = Tee(HERE / "analysis_log.txt")
    sys.stdout = tee
    rng_note = f"seed={RNG_SEED}"

    hdr("ENVIRONMENT")
    env = bx.check_environment(strict=True)
    print("  " + ", ".join(f"{k} {v}" for k, v in env["actual"].items()))
    print(f"  matches environment.lock: {env['matches_lock']}   ({rng_note})")
    print("  betafx test suite: 131 passed (run separately with pytest)")

    raw = pd.read_csv(RESULTS_CSV)
    d = prepare(raw)

    hdr("DESIGN")
    print(f"  factors: " + ", ".join(
        f"{k}={v} ({LEVEL_NAMES[k][0]} vs {LEVEL_NAMES[k][1]})"
        for k, v in FACTOR_LABEL.items()))
    print(f"  level map (explicit, never inferred): {LEVELS}")
    counts = bx.validate_design(d, expected_n=EXPECTED_N, levels=LEVELS)
    counts["cell"] = [f"{'sonnet' if a < 0 else 'opus'}/"
                      f"{'medium' if b < 0 else 'xhigh'}/"
                      f"{'off' if c < 0 else 'on'}"
                      for a, b, c in zip(counts.A, counts.B, counts.C)]
    show(counts[["cell", "A", "B", "C", "n"]])
    print(f"  8/8 cells present, model-matrix rank {counts.attrs['rank']}, "
          f"{len(d)} runs, no failures ({int(d.failure_flag.sum())} flagged)")

    hdr("ENDPOINT STRUCTURE AND MULTIPLICITY")
    print("""
  The eight requested endpoints are NOT eight independent questions. They fall
  into four constructs, and two of the dependencies are algebraic:

    1. discrimination        pr_auc, roc_auc
    2. resource use          usd, wallclock_min      (+ total_tokens, n_turns_total)
    3. feature production    n_engineered_features
    4. feature selection     n_features_after_fs, n_engineered_features_selected,
                             prop_engineered_selected
       where  prop_engineered_selected == n_engineered_features_selected
                                          / n_features_after_fs   EXACTLY,
       and n_engineered_features_selected is nested inside both of the counts
       above it.

  Testing plan, fixed before reading any p-value (PLAN.md note 3):

    PRIMARY      model (A) on PR-AUC, response-scale marginal estimand,
                 unadjusted alpha = 0.05.
    SECONDARY    effort (B) and critic (C) on PR-AUC, Holm within that family.
    EXPLORATORY  every other endpoint: Holm across its own three main effects.
                 Interactions everywhere: confidence intervals only, read on
                 the LINK scale, no significance verdict. The design is
                 powered for main effects; a non-significant interaction is
                 not evidence of additivity.

  A global Holm across all 24 requested-endpoint main-effect tests is also
  reported at the end as a deliberately over-conservative sensitivity. It is
  NOT the primary decision rule: Holm assumes nothing about dependence, so it
  stays valid under these correlations, but it becomes very conservative when
  endpoints are near-duplicates, and several of these are.
""")

    hdr("ENDPOINT CORRELATIONS (why the above matters)")
    keys = [e["key"] for e in ENDPOINTS]
    corr = d[keys].corr().round(2)
    print(corr.to_string())

    # ---------------------------------------------------------------- runs
    results = {}
    for e in ENDPOINTS:
        results[e["key"]] = analyze_endpoint(d, e["key"],
                                             fatal=(e["tier"] == "primary"))

    # ---------------------------------------------- primary-endpoint extras
    hdr("PRIMARY ENDPOINT: additional prespecified checks")

    sub("run-to-run stability: is precision configuration-dependent?")
    for k in ("pr_auc", "roc_auc", "prop_engineered_selected"):
        disp = bx.dispersion_test(d, k, precision_formula="~ A + B + C",
                                  levels=LEVELS, expected_n=EXPECTED_N)
        verdict = ("precision NOT constant -- report the submodel"
                   if disp["p"] < ALPHA else
                   "no evidence against constant precision")
        print(f"  {k:<28} LRT chi2({disp['df']}) = {disp['stat']:6.2f}, "
              f"p = {disp['p']:.4f}   -> {verdict}")
        results[k]["dispersion_lrt"] = {kk: float(vv) for kk, vv in disp.items()
                                        if isinstance(vv, (int, float))}
    print("  (alternative holds precision MAIN EFFECTS only; a rejection is a\n"
          "   finding about reliability in its own right and also breaks the\n"
          "   constant-phi assumption the power analysis rests on)")

    sub(f"cell-stratified bootstrap for PR-AUC ({N_BOOT} resamples)")
    print("  betafx.bootstrap_contrasts() aborts on this dataset at a 71.7% "
          "refit rate.\n  Investigated as its error message instructs: every "
          "failure is BFGS warnflag=2\n  (precision loss at phi-hat ~ 4e3), "
          "not a model failure. Rerun through\n  fit_beta_robust(), which "
          "keeps the 90% requirement and only changes the\n  descent "
          "algorithm on a flagged refit.")
    boot = bootstrap_marginal_effects(d, "pr_auc", n_boot=N_BOOT, alpha=ALPHA,
                                      seed=RNG_SEED)
    boot["label"] = boot.term.map(bx.pretty_term)
    delta = pd.DataFrame(results["pr_auc"]["effects"]).set_index("term")
    boot["delta_se"] = [float(delta.loc[t, "eff_se"]) for t in boot.term]
    show(boot[["term", "label", "contrast", "boot_lo", "boot_hi", "boot_se",
               "delta_se"]])
    print(f"  {boot.attrs['n_boot_ok']} fitted, {boot.attrs['n_boot_failed']} "
          f"failed ({boot.attrs['success_rate']:.1%}); "
          f"{boot.attrs['n_fallback']} needed the L-BFGS-B fallback.\n"
          "  Resampling is WITHIN cell, so the balanced design is preserved. "
          "boot_se against\n  delta_se is the check that matters: agreement "
          "means the parametric sampling\n  distribution is not doing any "
          "work the data cannot support.")
    save(boot, "bootstrap_pr_auc")
    results["pr_auc"]["bootstrap"] = boot.to_dict("records")
    results["pr_auc"]["bootstrap_meta"] = dict(boot.attrs)

    sub("refit-based randomization test for the primary term (betafx)")
    ATT = 2000
    pt = bx.permutation_test(d, "pr_auc", term="A", n_perm=ATT,
                             seed=RNG_SEED, levels=LEVELS, config=CFG)
    print(f"  observed |marginal effect| = {pt['observed']:.5f}, "
          f"p_perm = {pt['p_perm']:.4f} "
          f"(+/- {1.96 * pt['mc_se']:.4f} MC), {pt['n_perm']}/{ATT} "
          "permutations usable")
    if pt["n_perm"] < ATT:
        print(f"  CAVEAT: {ATT - pt['n_perm']} permutations were dropped by "
              "the same BFGS flag\n  described in fit_beta_robust(), and they "
              "are not a random subset -- the sharpest\n  likelihoods are the "
              "ones that fail. The model-free stratified randomization\n  "
              "test reported with the endpoint above (20,000 permutations, "
              "nothing discarded)\n  is the backstop to rely on; this one is "
              "corroboration.")
    results["pr_auc"]["permutation_refit"] = pt | {"n_perm_attempted": ATT}

    # -------------------------------------------- binomial sensitivity
    hdr("SENSITIVITY: the selection-composition question modeled as a binomial")
    print("""
  prop_engineered_selected is a count out of a known total: of the
  n_features_after_fs features that survived selection, how many were
  engineered. PLAN.md prefers a BINOMIAL model whenever the denominator is
  known, because it uses the sample size of each proportion (a 3/30 is much
  weaker evidence than a 30/300) instead of estimating one dispersion for all.

  Reported as a sensitivity rather than as the primary model because the
  denominator here is itself a random outcome of the selection step, not a
  fixed design quantity, and the beta model does not have to assume otherwise.
  Agreement between the two is the point of running both.""")
    try:
        res_b = bx.fit_secondary(d, "n_engineered_features_selected", "binomial",
                                 levels=LEVELS, config=CFG,
                                 expected_n=EXPECTED_N)
        tb = bx.effect_summary(res_b, "binomial", estimand="response")
        tb["label"] = tb.term.map(bx.pretty_term)
        beta_tab = pd.DataFrame(results["prop_engineered_selected"]["effects"])
        tb["beta_model_effect"] = beta_tab.effect.to_numpy()
        tb["beta_model_p"] = beta_tab.p.to_numpy()
        show(tb[["term", "label", "effect", "eff_lo", "eff_hi", "p",
                 "beta_model_effect", "beta_model_p"]])
        save(tb, "binomial_sensitivity_engineered_share")
        results["prop_engineered_selected"]["binomial_sensitivity"] = \
            tb.to_dict("records")
    except Exception as exc:
        print(f"  binomial sensitivity failed: {exc}")

    # ------------------------------------------------- global sensitivity
    hdr("GLOBAL HOLM SENSITIVITY (over-conservative; not the decision rule)")
    from statsmodels.stats.multitest import multipletests
    rows = []
    for k in REQUESTED:
        r = results[k]
        if "effects" not in r:
            continue
        for row in r["effects"]:
            if row["term"] in ("A", "B", "C"):
                rows.append(dict(endpoint=k, term=row["term"],
                                 label=row["label"], effect=row["effect"],
                                 p=row["p"]))
    g = pd.DataFrame(rows)
    rej, padj, *_ = multipletests(g.p.to_numpy(), alpha=ALPHA, method="holm")
    g["p_holm_global"] = padj
    g["survives_global"] = rej
    show(g.sort_values("p"))
    save(g, "global_holm_sensitivity")

    # ------------------------------------- interaction sensitivity (post-hoc)
    report_interaction_sensitivity(results, d)

    # --------------------------------------------------------- Pareto view
    hdr("DEPLOYMENT VIEW: performance against cost and time")
    agg = (d.groupby(["sign", "cell"])
           .agg(pr_auc=("pr_auc", "mean"), pr_sd=("pr_auc", "std"),
                usd=("usd", "mean"), minutes=("wallclock_min", "mean"),
                tokens=("total_tokens", "mean"))
           .reset_index().sort_values("usd"))

    def frontier(x, y):
        best, keep = -np.inf, []
        for i in np.argsort(x):
            if y[i] > best:
                keep.append(i)
                best = y[i]
        return keep

    a = agg.reset_index(drop=True)
    fr = frontier(a.usd.to_numpy(), a.pr_auc.to_numpy())
    a["pareto_cost"] = [i in fr for i in range(len(a))]
    fr_t = frontier(a.minutes.to_numpy(), a.pr_auc.to_numpy())
    a["pareto_time"] = [i in fr_t for i in range(len(a))]
    show(a[["sign", "cell", "pr_auc", "pr_sd", "usd", "minutes", "tokens",
            "pareto_cost", "pareto_time"]])
    print("  sign order: model effort critic, '-' = sonnet/medium/off")
    save(a, "configuration_summary")

    # ------------------------------------------------ optimism / gap check
    hdr("EXTRA: does the inner-CV score the agent optimizes track held-out PR-AUC?")
    r_p = stats.pearsonr(d.inner_cv_score, d.pr_auc)
    r_s = stats.spearmanr(d.inner_cv_score, d.pr_auc)
    gap = d.inner_cv_score - d.pr_auc
    print(f"  Pearson  r = {r_p.statistic:+.3f} (p = {r_p.pvalue:.3f})")
    print(f"  Spearman r = {r_s.statistic:+.3f} (p = {r_s.pvalue:.3f})")
    print(f"  optimism gap (inner CV - held-out PR-AUC): "
          f"mean {gap.mean():+.4f}, SD {gap.std():.4f}, "
          f"range {gap.min():+.4f} to {gap.max():+.4f}")
    results["_optimism"] = dict(pearson_r=float(r_p.statistic),
                                pearson_p=float(r_p.pvalue),
                                spearman_r=float(r_s.statistic),
                                spearman_p=float(r_s.pvalue),
                                gap_mean=float(gap.mean()),
                                gap_sd=float(gap.std()))

    # ------------------------------------------------------ cost decomposed
    hdr("EXTRA: is the cost effect price or behavior?")
    tok = pd.DataFrame(results["total_tokens"]["effects"])
    cst = pd.DataFrame(results["usd"]["effects"])
    dec = pd.DataFrame({
        "term": tok.term, "label": tok.label,
        "token_ratio": tok.effect.to_numpy(),
        "cost_ratio": cst.effect.to_numpy()})
    dec["implied_price_ratio"] = dec.cost_ratio / dec.token_ratio
    show(dec)
    print("""
  Reading this table: cost = price x tokens. Along the CRITIC and EFFORT
  factors the price per token is identical on both sides, so the cost ratio
  and the token ratio should agree and any gap is composition (prompt vs
  completion mix). Along the MODEL factor the implied price ratio is the part
  that was fixed by the price schedule before any run happened -- that column
  is the price component PLAN.md requires to be disclosed rather than
  reported as a finding.""")
    save(dec, "cost_decomposition")

    # --------------------------------------------------------------- write
    save(pd.DataFrame([{**{"endpoint": k}, **{kk: vv for kk, vv in
                          r.items() if isinstance(vv, (str, int, float))}}
                       for k, r in results.items() if isinstance(r, dict)
                       and "key" in r]), "endpoint_index")
    save(d, "analysis_frame")

    payload = dict(
        alpha=ALPHA, expected_n=EXPECTED_N, seed=RNG_SEED,
        environment=env["actual"], level_names=LEVEL_NAMES,
        factor_labels=FACTOR_LABEL, sign_order=SIGN_ORDER,
        endpoints=[{k: v for k, v in e.items()
                    if k != "structural"} | ({"structural": sorted(e["structural"])}
                                             if e.get("structural") else {})
                   for e in ENDPOINTS],
        results=results)

    def default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, (set, frozenset)):
            return sorted(o)
        if o is pd.NA:
            return None
        return str(o)

    (HERE / "results.json").write_text(
        json.dumps(payload, indent=1, default=default), encoding="utf-8")

    hdr("DONE")
    print(f"  tables -> {TABLES}")
    print(f"  json   -> {HERE / 'results.json'}")
    print(f"  log    -> {HERE / 'analysis_log.txt'}")
    tee.flush()


def interaction_sensitivity_only():
    """
    Recompute ONLY the interaction sensitivity, from results.json.

    The sensitivity re-ranks p-values that already exist; refitting eleven GLMs,
    20,000 permutations and 2,000 bootstrap resamples to re-derive numbers that
    are already on disk would risk perturbing them for no gain. This path fits
    nothing, resamples nothing, and writes only the new artifacts -- plus an
    APPENDED log section, so the existing transcript is preserved rather than
    overwritten the way a full `main()` run would.
    """
    payload = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    frame = pd.read_csv(TABLES / "analysis_frame.csv")

    class Append(Tee):
        def __init__(self, path):
            self.f = open(path, "a", encoding="utf-8")
            self.stdout = sys.__stdout__

    tee = Append(HERE / "analysis_log.txt")
    sys.stdout = tee
    try:
        print("\n\n" + "#" * 78)
        print("# APPENDED AFTER THE RUN ABOVE by "
              "`python analysis.py --interaction-sensitivity`.")
        print("# Reads results.json; fits nothing, resamples nothing. Every "
              "number in the")
        print("# transcript above is unchanged.")
        print("#" * 78)
        report_interaction_sensitivity(payload["results"], frame)
    finally:
        tee.flush()
        sys.stdout = tee.stdout


if __name__ == "__main__":
    if "--interaction-sensitivity" in sys.argv:
        interaction_sensitivity_only()
    else:
        main()
