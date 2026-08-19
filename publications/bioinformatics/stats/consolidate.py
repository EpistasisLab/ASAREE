"""
Consolidate every comparison in the analysis into two master tables and one
self-contained HTML report.

WHY THIS STEP EXISTS
--------------------
`analysis.py` writes one CSV per endpoint per procedure. That layout
is right for provenance (each file is the verbatim output of one call) and wrong
for reading: nobody can answer "is the model x critic interaction consistent
across endpoints?" by opening eleven files. So the last step of an analysis
should always be a JOIN, not another test. Nothing here re-computes a p-value;
this file only reshapes, derives link-scale contrasts that were already implied
by the coefficients, and renders.

THE ONE DERIVED QUANTITY, AND WHY
---------------------------------
betafx's `marginal_effects()` reports, for EVERY term including interactions,
the contrast of the eight predicted cell means grouped by the sign of that
term's column. For a main effect that is exactly the estimand you want. For an
interaction under a log link with large main effects it is NOT an interaction:
the group where (model x critic) = +1 holds both the cheapest cell and the most
expensive one, so its arithmetic mean is dominated by the main effects and the
"effect" reads x2.12 on cost while the interaction coefficient is 0.0896.

betafx's own docstring resolves this -- "TEST on the coefficients, REPORT
these" -- and FINDINGS.md 4.3 fixes the reporting rule to match: interactions
are read **on the link scale**, CI-only, no significance verdict. So this file
adds the link-scale contrast the plan calls for, alongside (never instead of)
the marginal contrast betafx produced. Both are labeled. See `link_contrast`.

With effect coding (-1/+1) the simple effect of A changes by 2*b_AB for a
one-unit change in B, and B moves 2 units across its two levels, so:

    main effect A          = 2 * b_A       (difference of simple means)
    two-way A x B          = 4 * b_AB      (how much A's effect changes with B)
    three-way A x B x C    = 8 * b_ABC     (how much AxB changes with C)

Under a log link each of those exponentiates to a ratio, a ratio-of-ratios, and
a ratio-of-ratios-of-ratios respectively. Under logit they are odds ratios.

THE TWO LAYERS, KEPT APART
--------------------------
Factor inference (all seven terms) comes from each endpoint's own GLM -- beta,
gamma, Poisson-HC0 or NB2 -- and appears in all_effects.csv. The eight-cell
letter display is a SEPARATE, descriptive question and is answered the same way
for every endpoint: Kruskal-Wallis, then Mann-Whitney U with Holm over the 28
pairs. Nothing routes on a normality test, so no panel's letters depend on a
gate that has little power to fail at n=10 per cell, and two adjacent violins
never answer different questions. No Gaussian model is fitted at any point in
the analysis, so there is no ANOVA table and no Tukey table to consolidate --
every number in this report comes from one of those two layers.

OUTPUTS
-------
    tables/all_effects.csv      77 rows = 11 endpoints x 7 terms
    tables/all_pairwise.csv    308 rows = 11 endpoints x 28 cell pairs
    tables/all_kruskal.csv      11 rows = rank omnibus + epsilon^2 per endpoint
    REPORT.html                 one clickable file, no network needed

Run after analysis.py and figures.py:   python consolidate.py
"""
from __future__ import annotations

import base64
import html
import json
import math
from pathlib import Path

import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
TABLES = HERE / "tables"
FIGS = HERE / "figures"
RES = json.loads((HERE / "results.json").read_text(encoding="utf-8"))

Z = float(stats.norm.isf(0.025))

# link per family -- must match betafx._LINK
LINK = {"beta": "logit", "gamma": "log", "poisson": "log", "negbin": "log",
        "gaussian": "identity"}

# how many coefficient units span the contrast, under -1/+1 effect coding
MULT = {"A": 2, "B": 2, "C": 2, "AB": 4, "AC": 4, "BC": 4, "ABC": 8}
ORDER = {"A": "main", "B": "main", "C": "main",
         "AB": "two-way", "AC": "two-way", "BC": "two-way",
         "ABC": "three-way"}

# the eight cells spelled out. Pairwise rows are keyed by sign code ("+-+"),
# which is compact and unreadable; a table meant to be scanned by a person gets
# the words, and keeps the code in parentheses so a row can still be traced
# back to pairwise_mwu_*.csv.
LV = RES["level_names"]
SIGN_LABEL = {s: (f'{LV["A"][s[0] == "+"]} / {LV["B"][s[1] == "+"]} / '
                  f'critic {LV["C"][s[2] == "+"]}') for s in RES["sign_order"]}

# response-scale meaning of exp(link contrast), by order and link
LINK_MEANING = {
    ("log", "main"): "ratio of means",
    ("log", "two-way"): "ratio of ratios (how much one factor's ratio shifts)",
    ("log", "three-way"): "ratio of ratio-of-ratios",
    ("logit", "main"): "odds ratio",
    ("logit", "two-way"): "ratio of odds ratios",
    ("logit", "three-way"): "ratio of ratio-of-odds-ratios",
}


# ==========================================================================
# 1. every factorial effect, all endpoints, all seven terms
# ==========================================================================

def all_effects() -> pd.DataFrame:
    idx = pd.read_csv(TABLES / "endpoint_index.csv").set_index("key")
    frames = []
    for key in idx.index:
        f = TABLES / f"effects_{key}.csv"
        if not f.exists():
            continue
        t = pd.read_csv(f)
        t["endpoint"] = key
        t["endpoint_label"] = idx.loc[key, "label"]
        t["unit"] = idx.loc[key, "unit"]
        t["family"] = idx.loc[key, "family"]
        t["endpoint_tier"] = idx.loc[key, "tier"]
        frames.append(t)
    d = pd.concat(frames, ignore_index=True)

    d["order"] = d.term.map(ORDER)
    d["link"] = d.family.map(LINK)
    k = d.term.map(MULT)

    # ---- the link-scale contrast the interaction plan is written against ----
    d["link_contrast"] = k * d.coef
    d["link_lo"] = k * (d.coef - Z * d.se)
    d["link_hi"] = k * (d.coef + Z * d.se)

    exp_links = d.link.isin(["log", "logit"])
    for src, dst in (("link_contrast", "link_exp"), ("link_lo", "link_exp_lo"),
                     ("link_hi", "link_exp_hi")):
        d[dst] = d[src].map(math.exp).where(exp_links)
    d["link_meaning"] = [LINK_MEANING.get((lk, o), "difference of means")
                         for lk, o in zip(d.link, d.order)]

    # a main effect is verdict-bearing; an interaction is CI-only by the plan
    d["verdict"] = [("significant" if s is True else "not significant")
                    if o == "main" and pd.notna(s) else "CI only (no verdict)"
                    for o, s in zip(d.order, d["significant"])]

    # The p each term was ACTUALLY tested on. Main effects: the response-scale
    # marginal contrast, which is the estimand reported for them throughout and
    # the quantity their CI is built on. Interactions: the link-scale
    # coefficient, because the marginal contrast is contaminated by the main
    # effects for those terms (see the note under A.2). This SELECTS between two
    # columns analysis.py already computed; it calculates nothing new. Displaying
    # p_link next to a verdict derived from p_response is what this replaces --
    # the two disagree, and on n_turns_total/model they disagree across alpha.
    d["p_test"] = [pr if o == "main" else pl
                   for o, pr, pl in zip(d.order, d.p_response, d.p_link)]
    # for a CI-only term the interval IS the report -- does it clear the null?
    lo = d.link_exp_lo.where(exp_links, d.link_lo)
    hi = d.link_exp_hi.where(exp_links, d.link_hi)
    null = exp_links.map({True: 1.0, False: 0.0})
    d["ci_excludes_null"] = (lo > null) | (hi < null)

    cols = ["endpoint", "endpoint_label", "unit", "family", "link",
            "endpoint_tier", "term", "label", "order",
            "coef", "se", "z", "p_link",
            "link_contrast", "link_lo", "link_hi",
            "link_exp", "link_exp_lo", "link_exp_hi", "link_meaning",
            "ci_excludes_null",
            "effect", "eff_lo", "eff_hi", "scale", "p_response", "p_test",
            "tier", "p_holm", "significant", "verdict", "structural"]
    d = d[[c for c in cols if c in d.columns]]
    # endpoints keep the order they were REQUESTED in (that is the order
    # endpoint_index.csv is written in), not alphabetical -- a reader scanning
    # this table is following the question list, not the dictionary. Within an
    # endpoint: mains, then two-ways, then the three-way.
    rank = {k: i for i, k in enumerate(idx.index)}
    d["_e"] = d.endpoint.map(rank)
    d["_o"] = d.order.map({"main": 0, "two-way": 1, "three-way": 2})
    return (d.sort_values(["_e", "_o", "term"])
             .drop(columns=["_e", "_o"]).reset_index(drop=True))


