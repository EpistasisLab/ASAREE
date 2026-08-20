#!/usr/bin/env python
"""
figures.py -- violin plots with compact letter displays, plus the deployment
and effect-size views, for the 2x2x2 factorial analysis.

Reads only what analysis.py wrote (tables/analysis_frame.csv and results.json),
so the plotting layer cannot invent a number the statistics layer did not
produce. That separation is the point: if a letter appears on a violin, it came
out of a named pairwise procedure, not out of the plotting code.

VISUAL DESIGN RULES APPLIED (all computable ones were computed, not eyeballed)
=============================================================================
  * Two categorical hues only -- blue #2a78d6 and orange #eb6834. Validated
    with validate_palette.py (the Python twin of the design system's
    validate_palette.js, verified against its documented outputs):
        all-pairs CVD dE 24.7 (protan), normal-vision dE 33.6, contrast >= 3:1
        -> ALL CHECKS PASS in light mode, no relief needed.
  * Color carries IDENTITY, never magnitude: blue is always the factor's
    low/baseline level, orange always its high level. Never a value-ramp on
    nominal categories.
  * Identity is never color-alone -- every violin is directly labeled on the
    x-axis and every multi-series panel carries a legend.
  * Thin marks, hairline solid grid one shade off the surface, no dashes, no
    box around the plot, generous padding.
  * Every raw observation is drawn (n = 10 per cell). At this sample size a
    bare KDE is a decoration; the reader must see the ten points. Kernel
    density is clipped to the observed range so the violin never implies data
    outside it.
  * A table view exists for every figure: tables/*.csv and FINDINGS.md.

Run:  python figures.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scipy import stats

HERE = Path(__file__).resolve().parent
FIGS = HERE / "figures"
FIGS.mkdir(parents=True, exist_ok=True)

# ---- design tokens (from the validated reference palette) -----------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
LO_HUE = "#2a78d6"        # categorical slot 1 -- factor low level
HI_HUE = "#eb6834"        # categorical slot 2 -- factor high level

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
    "font.size": 9,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
    "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "xtick.major.size": 0, "ytick.major.size": 3,
    "xtick.major.width": 0.8, "ytick.major.width": 0.8,
    "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
    "legend.frameon": False, "legend.fontsize": 8,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 130, "savefig.dpi": 220, "savefig.bbox": "tight",
})

DATA = pd.read_csv(HERE / "tables" / "analysis_frame.csv")
RES = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
R = RES["results"]
LEVEL_NAMES = {k: tuple(v) for k, v in RES["level_names"].items()}
FACTOR_LABEL = RES["factor_labels"]

# The eight configurations in FACTOR order, not value order: model, then
# critic, then effort, so the left half is every sonnet cell and the right half
# every opus cell, both halves repeating the same internal sequence. Read from
# results.json rather than re-declared here -- analysis.py names the letters in
# this order ('a' = leftmost), so a second copy that drifted would put 'a' on
# the wrong violin. Fixed order, never re-sorted per panel.
SIGN_ORDER = RES["sign_order"]
SIGN_LABEL = {s: "\n".join([
    LEVEL_NAMES["A"][s[0] == "+"], LEVEL_NAMES["B"][s[1] == "+"],
    "critic " + LEVEL_NAMES["C"][s[2] == "+"]]) for s in SIGN_ORDER}

# Which endpoint panels get a log y-axis. The log-link families (gamma, negbin,
# poisson) are the technically-motivated candidates -- their effects are ratios,
# and a log axis makes a constant ratio a constant distance. It is OFF by
# request: the violins are read on the axis the data are measured in, so the
# right-skew of cost and time is visible rather than straightened out. The
# ratios themselves are still estimated on the log scale by the model and are
# unaffected; only the drawing changes.
#
# The FORESTS and the PARETO x-axes stay logarithmic and are a separate case:
# a forest of ratios must be log or x1.5 and x0.667 look like different-sized
# effects, and the Pareto panels span 15x in cost.
LOG_FAMILIES = frozenset()


# ==========================================================================
# violin primitive
# ==========================================================================

def violin(ax, x, values, color, width=0.34, log=False):
    """
    One violin: clipped kernel density + the raw observations + median/IQR.

    The density is evaluated only between min(values) and max(values), so the
    silhouette never suggests values that were not observed -- important here,
    where PR-AUC is bounded and the counts are discrete. On a log axis the
    kernel is fitted in log space, otherwise the density of a right-skewed
    quantity is smeared by the axis transform.
    """
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    work = np.log(v) if log else v
    lo, hi = work.min(), work.max()

    if len(np.unique(work)) > 2 and np.ptp(work) > 0:
        grid = np.linspace(lo, hi, 200)
        try:
            dens = stats.gaussian_kde(work, bw_method=0.55)(grid)
        except np.linalg.LinAlgError:
            dens = np.ones_like(grid)
        dens = dens / dens.max() * width
        gy = np.exp(grid) if log else grid
        ax.fill_betweenx(gy, x - dens, x + dens, facecolor=color, alpha=0.20,
                         linewidth=0, zorder=2)
        ax.plot(np.concatenate([x - dens, (x + dens)[::-1]]),
                np.concatenate([gy, gy[::-1]]), color=color, linewidth=1.2,
                zorder=3, solid_joinstyle="round")

    # raw observations: deterministic jitter (seeded) so the figure is
    # reproducible; 2px surface ring so overlapping points stay countable
    rng = np.random.default_rng(abs(hash(f"{x}{len(v)}{v.sum():.6f}")) % 2**32)
    jit = rng.uniform(-width * 0.42, width * 0.42, size=len(v))
    ax.scatter(x + jit, v, s=13, facecolor=color, edgecolor=SURFACE,
               linewidth=1.0, alpha=0.85, zorder=5)

    q1, med, q3 = np.percentile(v, [25, 50, 75])
    ax.plot([x, x], [q1, q3], color=INK, linewidth=2.0, solid_capstyle="round",
            zorder=6, alpha=0.75)
    ax.plot([x - width * 0.55, x + width * 0.55], [med, med], color=INK,
            linewidth=2.0, solid_capstyle="round", zorder=7)


def headroom(ax, log=False, frac=0.17):
    """
    Open a clear band at the top of the panel for the letter display.

    Without this the letters land on the tallest violin and become unreadable
    exactly where the reader most needs them -- a label colliding with a mark
    is a defect, not a styling preference.
    """
    lo, hi = ax.get_ylim()
    if log:
        llo, lhi = np.log10(lo), np.log10(hi)
        ax.set_ylim(lo, 10 ** (lhi + frac * (lhi - llo)))
    else:
        ax.set_ylim(lo, hi + frac * (hi - lo))


def put_letters(ax, xs, groups, letters, log=False, grow=True):
    """
    Compact letter display, drawn above every violin at a common height.

    `grow=False` when several panels share a y-axis: the headroom must then be
    opened exactly once, or each panel's call compounds the previous one's.
    """
    if not letters:
        return
    if grow:
        headroom(ax, log)
    lo, hi = ax.get_ylim()
    y = (10 ** (np.log10(hi) - 0.012 * (np.log10(hi) - np.log10(lo)))
         if log else hi - 0.012 * (hi - lo))
    for x, g in zip(xs, groups):
        ax.text(x, y, letters.get(g, ""), ha="center", va="top", color=INK,
                fontsize=10, fontweight="600")


def _tick(v, _=None):
    """Readable tick text: 1.5, 20, 500, 2.5k, 1.2M -- never 4 x 10^1.

    Applied to LINEAR axes too, not just log ones. Matplotlib's default for a
    large linear range is a shared "1e6" multiplier parked in the axis corner,
    where on these figures it lands on top of the panel's effect line. Writing
    the magnitude into each tick removes the offset text entirely, and "1.5M"
    is what a reader can act on without doing arithmetic in their head.
    """
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:g}M"
    if a >= 1e3:
        return f"{v / 1e3:g}k"
    return f"{v:g}"


def style(ax, log=False):
    ax.grid(axis="y", zorder=0)
    ax.set_axisbelow(True)
    if log:
        # A log axis defaults to decade ticks, which on a 3-fold range leaves
        # two labels and on a wide range prints "4 x 10^1". Label plain-numeral
        # subdivisions instead, at a density set by how many decades the data
        # actually span -- 1-2-5 over a wide range, finer when the range is
        # narrow enough that 1-2-5 would leave a nearly bare axis.
        lo, hi = ax.get_ylim()
        span = math.log10(max(hi, 1e-12) / max(lo, 1e-12))
        subs = ((1, 2, 5) if span >= 1.2 else
                (1, 1.5, 2, 3, 5, 7) if span >= 0.6 else
                (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8))
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(
            matplotlib.ticker.LogLocator(base=10, subs=subs, numticks=15))
        ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_tick))
    ax.margins(x=0.12)


# ==========================================================================
# figure 1..N : one per endpoint
# ==========================================================================

def endpoint_figure(key, idx):
    e = R[key]
    if "effects" not in e:
        return None
    fam = e["family"]
    log = fam in LOG_FAMILIES
    eff = pd.DataFrame(e["effects"]).set_index("term")
    letters = e["letters"]
    unit = e["unit"]

    fig = plt.figure(figsize=(13.6, 4.6))
    gs = fig.add_gridspec(1, 4, width_ratios=[1, 1, 1, 3.6], wspace=0.30,
                          left=0.055, right=0.995, top=0.80, bottom=0.20)

    # ---- panels 1-3: one factor each ------------------------------------
    # Shared y-axis: the four panels show the same quantity, so an independent
    # scale per panel would let a tiny effect look like a large one.
    first, pending = None, []
    for i, f in enumerate(("A", "B", "C")):
        ax = fig.add_subplot(gs[0, i], sharey=first)
        first = first or ax
        if i:
            ax.tick_params(labelleft=False)
        levels = LEVEL_NAMES[f]
        for j, lv in enumerate(levels):
            violin(ax, j, DATA.loc[DATA[f] == lv, key], (LO_HUE, HI_HUE)[j],
                   log=log)
        style(ax, log)
        ax.set_xticks([0, 1], levels)
        ax.set_xlim(-0.6, 1.6)
        if i == 0:
            ax.set_ylabel(f"{e['label']}  ({unit})")
        pending.append((ax, [0, 1], list(levels), letters.get(f)))

        row = eff.loc[f]
        p = letters.get(f"{f}_p", row["p"])
        star = "" if p >= 0.05 else "  *"
        if fam in ("beta", "gaussian"):
            txt = f"{row['effect']:+.4g} [{row['eff_lo']:+.3g}, {row['eff_hi']:+.3g}]"
        else:
            txt = f"x{row['effect']:.3g} [{row['eff_lo']:.3g}, {row['eff_hi']:.3g}]"
        ax.set_title(f"{FACTOR_LABEL[f]}{star}", color=INK, fontsize=10.5,
                     pad=16, loc="left")
        ax.text(0, 1.015, f"{txt}   p={p:.3g}", transform=ax.transAxes,
                fontsize=7.6, color=INK2, ha="left", va="bottom")

    # ---- panel 4: all eight configurations ------------------------------
    ax = fig.add_subplot(gs[0, 3], sharey=first)
    for j, s in enumerate(SIGN_ORDER):
        violin(ax, j, DATA.loc[DATA["sign"] == s, key],
               HI_HUE if s[0] == "+" else LO_HUE, width=0.36, log=log)
    style(ax, log)
    ax.set_xticks(range(8), [SIGN_LABEL[s] for s in SIGN_ORDER], fontsize=7.4)
    ax.set_xlim(-0.7, 7.7)
    ax.tick_params(labelleft=False)
    pending.append((ax, list(range(8)), SIGN_ORDER, letters.get("cell")))

    headroom(first, log)                      # once, for the shared axis
    for pax, xs, groups, lets in pending:
        put_letters(pax, xs, groups, lets, log=log, grow=False)

    ax.set_title("all eight configurations", color=INK, fontsize=10.5, pad=16,
                 loc="left")
    ax.text(0, 1.015, e["letters_method"], transform=ax.transAxes,
            fontsize=7.6, color=INK2, ha="left", va="bottom")
    ax.legend(handles=[Patch(facecolor=LO_HUE, alpha=0.55, edgecolor=LO_HUE,
                             label="sonnet"),
                       Patch(facecolor=HI_HUE, alpha=0.55, edgecolor=HI_HUE,
                             label="opus")],
              loc="upper right", ncol=2, bbox_to_anchor=(1.0, 1.16))

    scale = ("log scale" if log else "linear scale")
    fig.suptitle(f"{e['label']}   ({fam} model, {scale})", x=0.055, y=0.965,
                 ha="left", fontsize=13, color=INK, fontweight="600")
    fig.text(0.055, 0.045,
             "Violins show the clipped kernel density; every run is plotted "
             "(n = 10 per cell). Bar = IQR, wide tick = median.  "
             "Letters: groups sharing a letter are NOT significantly "
             "different.  * = significant after the prespecified adjustment.",
             fontsize=7.6, color=MUTED, ha="left")
    out = FIGS / f"fig_{idx:02d}_{key}.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ==========================================================================
# deployment view: performance against cost
# ==========================================================================

def declutter(fig, texts, step=5.0, pad=2.0, passes=10):
    """Push offset-annotations apart until no two rendered boxes overlap.

    Alternating labels above/below their point gets most of the way there, but
    on a log axis two neighbours can still touch. Rather than hand-tuning
    offsets per point -- which silently breaks the moment the data changes --
    measure: draw, read each text's real bounding box, and push the offender
    further along the direction it already sits. Everything here is geometry
    the reader can see, so it transfers to any figure with point labels.
    """
    flipped = set()
    for _ in range(passes):
        fig.canvas.draw()
        boxes = [t.get_window_extent().padded(pad) for t in texts]
        clash = [(i, j) for i in range(len(texts))
                 for j in range(i + 1, len(texts))
                 if boxes[i].overlaps(boxes[j])]
        if not clash:
            return True
        for i, j in clash:                       # move the later of each pair
            ox, oy = texts[j].xyann
            up = oy >= 0
            # only push j further out if that is AWAY from i; a label sitting
            # below its neighbour and pushed upward just climbs into it, which
            # is how naive repulsion loops fail to converge
            away = (boxes[j].y0 + boxes[j].y1) >= (boxes[i].y0 + boxes[i].y1)
            if up == away:
                texts[j].xyann = (ox, oy + (step if up else -step))
            elif j not in flipped:               # send it to the other side
                flipped.add(j)
                texts[j].xyann = (ox, -13.0 if up else 13.0)
                texts[j].set_va("top" if up else "bottom")
    fig.canvas.draw()
    return False


def pareto_figure():
    agg = (DATA.groupby("sign")
           .agg(pr=("pr_auc", "mean"), se=("pr_auc", lambda v: v.std(ddof=1)
                                           / np.sqrt(len(v))),
                usd=("usd", "mean"), mins=("wallclock_min", "mean"))
           .reindex(SIGN_ORDER).reset_index())

    # two short lines rather than one long one: a centered label half as wide is
    # what keeps eight annotations from colliding on a log axis
    short = {s: SIGN_LABEL[s].replace("\n", " / ", 1) for s in SIGN_ORDER}
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.4))
    n_front, panels = 0, []
    for ax, xcol, xlab in ((axes[0], "usd", "mean cost per run (USD, log scale)"),
                           (axes[1], "mins",
                            "mean wall-clock time (minutes, log scale)")):
        x = agg[xcol].to_numpy()
        y = agg["pr"].to_numpy()
        order = np.argsort(x)
        keep, best = [], -np.inf
        for i in order:
            if y[i] > best:
                keep.append(i)
                best = y[i]
        n_front = max(n_front, len(keep))

        # The frontier is a staircase, and here it collapses to a single point:
        # the cheapest configuration is also the most accurate, so nothing
        # dominates it and no segment exists to draw. Drawing a line through
        # one point silently renders nothing, which would read as "no frontier
        # computed"; the ring plus the reference rule says what is true.
        ax.axhline(max(y[i] for i in keep), color=MUTED, linewidth=1.0,
                   zorder=1)
        if len(keep) > 1:
            ax.plot(x[keep], y[keep], color=MUTED, linewidth=1.4, zorder=2,
                    solid_capstyle="round")
        ax.errorbar(x, y, yerr=agg["se"], fmt="none", ecolor=AXIS,
                    elinewidth=1.2, capsize=0, zorder=3)

        # labels alternate above / below in x order, then declutter measures
        labels = []
        for rank, i in enumerate(order):
            s = agg["sign"][i]
            if i in keep:
                ax.scatter(x[i], y[i], s=200, facecolor="none",
                           edgecolor=MUTED, linewidth=1.4, zorder=4)
            ax.scatter(x[i], y[i], s=70, zorder=5, linewidth=1.6,
                       facecolor=HI_HUE if s[0] == "+" else LO_HUE,
                       edgecolor=SURFACE)
            up = rank % 2 == 0
            labels.append(ax.annotate(
                short[s], (x[i], y[i]), textcoords="offset points",
                xytext=(0, 13 if up else -13), ha="center",
                va="bottom" if up else "top", fontsize=7.4, color=INK2))
        panels.append(labels)
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(
            matplotlib.ticker.LogLocator(base=10, subs=(1, 2, 5), numticks=15))
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_tick))
        ax.grid(zorder=0)
        ax.set_axisbelow(True)
        ax.set_xlabel(xlab)
        ax.margins(x=0.30, y=0.24)
    axes[0].set_ylabel("mean held-out PR-AUC")
    axes[0].legend(handles=[
        Line2D([], [], marker="o", linestyle="", markerfacecolor=LO_HUE,
               markeredgecolor=SURFACE, markersize=8, label="sonnet"),
        Line2D([], [], marker="o", linestyle="", markerfacecolor=HI_HUE,
               markeredgecolor=SURFACE, markersize=8, label="opus"),
        Line2D([], [], marker="o", linestyle="", markerfacecolor="none",
               markeredgecolor=MUTED, markersize=11,
               label="Pareto-optimal")], loc="lower left", ncol=3)
    fig.tight_layout(rect=(0.005, 0.02, 1, 0.86))
    fig.text(0.045, 0.955, "Is any of the extra spend buying accuracy?",
             ha="left", fontsize=13.5, color=INK, fontweight="600")
    fig.text(0.045, 0.895,
             "Cell means with +/-1 SE (n = 10 runs each). The Pareto frontier "
             f"is {n_front} point: the cheapest, fastest configuration also "
             "has the highest mean PR-AUC,\nso no other configuration is worth "
             "its extra spend. The rule marks that best mean.",
             fontsize=8.6, color=INK2, ha="left", va="top")
    for labels in panels:                # after layout: boxes are final now
        if not declutter(fig, labels):
            print("  ! pareto labels still overlap after declutter")
    out = FIGS / "fig_20_pareto.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ==========================================================================
# effect-size forest
# ==========================================================================

def _forest_rows(keys):
    rows = []
    for k in keys:
        eff = pd.DataFrame(R[k]["effects"]).set_index("term")
        for f in ("A", "B", "C"):
            r = eff.loc[f]
            rows.append(dict(endpoint=R[k]["label"], factor=FACTOR_LABEL[f],
                             effect=r["effect"], lo=r["eff_lo"], hi=r["eff_hi"],
                             sig=bool(R[k]["letters"].get(f"{f}_p", 1) < 0.05),
                             structural=bool(r.get("structural", False))))
    return pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)


def forest_figure(keys, name, title, ratio, groups=None):
    """
    One row per (endpoint, factor) of the same estimand type. Ratio endpoints
    and difference endpoints are NEVER put on one axis: they are different
    units and a shared axis would invent a comparison.

    `groups` facets into small multiples with independent x-axes. That is the
    fix when endpoints share a unit but not a magnitude -- here a +0.07 shift
    in a proportion would squash the +0.005 PR-AUC effects into the zero line,
    and a second y-axis (the usual temptation) is never the answer.
    """
    groups = groups or [(None, keys)]
    tabs = [_forest_rows(ks) for _, ks in groups]
    nrow = max(len(t) for t in tabs)
    fig, axes = plt.subplots(
        1, len(groups), squeeze=False,
        figsize=(9.2 if len(groups) == 1 else 6.9 * len(groups),
                 0.42 * nrow + 2.0))
    for ax, (sub, _), t in zip(axes[0], groups, tabs):
        off = nrow - len(t)          # top-align a short panel under its header
        ax.axvline(1 if ratio else 0, color=INK2, linewidth=1.2, zorder=2)
        for i, r in t.iterrows():
            ax.plot([r.lo, r.hi], [i + off, i + off], color=LO_HUE,
                    linewidth=2.0, solid_capstyle="round", alpha=0.9, zorder=3)
            ax.scatter(r.effect, i + off, s=52,
                       facecolor=LO_HUE if r.sig else SURFACE,
                       edgecolor=LO_HUE, linewidth=1.6, zorder=4)
            if r.structural:
                ax.text(ax.get_xlim()[1], i + off, "  price-linked",
                        va="center", fontsize=7, color=MUTED)
        ax.set_yticks(np.arange(len(t)) + off,
                      [f"{r.endpoint}  -  {r.factor}" for r in t.itertuples()],
                      fontsize=8)
        if ratio:
            ax.set_xscale("log")
            ax.xaxis.set_major_locator(matplotlib.ticker.LogLocator(
                base=10, subs=(1, 1.5, 2, 3, 4, 5, 7), numticks=15))
            ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
            ax.xaxis.set_major_formatter(
                matplotlib.ticker.FuncFormatter(_tick))
            ax.set_xlabel("ratio of marginal means (log axis; 1 = no effect)")
        else:
            ax.set_xlabel("difference in marginal means (0 = no effect)")
        ax.grid(axis="x", zorder=0)
        ax.set_axisbelow(True)
        ax.set_ylim(-0.8, nrow - 0.2)
        if sub:                       # text, not set_title: panel 0's title
            ax.text(0, 1.015, sub, transform=ax.transAxes,   # carries the
                    fontsize=9.5, color=INK2, ha="left", va="bottom")  # header
    axes[0][-1].legend(handles=[
        Line2D([], [], marker="o", linestyle="", markerfacecolor=LO_HUE,
               markeredgecolor=LO_HUE, markersize=7,
               label="significant after the prespecified adjustment"),
        Line2D([], [], marker="o", linestyle="", markerfacecolor=SURFACE,
               markeredgecolor=LO_HUE, markersize=7, label="not significant")],
        loc="lower right", ncol=1, fontsize=8)
    axes[0][0].set_title(title, color=INK, fontsize=12.5, loc="left",
                         pad=30 if any(s for s, _ in groups) else 14,
                         fontweight="600")
    fig.text(0.005, 0.005, "Eight-cell marginal effects with 95% delta-method "
             "intervals. Hollow marker = interval consistent with no effect.",
             fontsize=7.6, color=MUTED)
    fig.tight_layout()
    out = FIGS / name
    fig.savefig(out)
    plt.close(fig)
    return out


# ==========================================================================
# overview small multiples
# ==========================================================================

def overview_figure(keys):
    fig, axes = plt.subplots(2, 4, figsize=(16.4, 8.0))
    for ax, k in zip(axes.ravel(), keys):
        e = R[k]
        log = e["family"] in LOG_FAMILIES
        for j, s in enumerate(SIGN_ORDER):
            violin(ax, j, DATA.loc[DATA["sign"] == s, k],
                   HI_HUE if s[0] == "+" else LO_HUE, width=0.36, log=log)
        style(ax, log)
        ax.set_xticks(range(8), SIGN_ORDER, fontsize=7.5,
                      family="DejaVu Sans Mono")
        ax.set_xlim(-0.7, 7.7)
        put_letters(ax, range(8), SIGN_ORDER, e["letters"]["cell"], log=log)
        ax.set_title(f"{e['label']}  ({e['unit']})", color=INK, fontsize=10,
                     loc="left", pad=8)
    fig.suptitle("All eight requested endpoints across the eight "
                 "configurations", x=0.012, y=0.995, ha="left", fontsize=13.5,
                 color=INK, fontweight="600")
    fig.text(0.012, 0.962,
             "x-axis code: model / effort / critic, '-' = sonnet / medium / "
             "off, '+' = opus / xhigh / on.  Blue = sonnet, orange = opus.  "
             "Shared letter = not significantly different.",
             fontsize=8.4, color=INK2, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.945))
    out = FIGS / "fig_22_overview.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ==========================================================================
def main():
    order = ["pr_auc", "roc_auc", "usd", "wallclock_min",
             "n_engineered_features", "n_features_after_fs",
             "n_engineered_features_selected", "prop_engineered_selected",
             "total_tokens", "n_turns_total", "inner_cv_score"]
    made = []
    for i, k in enumerate(order, start=1):
        p = endpoint_figure(k, i)
        if p:
            made.append(p)
            print(f"  wrote {p.name}")

    made.append(pareto_figure())
    print(f"  wrote {made[-1].name}")
    made.append(forest_figure(
        None, "fig_21a_forest_differences.png",
        "Main effects on the bounded endpoints (differences)", ratio=False,
        groups=[("accuracy endpoints (note the axis: units of AUC)",
                 ["pr_auc", "roc_auc", "inner_cv_score"]),
                ("composition of the selected feature set",
                 ["prop_engineered_selected"])]))
    print(f"  wrote {made[-1].name}")
    made.append(forest_figure(
        ["usd", "wallclock_min", "total_tokens", "n_turns_total",
         "n_engineered_features", "n_features_after_fs",
         "n_engineered_features_selected"],
        "fig_21b_forest_ratios.png",
        "Main effects on the cost and count endpoints (ratios)", ratio=True))
    print(f"  wrote {made[-1].name}")
    made.append(overview_figure(order[:8]))
    print(f"  wrote {made[-1].name}")
    print(f"\n{len(made)} figures -> {FIGS}")


if __name__ == "__main__":
    main()