# ==========================================================================
# 1b. the interaction multiplicity sensitivity (read back, never recomputed)
# ==========================================================================
# analysis.py --interaction-sensitivity writes these two tables. Nothing here
# ranks, adjusts or thresholds anything: this module joins and displays. If the
# files are absent the report simply omits the section rather than inventing it.

def interaction_sensitivity():
    a = TABLES / "interaction_sensitivity.csv"
    b = TABLES / "all_terms_multiplicity.csv"
    if not (a.exists() and b.exists()):
        return None, None
    return pd.read_csv(a), pd.read_csv(b)


# ==========================================================================
# 2. every pairwise cell comparison, all endpoints, both procedures
# ==========================================================================

def all_pairwise() -> pd.DataFrame:
    """
    Every row is Mann-Whitney U with Holm over that endpoint's 28 pairs. That
    is the ONE procedure behind every letter in every figure -- no endpoint is
    routed to a parametric test, so a reader never has to ask which panel was
    scored how.

    `p_adj` and `significant` therefore always come from the rank test. No
    point estimate is attached: MWU tests stochastic dominance, not a
    difference of means, and inventing one would invite exactly the misreading
    it causes on `usd` (see FINDINGS.md 3.3).

    There is no parametric column to compare against, because no parametric
    post-hoc is run. A reader who wants an effect size for a pair reads the
    factor model in all_effects.csv, which is the layer that estimates one.
    """
    idx = pd.read_csv(TABLES / "endpoint_index.csv").set_index("key")
    rows = []
    for key in idx.index:
        mw = TABLES / f"pairwise_mwu_{key}.csv"
        if not mw.exists():
            continue
        t = pd.read_csv(mw)
        t["method"] = "Mann-Whitney U + Holm"
        t["estimate"] = pd.NA
        t["ci_lo"] = pd.NA
        t["ci_hi"] = pd.NA
        t["endpoint"] = key
        t["endpoint_label"] = idx.loc[key, "label"]
        t["unit"] = idx.loc[key, "unit"]
        rows.append(t)

    d = pd.concat(rows, ignore_index=True)
    d["significant"] = d.reject.astype(bool)

    # attach the compact letter each cell carries, so a reader can go from the
    # figure straight to the row that justifies it
    letters = {k: v["letters"]["cell"]
               for k, v in RES["results"].items() if "letters" in v}

    def letter(ep, grp):
        m = letters.get(ep, {})
        return m.get(grp, "")

    d["letter1"] = [letter(e, g) for e, g in zip(d.endpoint, d.group1)]
    d["letter2"] = [letter(e, g) for e, g in zip(d.endpoint, d.group2)]
    # the letter display's contract, asserted here as a join-level check
    shared = [bool(set(a) & set(b)) if a and b else None
              for a, b in zip(d.letter1, d.letter2)]
    d["letters_share"] = shared
    bad = d[(d.letters_share.notna()) & (d.letters_share == d.significant)]
    if len(bad):
        raise AssertionError(
            f"letter contract violated on {len(bad)} pairs: sharing a letter "
            f"must be equivalent to NOT significant\n{bad.head()}")

    return d[["endpoint", "endpoint_label", "unit", "method",
              "group1", "group2", "letter1", "letter2", "letters_share",
              "estimate", "ci_lo", "ci_hi", "p_raw", "p_adj", "significant"]]


# ==========================================================================
# 3. the rank-based omnibus, one row per endpoint
# ==========================================================================

def all_kruskal() -> pd.DataFrame:
    """
    The distribution-free replacement for the old ANOVA variance table.

    `epsilon_sq` = H/(N-1) is the share of rank variation attributable to
    configuration -- the honest answer to "does this design explain this
    endpoint at all?" without fitting a Gaussian model to find out.

    It is deliberately NOT a decomposition. A sum-of-squares table split the
    explained share across the seven terms; this does not, because Kruskal-
    Wallis treats the eight cells as one unordered factor. Per-term attribution
    lives in all_effects.csv, on a scale a reader can act on.
    """
    idx = pd.read_csv(TABLES / "endpoint_index.csv").set_index("key")
    rows = []
    for key in idx.index:
        kw = RES["results"].get(key, {}).get("kruskal")
        if not kw:
            continue
        rows.append({"endpoint": key, "endpoint_label": idx.loc[key, "label"],
                     "unit": idx.loc[key, "unit"], "n": kw["n"],
                     "k_cells": kw["df"] + 1, "H": kw["H"], "df": kw["df"],
                     "p": kw["p"], "epsilon_sq": kw["epsilon_sq"],
                     "eta_sq_H": kw["eta_sq_H"]})
    return pd.DataFrame(rows)


# ==========================================================================
# 4. render -- one file, opens by double-click, no network
# ==========================================================================

CSS = """
:root{--surface:#fcfcfb;--card:#fff;--ink:#1a1a19;--ink2:#4a4a47;--muted:#82827c;
--line:#e6e6e1;--lo:#2a78d6;--hi:#eb6834;--good:#1f7a4d;--warn:#8a6d1f}
@media(prefers-color-scheme:dark){:root{--surface:#1a1a19;--card:#222220;
--ink:#f2f2ef;--ink2:#c4c4be;--muted:#8e8e87;--line:#33332f;--lo:#6ba6ee;
--hi:#f5895d;--good:#5cc48d;--warn:#d6b551}}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:32px 28px 80px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.3px}
h2{font-size:19px;margin:0 0 4px;letter-spacing:-.2px}
.sub{color:var(--ink2);margin:0 0 26px;max-width:900px}
.note{color:var(--muted);font-size:13px;max-width:960px;margin:8px 0 18px}
nav{position:sticky;top:0;z-index:20;background:var(--surface);
border-bottom:1px solid var(--line);margin:0 -28px 26px;padding:10px 28px;
display:flex;gap:6px;flex-wrap:wrap}
nav button{font:600 13px inherit;font-family:inherit;color:var(--ink2);
background:none;border:1px solid transparent;border-radius:7px;
padding:7px 13px;cursor:pointer}
nav button:hover{background:var(--card);border-color:var(--line)}
nav button[aria-selected=true]{background:var(--card);border-color:var(--line);
color:var(--ink)}
section{display:none}section.on{display:block}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 12px}
.bar input,.bar select{font:13px inherit;font-family:inherit;color:var(--ink);
background:var(--card);border:1px solid var(--line);border-radius:7px;
padding:7px 11px}
.bar input{min-width:230px}
.count{color:var(--muted);font-size:12.5px;margin-left:auto}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:10px;
background:var(--card);max-height:74vh}
/* max-content so columns hug their text instead of being stretched apart when
   a table has few columns; min-width keeps it filling the card when it has many */
table{border-collapse:collapse;width:max-content;min-width:100%;font-size:13px;
font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:var(--card);text-align:left;
padding:9px 12px;border-bottom:1.5px solid var(--line);white-space:nowrap;
cursor:pointer;font-weight:650;color:var(--ink2);font-size:12px;
letter-spacing:.02em}
th:hover{color:var(--ink)}
th.srt::after{content:" \\2191";color:var(--muted)}
th.srt.desc::after{content:" \\2193"}
td{padding:8px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
tbody tr:hover{background:color-mix(in srgb,var(--line) 40%,transparent)}
td.n{text-align:right}
.tag{display:inline-block;padding:1.5px 7px;border-radius:99px;font-size:11px;
font-weight:650;border:1px solid var(--line);color:var(--ink2)}
.sig{color:var(--good);font-weight:650}
.ns{color:var(--muted)}
.ci{color:var(--warn);font-weight:650}
.main{border-left:3px solid var(--lo)}
.two-way{border-left:3px solid var(--hi)}
.three-way{border-left:3px solid var(--muted)}
figure{margin:0 0 30px}
figure img{width:100%;height:auto;border:1px solid var(--line);border-radius:10px;
background:var(--card);display:block}
figcaption{color:var(--muted);font-size:12.5px;margin-top:7px}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;
background:color-mix(in srgb,var(--line) 45%,transparent);
padding:1px 5px;border-radius:4px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--ink2);
margin:0 0 14px}
.sw{display:inline-block;width:11px;height:11px;border-radius:3px;
vertical-align:-1px;margin-right:5px}
"""

JS = """
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>
    x.setAttribute('aria-selected', x===b));
  document.querySelectorAll('section').forEach(s=>
    s.classList.toggle('on', s.id===b.dataset.t));
});
function rows(t){return [...t.tBodies[0].rows]}
document.querySelectorAll('table').forEach(t=>{
  [...t.tHead.rows[0].cells].forEach((th,i)=>th.onclick=()=>{
    const desc = th.classList.contains('srt') && !th.classList.contains('desc');
    [...t.tHead.rows[0].cells].forEach(x=>x.classList.remove('srt','desc'));
    th.classList.add('srt'); if(desc) th.classList.add('desc');
    const num = th.dataset.n==='1';
    rows(t).sort((a,b)=>{
      let x=a.cells[i].dataset.v??a.cells[i].textContent,
          y=b.cells[i].dataset.v??b.cells[i].textContent;
      if(num){x=parseFloat(x); y=parseFloat(y);
        if(isNaN(x))x=Infinity; if(isNaN(y))y=Infinity; return desc?y-x:x-y}
      return desc? String(y).localeCompare(String(x))
                 : String(x).localeCompare(String(y));
    }).forEach(r=>t.tBodies[0].appendChild(r));
  });
});
function wire(id){
  const t=document.getElementById(id), bar=t.closest('section').querySelector('.bar');
  const q=bar.querySelector('input'), sels=[...bar.querySelectorAll('select')],
        n=bar.querySelector('.count');
  function run(){
    const s=q.value.toLowerCase();
    let k=0;
    rows(t).forEach(r=>{
      const okq = !s || r.textContent.toLowerCase().includes(s);
      const oks = sels.every(sel=>!sel.value ||
        (r.dataset[sel.dataset.k]||'')===sel.value);
      const show = okq && oks; r.style.display = show?'':'none'; if(show)k++;
    });
    n.textContent = k+' of '+rows(t).length+' rows';
  }
  q.oninput=run; sels.forEach(s=>s.onchange=run); run();
}
"""


def fmt(v, sig=3):
    """Numbers a person can read; p-values keep their exponent."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (bool,)):
        return "yes" if v else "no"
    a = abs(v)
    if a and (a < 1e-4 or a >= 1e6):
        return f"{v:.2e}"
    if a >= 1000:
        return f"{v:,.0f}"
    return f"{v:.{sig}g}"


def p_cell(p):
    if p is None or pd.isna(p):
        return '<td class="n"></td>'
    cls = "sig" if p < 0.05 else "ns"
    return f'<td class="n {cls}" data-v="{p}">{fmt(p, 3)}</td>'


def table_html(df, tid, cols, numeric=(), data_keys=(), row_class=None,
               cell_render=None):
    th = "".join(
        f'<th data-n="{"1" if c in numeric else "0"}">{html.escape(lab)}</th>'
        for c, lab in cols)
    out = []
    for _, r in df.iterrows():
        attrs = "".join(f' data-{k.lower()}="{html.escape(str(r[k]))}"'
                        for k in data_keys)
        cls = f' class="{row_class(r)}"' if row_class else ""
        tds = []
        for c, _lab in cols:
            if cell_render and c in cell_render:
                tds.append(cell_render[c](r))
            elif c in numeric:
                v = r[c]
                tds.append(f'<td class="n" data-v="{"" if pd.isna(v) else v}">'
                           f'{fmt(v)}</td>')
            else:
                tds.append(f"<td>{html.escape(str(r[c]) if pd.notna(r[c]) else '')}</td>")
        out.append(f"<tr{attrs}{cls}>{''.join(tds)}</tr>")
    return (f'<div class="scroll"><table id="{tid}"><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(out)}</tbody></table></div>')


def sel(key, label, values):
    opts = "".join(f'<option value="{html.escape(str(v))}">{html.escape(str(v))}'
                   f"</option>" for v in values)
    return (f'<select data-k="{key}"><option value="">{html.escape(label)}'
            f"</option>{opts}</select>")


def render(eff, pair, kruskal, ixs=None, allterms=None):
    figs = sorted(FIGS.glob("*.png"))
    fig_html = []
    for f in figs:
        b64 = base64.b64encode(f.read_bytes()).decode()
        fig_html.append(
            f'<figure><img alt="{html.escape(f.stem)}" '
            f'src="data:image/png;base64,{b64}">'
            f"<figcaption>{html.escape(f.name)}</figcaption></figure>")

    # ---- effects tab -------------------------------------------------------
    e = eff.copy()
    e["link_ci"] = [f"{fmt(a)} to {fmt(b)}" for a, b in zip(e.link_lo, e.link_hi)]
    e["exp_ci"] = [("" if pd.isna(a) else f"{fmt(a)} to {fmt(b)}")
                   for a, b in zip(e.link_exp_lo, e.link_exp_hi)]
    e["resp_ci"] = [f"{fmt(a)} to {fmt(b)}" for a, b in zip(e.eff_lo, e.eff_hi)]
    e_cols = [("endpoint_label", "endpoint"), ("label", "term"),
              ("order", "order"), ("family", "family"),
              ("link_contrast", "link contrast"), ("link_ci", "link 95% CI"),
              ("link_exp", "exp(contrast)"), ("exp_ci", "exp 95% CI"),
              ("link_meaning", "what exp(contrast) means"),
              ("p_test", "p (tested)"),
              ("effect", "betafx marginal effect"), ("resp_ci", "its 95% CI"),
              ("p_holm", "p Holm"), ("verdict", "verdict")]
    eff_tab = table_html(
        e, "tEff", e_cols,
        numeric={"link_contrast", "link_exp", "effect"},
        data_keys=["endpoint", "order", "verdict"],
        row_class=lambda r: r["order"],
        cell_render={
            "p_test": lambda r: p_cell(r.p_test),
            "p_holm": lambda r: p_cell(r.p_holm) if "p_holm" in r else "<td></td>",
            "verdict": lambda r: (
                f'<td class="{"sig" if r.verdict == "significant" else "ci" if r.verdict.startswith("CI") else "ns"}">'
                f"{html.escape(r.verdict)}</td>"),
        })

    # ---- pairwise tab ------------------------------------------------------
    p = pair.copy()
    nm = lambda s: SIGN_LABEL.get(s, s)                              # noqa: E731
    p["cell_a"] = [f"{nm(g)}  ({g})" for g in p.group1]
    p["cell_b"] = [f"{nm(g)}  ({g})" for g in p.group2]
    p["letters"] = p.letter1 + " vs " + p.letter2
    p_cols = [("endpoint_label", "endpoint"),
              ("cell_a", "cell A"), ("cell_b", "cell B"), ("letters", "letters"),
              ("p_raw", "p raw"), ("p_adj", "p Holm"),
              ("significant", "significant")]
    pair_tab = table_html(
        p, "tPair", p_cols,
        data_keys=["endpoint"],
        cell_render={
            "p_raw": lambda r: p_cell(r.p_raw),
            "p_adj": lambda r: p_cell(r.p_adj),
            "significant": lambda r: (
                f'<td class="{"sig" if r.significant else "ns"}">'
                f'{"yes" if r.significant else "no"}</td>'),
        })

    # ---- rank omnibus tab --------------------------------------------------
    k_cols = [("endpoint_label", "endpoint"), ("n", "N"),
              ("k_cells", "cells"), ("H", "H"), ("df", "df"), ("p", "p"),
              ("epsilon_sq", "ε²"), ("eta_sq_H", "η²_H")]
    kruskal_tab = table_html(
        kruskal, "tKw", k_cols,
        numeric={"n", "k_cells", "H", "df", "epsilon_sq", "eta_sq_H"},
        data_keys=["endpoint"],
        cell_render={"p": lambda r: p_cell(r.p)})

    # ---- interaction sensitivity tab --------------------------------------
    # Both tables in full, so this file is a complete record on its own and a
    # reader never has to be handed a .md alongside it.
    ix_tab = at_tab = ix_note = ""
    if ixs is not None:
        x = ixs.copy()
        x["link_ci"] = [f"{fmt(a)} ({fmt(b)} to {fmt(c)})" for a, b, c
                        in zip(x.link_contrast, x.link_lo, x.link_hi)]
        x["exp_ci"] = [f"{fmt(a)} ({fmt(b)} to {fmt(c)})" for a, b, c
                       in zip(x.link_exp, x.link_exp_lo, x.link_exp_hi)]
        x["mult_s"] = ["x" + str(int(m)) for m in x.mult]
        x_cols = [("holm_rank", "rank"), ("endpoint", "endpoint"),
                  ("label", "term"), ("order", "order"), ("mult_s", "mult"),
                  ("link_ci", "link contrast (95% CI)"),
                  ("exp_ci", "exp(contrast) (95% CI)"),
                  ("exp_meaning", "what exp means"), ("p_raw", "raw p"),
                  ("global_threshold", "global threshold"),
                  ("p_holm_global", "global p_holm"),
                  ("p_holm_within", "within-endpoint p_holm"),
                  ("survives_global", "survives global")]
        ix_tab = table_html(
            x, "tIx", x_cols, numeric={"holm_rank"},
            data_keys=["endpoint", "order"],
            row_class=lambda r: "sens" if r.survives_global else "",
            cell_render={
                "p_raw": lambda r: p_cell(r.p_raw),
                "global_threshold": lambda r: (
                    f'<td class="n" data-v="{r.global_threshold}">'
                    f"{fmt(r.global_threshold)}</td>"),
                "p_holm_global": lambda r: p_cell(r.p_holm_global),
                "p_holm_within": lambda r: p_cell(r.p_holm_within),
                "survives_global": lambda r: (
                    f'<td class="{"ci" if r.survives_global else "ns"}">'
                    f'{"sensitivity result" if r.survives_global else "no"}</td>'),
            })
        a2 = allterms.copy()
        a2_cols = [("endpoint", "endpoint"), ("label", "term"),
                   ("order", "order"), ("tier", "tier"), ("p_raw", "raw p"),
                   ("p_scale", "p scale"),
                   ("p_holm_prespecified", "prespecified p_holm"),
                   ("p_holm_global_interaction", "global interaction p_holm"),
                   ("status", "status")]
        at_tab = table_html(
            a2, "tAll", a2_cols, data_keys=["endpoint", "tier", "order"],
            cell_render={
                "p_raw": lambda r: p_cell(r.p_raw),
                "p_holm_prespecified": lambda r: p_cell(r.p_holm_prespecified),
                "p_holm_global_interaction":
                    lambda r: p_cell(r.p_holm_global_interaction),
                "status": lambda r: (
                    f'<td class="{"sig" if r.status.startswith("significant") else "ci" if r.status.startswith("survives") else "ns"}">'
                    f"{html.escape(r.status)}</td>"),
            })
        n_s = int(ixs.survives_global.sum())
        surv = ixs[ixs.survives_global]
        stop = ixs[~ixs.survives_global].iloc[0]
        terms = sorted(set(surv.label))
        ix_note = (
            f"{n_s} of {len(ixs)} terms clear the global Holm at "
            f"&alpha;&nbsp;=&nbsp;0.05: "
            + ", ".join(f"<code>{html.escape(r.endpoint)}</code> "
                        f"{fmt(r.p_raw)}" for r in surv.itertuples())
            + f". The chain stops at rank {int(stop.holm_rank)}, "
            f"<code>{html.escape(stop.endpoint)}</code> "
            f"{html.escape(stop.label)}, whose raw p = {fmt(stop.p_raw)} "
            f"exceeds its threshold &alpha;/"
            f"{len(ixs) - int(stop.holm_rank) + 1} = "
            f"{fmt(stop.global_threshold)}. <b>These are not {n_s} findings. "
            f"They are one term &mdash; <code>{html.escape(terms[0])}</code> "
            f"&mdash; on three correlated resource endpoints</b> "
            f"(<code>usd</code> and <code>wallclock_min</code> correlate at "
            f"r&nbsp;=&nbsp;0.91, and both are largely downstream of "
            f"<code>total_tokens</code>), so counting them as three "
            f"independent survivors would triple-count a single resource-side "
            f"pattern: the critic costs disproportionately more on opus.")

    eps = list(dict.fromkeys(eff.endpoint_label))
    body = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>2x2x2 factorial - every comparison</title><style>{CSS}</style>
<div class="wrap">
<h1>Multi-agent ML pipeline &mdash; every comparison in one place</h1>
<p class="sub">2&times;2&times;2 factorial: <b>model</b> (sonnet / opus) &times;
<b>effort</b> (medium / xhigh) &times; <b>critic</b> (off / on), n = 10 per cell,
N = 80. Every table below is a reshape of <code>tables/*.csv</code> &mdash;
no p-value is recomputed here. Click any column header to sort; type to filter.</p>

<nav>
  <button data-t="eff" aria-selected="true">Factorial effects ({len(eff)})</button>
  <button data-t="pair">Pairwise cells ({len(pair)})</button>
  <button data-t="kw">Rank omnibus ({len(kruskal)})</button>
  {'<button data-t="ix">Interaction sensitivity</button>' if ixs is not None else ''}
  <button data-t="fig">Figures ({len(figs)})</button>
  <button data-t="how">How to read this</button>
</nav>

<section id="eff" class="on">
  <h2>All seven factorial terms &times; {eff.endpoint.nunique()} endpoints</h2>
  <p class="note">Three main effects, three two-way interactions and the
  three-way, for each endpoint. <b>link contrast</b> is the effect-coded
  coefficient scaled to the contrast it represents (&times;2 main, &times;4
  two-way, &times;8 three-way) &mdash; this is the interaction estimand
  FINDINGS.md &sect;4.3 specifies, read on the link scale, CI-only.
  <b>betafx marginal effect</b> is the eight-cell contrast from
  <code>marginal_effects()</code>; for main effects it is the estimand you want,
  for interactions it is confounded with the main effects and is shown for
  provenance only.</p>
  <p class="note" style="max-width:900px"><b><code>p (tested)</code> is the
  p-value that term was actually tested on</b>, and the scale follows the
  estimand: <i>main effects</i> are tested on the response-scale marginal
  contrast &mdash; the quantity in the <code>betafx marginal effect</code> column
  and the one its CI is built on &mdash; while <i>interactions</i> are tested on
  the link-scale coefficient, because the marginal contrast is contaminated by
  the main effects for those terms. <code>p Holm</code> and <code>verdict</code>
  are computed from this column, so the three always agree. Both raw p-values are
  kept side by side in <code>tables/all_effects.csv</code> as
  <code>p_link</code> and <code>p_response</code>. The two can differ sharply
  &mdash; on <code>usd</code>/effort by ten orders of magnitude &mdash; but only
  one main effect in the study changes verdict between them
  (<code>n_turns_total</code>/model; see below).</p>
  <div class="legend">
    <span><i class="sw" style="background:var(--lo)"></i>main effect</span>
    <span><i class="sw" style="background:var(--hi)"></i>two-way</span>
    <span><i class="sw" style="background:var(--muted)"></i>three-way</span>
  </div>
  <div class="bar">
    <input placeholder="filter&hellip; e.g. critic, usd, pr_auc">
    {sel("endpoint", "all endpoints", sorted(eff.endpoint.unique()))}
    {sel("order", "all orders", ["main", "two-way", "three-way"])}
    <span class="count"></span>
  </div>
  {eff_tab}
</section>

<section id="pair">
  <h2>All 28 cell-vs-cell comparisons &times; {pair.endpoint.nunique()} endpoints</h2>
  <p class="note">One procedure for every endpoint: <b>Mann-Whitney U with Holm
  over the 28 pairs</b>. No routing, no gate &mdash; the display is valid whether
  or not the residuals happen to be Gaussian. These rows carry no mean difference
  on purpose: MWU tests stochastic dominance, not a difference of means.
  <b>letters</b> shows the two compact-display letters; sharing a letter is
  equivalent to "not significant" and that equivalence is asserted in
  <code>consolidate.py</code>, so this table and the violin plots cannot
  disagree. There is no parametric column to compare against: no ANOVA and no
  Tukey table is computed anywhere in this analysis, so nothing here can be
  quoted as a second opinion on a pair.</p>
  <div class="bar">
    <input placeholder="filter&hellip; e.g. opus xhigh">
    {sel("endpoint", "all endpoints", sorted(pair.endpoint.unique()))}
    <span class="count"></span>
  </div>
  {pair_tab}
</section>

<section id="kw">
  <h2>How much configuration explains, per endpoint</h2>
  <p class="note">Kruskal-Wallis over the eight cells, with
  <b>&epsilon;&sup2; = H/(N&minus;1)</b> &mdash; the share of <b>rank</b>
  variation attributable to configuration. This is the distribution-free
  counterpart of a variance-share table, and it is a transformation of a
  statistic already computed for the letters: <b>no additional test, no
  additional p-value, nothing added to the multiplicity budget</b>. It covers
  all {len(kruskal)} endpoints, where a Gaussian variance decomposition could
  only ever have covered the subset that passed a normality check.
  <b>It is not a decomposition</b> &mdash; Kruskal-Wallis treats the eight cells
  as one unordered factor, so &epsilon;&sup2; cannot be split across model,
  effort, critic and their interactions. That split is the
  <i>Factorial effects</i> tab, and a coefficient with an interval is a more
  useful answer than a variance share.</p>
  <div class="bar">
    <input placeholder="filter&hellip;">
    {sel("endpoint", "all endpoints", sorted(kruskal.endpoint.unique()))}
    <span class="count"></span>
  </div>
  {kruskal_tab}
</section>

{f'''<section id="ix">
  <h2>Interaction multiplicity sensitivity &mdash; {len(ixs)} interaction terms</h2>
  <p class="note" style="max-width:900px"><b>Not prespecified. Not a decision
  rule. It changes no claim anywhere in this report.</b> The multiplicity plan
  was fixed before any p-value was read and it spent the entire error budget on
  main effects: <i>model</i> on PR-AUC as a single unadjusted primary test,
  <i>effort</i> and <i>critic</i> on PR-AUC as a Holm family of two, and every
  other endpoint Holm-adjusted across its own three main effects. The
  {len(ixs)} interaction terms were allocated <b>no error rate</b> and were
  reported CI-only. This tab asks the question that plan does not answer: if an
  error rate <i>had</i> been allocated to those terms, which would survive it.
  Two families are shown &mdash; <b>global</b> (Holm across all {len(ixs)} at
  once) and <b>within-endpoint</b> (Holm across each endpoint's own four,
  mirroring the exploratory main-effect tier) &mdash; because the gap between
  them is what family size costs. Nothing was refitted: every raw p is the
  unadjusted link-scale p from the <i>Factorial effects</i> tab, re-ranked.</p>
  <p class="note" style="max-width:900px"><b>Why this is still not a
  confirmatory claim.</b> Not because the design is underpowered for
  interactions &mdash; against p&nbsp;=&nbsp;1.60&times;10&#8315;&#8309; that
  argument does not hold, and it is not made here. Because the multiplicity
  plan was fixed before any p-value was read, and it allocated these terms no
  error rate. A test chosen after seeing its own p-value is a sensitivity
  analysis whatever it returns, so a surviving term is labelled a
  <i>sensitivity result</i> and never &ldquo;significant&rdquo;.</p>
  <h3>Sorted by raw p, so the Holm step-down chain reads top to bottom</h3>
  <p class="note"><code>global threshold</code> is &alpha;/({len(ixs)}&minus;rank+1),
  the Holm critical value at that step; the chain stops at the first row whose
  raw p exceeds it and every row below is not-surviving regardless of its own p.
  Source: <code>tables/interaction_sensitivity.csv</code>.</p>
  <div class="bar">
    <input placeholder="filter&hellip; e.g. model:critic, usd">
    {sel("endpoint", "all endpoints", sorted(ixs.endpoint.unique()))}
    {sel("order", "all orders", ["two-way", "three-way"])}
    <span class="count"></span>
  </div>
  {ix_tab}
  <p class="note" style="max-width:900px">{ix_note}</p>
  <h3>All {len(allterms)} terms in one schema</h3>
  <p class="note" style="max-width:900px">Main effects and interactions side by
  side: {int((allterms.tier != "interaction").sum())} main-effect rows and
  {int((allterms.tier == "interaction").sum())} interaction rows.
  <code>prespecified p_holm</code> is copied verbatim from the fixed plan and is
  blank for every interaction &mdash; none was ever in one of those families.
  <code>global interaction p_holm</code> is blank for every main effect, for the
  same reason in reverse. The primary test (<code>pr_auc</code> model) is blank
  in both: it was unadjusted by design. <code>p scale</code> records which
  p-value each tier's own procedure consumed &mdash; the prespecified Holm ran
  on the response-scale p, this sensitivity on the link-scale p &mdash; so the
  two are never silently mixed. Source:
  <code>tables/all_terms_multiplicity.csv</code>.</p>
  <div class="bar">
    <input placeholder="filter&hellip; e.g. interaction, primary">
    {sel("endpoint", "all endpoints", sorted(allterms.endpoint.unique()))}
    {sel("tier", "all tiers", ["primary", "secondary", "exploratory",
                               "interaction"])}
    <span class="count"></span>
  </div>
  {at_tab}
</section>''' if ixs is not None else ''}

<section id="fig">
  <h2>Figures</h2>
  <p class="note">Embedded, so this file works offline and survives being
  emailed. Sources are in <code>figures/</code>.</p>
  {"".join(fig_html)}
</section>

<section id="how">
  <h2>How to read this</h2>
  <p class="note" style="max-width:820px">
  <b>Main effects carry a verdict; interactions do not.</b> The multiplicity
  plan was fixed before any p-value was read: model on PR-AUC is primary and
  unadjusted at &alpha;=0.05; effort and critic on PR-AUC are Holm-adjusted
  within that family; every other endpoint is Holm-adjusted across its own three
  main effects; and interactions everywhere are reported CI-only on the link
  scale with no significance verdict. That last rule follows from the allocation
  itself: the plan gave the interaction terms no error rate, so there is no
  threshold against which one could be declared significant. An interaction whose
  interval covers the null is correspondingly not evidence of additivity &mdash;
  the interval stays consistent with a range of non-null values, and the absence
  of a verdict is not a null result. What those terms look like if an error rate
  <i>is</i> allocated to them is reported post hoc in the
  <i>Interaction sensitivity</i> tab, as a sensitivity result and not a decision
  rule.
  </p>
  <p class="note" style="max-width:820px">
  <b>One verdict depends on the testing scale, and is disclosed rather than
  resolved by the result it produces.</b> The effect of model on
  <code>n_turns_total</code> is the only main effect in the study whose verdict
  moves with that choice. Holm-adjusted within its family of three it is
  p&nbsp;=&nbsp;0.0109 under the response-scale test that was implemented and is
  reported throughout, p&nbsp;=&nbsp;0.0669 under the link-scale alternative, and
  p&nbsp;=&nbsp;0.0344 under a model-free stratified randomization test (20,000
  permutations), which does not depend on the scale question at all. The effect
  is best described as <b>real but marginal</b> and is not carried as a
  substantive claim. The alignment of test scale to reported estimand was settled
  after the analysis was run, not prespecified; see METHODS.md
  &sect;&nbsp;<i>Estimands and reporting scale</i>.
  </p>
  <p class="note" style="max-width:820px">
  <b>Why two effect columns.</b> With &minus;1/+1 effect coding a coefficient is
  half the marginal difference, so nothing raw is quoted. betafx reports the
  contrast of the eight predicted cell means; for an interaction under a log
  link that grouping mixes in the main effects (on <code>usd</code> the
  model&times;critic "effect" reads &times;2.12 while its coefficient is 0.0896,
  because the +1 group holds both the cheapest and the most expensive cell). The
  <b>link contrast</b> column is the clean interaction estimand:
  exp(4&times;b) = how many times larger one factor's ratio becomes at the other
  factor's high level.
  </p>
  <p class="note" style="max-width:820px">
  <b>Two questions, two procedures &mdash; and only one of them is parametric.</b>
  The <i>factor</i> effects (all seven terms, every endpoint) come from that
  endpoint's own GLM &mdash; beta for PR-AUC, ROC-AUC and the engineered share,
  gamma for cost and wallclock, Poisson-HC0 for counts, NB2 for tokens and turns.
  Those are the rows you cite, and nothing on this page changes them. The
  <i>cell-vs-cell</i> comparisons that generate the eight letters are a separate,
  descriptive question, and they are answered the same way for every endpoint:
  Kruskal-Wallis, then Mann-Whitney U with Holm over the 28 pairs.
  </p>
  <p class="note" style="max-width:820px">
  <b>Why not route the letters on a normality test.</b> The textbook flowchart
  sends each endpoint down a Gaussian path (ANOVA + Tukey) when its within-cell
  residuals pass Shapiro and Levene. That gate is itself a hypothesis test with
  its own error rate, and at n=10 per cell it has little power to detect the
  departures that would actually matter &mdash; so the letters would silently
  inherit whatever it got wrong, and neighboring panels would answer different
  questions ("which means differ" vs "which cells stochastically dominate").
  Committing to the rank test everywhere costs power where the residuals really
  are Gaussian and buys a display whose validity does not depend on an
  assumption holding. Resolution is not the constraint: at n=10 vs 10 the
  smallest attainable two-sided MWU p is 2/C(20,10) = 1.08&times;10&#8315;&#8309;,
  leaving 3.0&times;10&#8315;&#8308; after the worst Holm multiplier. No Gaussian
  model is fitted anywhere in this analysis: <b>a procedure that will not be
  reported is not computed</b>, because leaving one in an output file is an
  invitation to quote it.
  </p>
  <p class="note" style="max-width:820px">
  <b>Letters.</b> Groups sharing a letter are NOT significantly different.
  Letters are named left to right in the figures' x-order, which is sorted by
  factor level and never by observed value, so <code>a</code> is always the
  leftmost violin and a position means the same thing in every figure.
  </p>
  <p class="note" style="max-width:820px">
  <b>Provenance.</b> Rebuild with
  <code>python analysis.py &amp;&amp; python figures.py &amp;&amp; python
  consolidate.py</code>. Full narrative in <code>FINDINGS.md</code>; method
  transfer notes in <code>HANDOFF_FOR_AI.md</code>.
  </p>
</section>
</div>
<script>{JS}
wire('tEff'); wire('tPair');
{"wire('tIx'); wire('tAll');" if ixs is not None else ""}
wire('tAov');
</script></html>"""
    return body


# ==========================================================================
# 5. the same three tables as a Markdown appendix, spliced into FINDINGS.md
# ==========================================================================

MARK_A = "<!-- BEGIN CONSOLIDATED APPENDIX (generated by consolidate.py) -->"
MARK_B = "<!-- END CONSOLIDATED APPENDIX -->"


def md_table(rows, head, align=None):
    align = align or ["---"] * len(head)
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(align) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def ci(lo, hi, s=3):
    return f"({fmt(lo, s)}, {fmt(hi, s)})"


def appendix(eff, pair, kruskal, ixs=None, allterms=None) -> str:
    L, R = ["\n# Appendix A — Every comparison, consolidated\n"], None
    L.append(
        "Generated by `consolidate.py`; do not hand-edit between the markers. "
        "Machine-readable twins: `tables/all_effects.csv` (77 rows) and "
        "`tables/all_pairwise.csv` (308 rows). Interactive version with sort "
        "and filter: **`REPORT.html`** — open it in a browser.\n")

    # ---- A.1 all seven terms, per endpoint --------------------------------
    L.append("\n## A.1 All seven factorial terms, endpoint by endpoint\n")
    L.append(
        "`link contrast` is the effect-coded coefficient scaled to the contrast "
        "it represents (×2 main, ×4 two-way, ×8 three-way) and `exp` is its "
        "response-scale reading — a ratio under a log link, an odds ratio under "
        "logit. This is the interaction estimand §4.3 specifies. "
        "`marginal effect` is betafx's eight-cell contrast: the estimand you "
        "want for a **main effect**, but confounded with the main effects for "
        "an interaction (see the note under A.2), so it is shown for provenance "
        "only. Main effects carry a verdict; interactions are CI-only.\n"
        "\n`p (tested)` is the p-value the term was actually tested on, and the "
        "scale follows the estimand: main effects on the response-scale marginal "
        "contrast (the `marginal effect` column, and the quantity its CI is built "
        "on), interactions on the link-scale coefficient. `verdict` is derived "
        "from this column, so the two always agree. Both raw p-values are kept "
        "side by side in `tables/all_effects.csv` as `p_link` and `p_response`; "
        "exactly one main effect in the study changes verdict between them "
        "(`n_turns_total` model — see §3.10 and METHODS.md § Multiplicity).\n")
    for key, g in eff.groupby("endpoint", sort=False):
        e0 = g.iloc[0]
        L.append(f"\n**{e0.endpoint_label}** — `{key}` · {e0.family} "
                 f"({e0.link} link) · {e0.unit} · {e0.endpoint_tier}\n")
        exp_hdr = "exp(contrast)" if e0.link in ("log", "logit") else "—"
        rows = []
        for r in g.itertuples():
            expcol = ("—" if pd.isna(r.link_exp) else
                      f"{fmt(r.link_exp)} {ci(r.link_exp_lo, r.link_exp_hi)}")
            verdict = ("**significant**" if r.verdict == "significant"
                       else "n.s." if r.verdict == "not significant"
                       else ("CI excludes null" if r.ci_excludes_null
                             else "CI spans null"))
            rows.append([
                r.label, r.order,
                f"{fmt(r.link_contrast)} {ci(r.link_lo, r.link_hi)}",
                expcol,
                f"{fmt(r.effect)} {ci(r.eff_lo, r.eff_hi)}",
                fmt(r.p_test), verdict])
        L.append(md_table(rows,
                          ["term", "order", "link contrast (95% CI)", exp_hdr,
                           "marginal effect (95% CI)", "p (tested)", "verdict"],
                          ["---", "---", "---:", "---:", "---:", "---:", "---"]))
        L.append("")

    # ---- A.2 the interactions that actually move ---------------------------
    ix = eff[(eff.order != "main") & eff.ci_excludes_null]
    L.append("\n## A.2 Interactions whose link-scale CI clears the null\n")
    L.append(
        f"{len(ix)} of {len(eff[eff.order != 'main'])} interaction terms. "
        "**No significance verdict attaches to any of these** — the multiplicity "
        "plan was fixed before any p-value was read and allocated the "
        "interaction terms no error rate, so no threshold exists against which "
        "one could be declared significant. This table is a list of leads, not "
        "of findings, and a term absent from it is not evidence of additivity: "
        "the interval stays consistent with a range of non-null values. What "
        "these terms look like if an error rate *is* allocated is reported post "
        "hoc in A.6.\n")
    rows = [[r.endpoint_label, r.label, r.order,
             f"{fmt(r.link_contrast)} {ci(r.link_lo, r.link_hi)}",
             "—" if pd.isna(r.link_exp) else
             f"{fmt(r.link_exp)} {ci(r.link_exp_lo, r.link_exp_hi)}",
             r.link_meaning, fmt(r.p_link)]
            for r in ix.itertuples()]
    L.append(md_table(rows, ["endpoint", "term", "order",
                             "link contrast (95% CI)", "exp (95% CI)",
                             "what exp means", "p (link)"],
                      ["---", "---", "---", "---:", "---:", "---", "---:"]))
    L.append(
        "\n> **Why the two effect columns can disagree in direction.** On "
        "`usd`, model×critic has coefficient +0.0896 but betafx's marginal "
        "`effect` reads ×2.12. betafx groups the eight predicted cell means by "
        "the sign of the term's contrast column; for model×critic the +1 group "
        "is {sonnet/off, opus/on}, which holds both the cheapest cell ($1.07) "
        "and the most expensive ($16.53), so its arithmetic mean is driven by "
        "the *main* effects. The clean reading is exp(4 × 0.0896) = **×1.43**: "
        "the opus-over-sonnet cost ratio is 1.43× larger with the critic on "
        "than off. Checks out against the raw medians — $2.97/$1.04 = 2.87 with "
        "the critic off, $12.20/$2.66 = 4.59 with it on, and 4.59/2.87 = 1.60.\n")

    # ---- A.3 rank-based omnibus effect size --------------------------------
    L.append("\n## A.3 How much configuration explains, per endpoint "
             "(rank-based)\n")
    L.append(
        "Kruskal-Wallis over the eight cells, with ε² = H/(N−1): the share of "
        "**rank** variation attributable to configuration. This is the "
        "distribution-free counterpart of a variance-share table, and it is a "
        "transformation of a statistic already computed for the letters — no "
        "additional test, no additional p-value, nothing added to the "
        "multiplicity budget. It covers all "
        f"{len(kruskal)} endpoints, where a Gaussian variance decomposition "
        "could only ever have covered the subset whose residuals passed a "
        "normality check.\n\n"
        "It is **not** a decomposition: Kruskal-Wallis treats the eight cells "
        "as one unordered factor, so ε² cannot be split across model, effort, "
        "critic and their interactions. That split is what §A.1 is for, and a "
        "coefficient with an interval is a more useful answer than a variance "
        "share anyway.\n")
    krows = [[r.endpoint_label, fmt(r.H, 4), str(int(r.df)),
              fmt(r.p, 3), fmt(r.epsilon_sq, 3), fmt(r.eta_sq_H, 3)]
             for r in kruskal.itertuples()]
    L.append(md_table(krows,
                      ["endpoint", "H", "df", "p", "ε²", "η²_H"],
                      ["---", "---:", "---:", "---:", "---:", "---:"]))

    # ---- A.4 the letter matrix --------------------------------------------
    L.append("\n## A.4 Compact letters for all eight cells, every endpoint\n")
    L.append(
        "Cells sharing a letter are **not** significantly different. Columns "
        "are in the figures' left-to-right x-order (sorted by factor level, "
        "never by observed value), which is also the order the letters are "
        "named in — so `a` is always the leftmost cell. Every row comes from "
        "the same procedure — Mann-Whitney U with Holm over that endpoint's 28 "
        "pairs — so a letter means the same thing in every figure. The "
        "letter/significance equivalence is asserted over all 308 pairs in "
        "`consolidate.py`.\n")
    idx = pd.read_csv(TABLES / "endpoint_index.csv").set_index("key")
    signs = RES["sign_order"]
    hdr = [SIGN_LABEL[s].replace(" / ", "<br>") for s in signs]
    rows = []
    for key in idx.index:
        r = RES["results"].get(key, {})
        if "letters" not in r:
            continue
        lt = r["letters"]["cell"]
        rows.append([idx.loc[key, "label"]] + [lt.get(s, "") for s in signs])
    L.append(md_table(rows, ["endpoint"] + hdr,
                      ["---"] + [":-:"] * len(signs)))
    L.append(
        "\nThe letters are distribution-free for **every** endpoint: "
        "Kruskal-Wallis, then Mann-Whitney U with Holm over the 28 pairs. No "
        "endpoint is routed to a parametric post-hoc, so no panel's letters "
        "depend on a normality test that has little power to fail at n = 10 "
        "per cell, and neighboring panels never answer different questions. "
        "The price of that choice is power where the residuals really are "
        "Gaussian, paid deliberately and paid on every endpoint alike. The "
        "p-values in A.1 come from each endpoint's GLM and are untouched by "
        "any of this.\n")

    # ---- A.5 where the rest lives -----------------------------------------
    L.append("\n## A.5 The 308 pairwise rows\n")
    L.append(
        "Too long to inline. `tables/all_pairwise.csv` holds every cell-vs-cell "
        "comparison for all 11 endpoints in one schema — endpoint, the two "
        "cells, their letters, raw and Holm-adjusted p, and the verdict. No "
        "row carries a mean difference in the `estimate` column, because no "
        "row is a test of means: MWU tests stochastic dominance, and supplying "
        "a point estimate for it invites exactly the misreading documented in "
        "§3.3. Sort and filter it in `REPORT.html`.\n")

    # ---- A.6 interaction multiplicity sensitivity -------------------------
    if ixs is not None:
        n_s = int(ixs.survives_global.sum())
        surv = ixs[ixs.survives_global]
        stop = ixs[~ixs.survives_global].iloc[0]
        terms = sorted(set(surv.label))
        L.append("\n## A.6 Interaction multiplicity sensitivity "
                 "(NOT prespecified)\n")
        L.append(
            "**Not prespecified, not a decision rule, and it changes no claim "
            "in this document.** The plan in §4.4 was fixed before any p-value "
            f"was read and it allocated the {len(ixs)} interaction terms **no "
            "error rate**; they are reported CI-only in §4.3 and A.2 and that "
            "does not change. This section answers a different question: *if* "
            f"an error rate had been allocated to those {len(ixs)} terms, which "
            "would survive it. Two families are reported — **global** (Holm "
            f"across all {len(ixs)} at once) and **within-endpoint** (Holm "
            "across each endpoint's own four, mirroring the exploratory "
            "main-effect tier) — because the gap between them is what family "
            "size costs. Nothing was refitted; every raw p is the unadjusted "
            "link-scale p from A.1, re-ranked. Full write-up with both tables "
            "in full: **`INTERACTION_SENSITIVITY.md`**, machine-readable twins "
            "`tables/interaction_sensitivity.csv` and "
            "`tables/all_terms_multiplicity.csv`, interactive version in the "
            "*Interaction sensitivity* tab of **`REPORT.html`**.\n")
        L.append(
            "**Why this is still not a confirmatory claim.** Not because a "
            "2×2×2 with n = 10 is underpowered for interactions — against "
            "p = 1.60e-05 that argument does not hold, and it is not made "
            "here. Because the multiplicity plan was fixed before any p-value "
            "was read and allocated these terms no error rate. A test chosen "
            "after seeing its own p-value is a sensitivity analysis whatever "
            "it returns.\n")
        L.append(f"\n**Top of the global step-down chain** (all {len(ixs)} rows "
                 "in the CSV and in `INTERACTION_SENSITIVITY.md`):\n")
        head = ixs.head(6)
        rows = [[str(int(r.holm_rank)), r.endpoint, r.label,
                 f"{fmt(r.link_exp)} {ci(r.link_exp_lo, r.link_exp_hi)}",
                 f"{r.p_raw:.2e}", f"{r.global_threshold:.2e}",
                 f"{r.p_holm_global:.2e}", f"{r.p_holm_within:.2e}",
                 "**yes**" if r.survives_global else "no"]
                for r in head.itertuples()]
        L.append(md_table(rows, ["rank", "endpoint", "term",
                                 "exp(contrast) (95% CI)", "raw p",
                                 "global threshold", "global p_holm",
                                 "within-endpoint p_holm", "survives global"],
                          ["---:", "---", "---", "---:", "---:", "---:",
                           "---:", "---:", "---"]))
        L.append(
            f"\n{n_s} of {len(ixs)} terms clear the global Holm at α = 0.05: "
            + ", ".join(f"`{r.endpoint}` ({r.p_raw:.2e})"
                        for r in surv.itertuples())
            + f". The chain stops at rank {int(stop.holm_rank)}, "
            f"`{stop.endpoint}` {stop.label}, whose raw p = {stop.p_raw:.2e} "
            f"exceeds its threshold α/{len(ixs) - int(stop.holm_rank) + 1} = "
            f"{stop.global_threshold:.2e}.\n")
        L.append(
            f"**These are not {n_s} findings. They are one term — "
            f"`{terms[0]}` — on three correlated resource endpoints.** `usd` "
            "and `wallclock_min` correlate at r = 0.91, and both are largely "
            "downstream of `total_tokens`. Counting them as three independent "
            "survivors would triple-count a single resource-side pattern, the "
            "one already reported in §4.3: the critic costs disproportionately "
            "more on opus. It remains a sensitivity result and carries no "
            "confirmatory claim.\n")
        L.append(
            f"The within-endpoint family clears "
            f"{int(ixs.survives_within.sum())} of {len(ixs)} terms against "
            f"{n_s} under the global family. Neither number is more correct — "
            "they answer different questions — and neither is a decision rule "
            "here.\n")
    return "\n".join(L)


def splice_findings(text):
    p = HERE / "FINDINGS.md"
    s = p.read_text(encoding="utf-8")
    block = f"{MARK_A}\n{text}\n{MARK_B}"
    if MARK_A in s:
        a, rest = s.split(MARK_A, 1)
        _, b = rest.split(MARK_B, 1)
        s = a + block + b
    else:
        # land it before the closing citation rule so the footer stays last
        cut = s.rindex("\n---\n")
        s = s[:cut] + "\n" + block + "\n" + s[cut:]
    p.write_text(s, encoding="utf-8")
    return p


def main():
    eff, pair, kw = all_effects(), all_pairwise(), all_kruskal()
    ixs, allterms = interaction_sensitivity()
    eff.to_csv(TABLES / "all_effects.csv", index=False)
    pair.to_csv(TABLES / "all_pairwise.csv", index=False)
    kw.to_csv(TABLES / "all_kruskal.csv", index=False)
    out = HERE / "REPORT.html"
    out.write_text(render(eff, pair, kw, ixs, allterms), encoding="utf-8")
    md = splice_findings(appendix(eff, pair, kw, ixs, allterms))

    print(f"  tables/all_effects.csv    {len(eff):>4} rows "
          f"({eff.endpoint.nunique()} endpoints x 7 terms)")
    print(f"  tables/all_pairwise.csv   {len(pair):>4} rows "
          f"({pair.endpoint.nunique()} endpoints x 28 pairs)")
    print(f"  tables/all_kruskal.csv    {len(kw):>4} rows "
          f"(rank omnibus + epsilon^2, one per endpoint)")
    if ixs is not None:
        print(f"  tables/interaction_sensitivity.csv  {len(ixs):>4} rows "
              f"(read only; sensitivity, not a decision rule)")
        print(f"  tables/all_terms_multiplicity.csv   {len(allterms):>4} rows "
              f"(read only)")
    print(f"  REPORT.html               {out.stat().st_size / 1e6:.1f} MB "
          f"self-contained")
    print(f"  {md.name} <- Appendix A spliced between markers")
    return eff, pair, kw


if __name__ == "__main__":
    main()
