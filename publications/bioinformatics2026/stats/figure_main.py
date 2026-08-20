#!/usr/bin/env python3
"""
Main results figure for the 2x2x2 factorial (model x effort x critic).

    python3 figure_main.py              ->  figures/fig_main_results.{pdf,png}
    python3 figure_main.py --narrow     ->  figures/fig_main_results_narrow.{pdf,png}

Row 1  A     the experiment designer the 80 runs were specified from (SS_PanelA.png)
       B-C   the cost-accuracy frontier, against dollars and against wall-clock
Row 2  D-H   accuracy, then what each factor costs (money, time, tokens)
Row 3  I-M   iterations, then what each factor does to the feature set

Everything is drawn from tables/analysis_frame.csv (raw runs), tables/all_effects.csv
(model contrasts) and tables/all_terms_multiplicity.csv (Holm-adjusted p-values).
Every effect size is printed to stdout so the figure can be checked against the text.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.transforms import blended_transform_factory
from scipy.stats import gaussian_kde

HERE = Path(__file__).resolve().parent
TAB = HERE / "tables"
OUT = HERE / "figures"
OUT.mkdir(exist_ok=True)

NARROW = "--narrow" in sys.argv or "--compact" in sys.argv

# ---------------------------------------------------------------- data -----
D = pd.read_csv(TAB / "analysis_frame.csv")
E = pd.read_csv(TAB / "all_effects.csv")
M = pd.read_csv(TAB / "all_terms_multiplicity.csv")

BETA = {"pr_auc", "roc_auc", "prop_engineered_selected", "inner_cv_score"}

# -------------------------------------------------------------- tokens -----
INK, INK2, MUTED = "#12120f", "#3d3c38", "#7c7a73"
GRID, AXIS, SURFACE = "#e7e6df", "#b6b5ab", "#ffffff"

# one colour pair per factor: left violin = baseline level, right = raised level.
# validated for CVD separation, chroma, and contrast against a white surface.
C_MODEL = ("#2a78d6", "#eb6834")     # Sonnet 5 / Opus 5   (also used in A-B)
C_EFFORT = ("#6a4ec9", "#3f9e5c")    # medium   / xhigh
C_CRITIC = ("#b5399b", "#bf7d1a")    # off      / on
BLUE, ORANGE = C_MODEL

FACTORS = [("A", "model", ("sonnet", "opus"), C_MODEL),
           ("B", "effort", ("medium", "xhigh"), C_EFFORT),
           ("C", "critic", ("off", "on"), C_CRITIC)]


def effect_of(endpoint, term):
    """(display string, is-significant, raw effect) for a main effect."""
    e = E[(E.endpoint == endpoint) & (E.term == term)].iloc[0]
    p = M[(M.endpoint == endpoint) & (M.term == term)].iloc[0]
    # `status` is authoritative: primary-tier terms carry no Holm adjustment
    # (p_holm_prespecified is NaN for them), secondary/exploratory terms do.
    sig = str(p.status).startswith("significant")
    if endpoint == "prop_engineered_selected":
        txt = f"{e.effect * 100:+.1f} pp"
    elif endpoint in BETA:
        txt = f"{e.effect:+.4f}"
    else:
        txt = f"{e.effect:.2f}x"
    return txt, sig, e.effect


def tint(hex_color, f):
    """blend toward white by fraction f"""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(((1 - f) * c + f * 255) / 255 for c in (r, g, b))


plt.rcParams.update({
    "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "text.color": INK, "axes.labelcolor": INK, "axes.edgecolor": AXIS,
    "xtick.color": AXIS, "ytick.color": AXIS,
    "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.2, "ytick.major.size": 2.2,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
})

# ------------------------------------------------------------- geometry ----
# panel A is the experiment-designer screenshot; its aspect fixes the row height
SHOT = HERE / "SS_PanelA.png"
shot_img = plt.imread(SHOT)
# the capture ends mid-way through the third metric row; cutting in the gap above
# it loses no information, removes a clipped row, and buys back figure height
shot_img = shot_img[:int(0.966 * shot_img.shape[0])]
SHOT_AR = shot_img.shape[1] / shot_img.shape[0]     # width / height

if NARROW:
    FW, VH = 9.6, 0.86
    FS_TICK, FS_LVL, FS_TITLE, FS_LAB = 6.4, 6.6, 7.4, 7.0
    FS_PT, FS_KEY, FS_STAR = 5.9, 6.2, 12.4
    LET_DX, V_GAP, P_GAP = 0.28, 0.42, 0.46
    MARG_L, MARG_R = 0.56, 0.13
    STEM = "fig_main_results_narrow"
else:
    FW, VH = 12.0, 0.95
    FS_TICK, FS_LVL, FS_TITLE, FS_LAB = 7.7, 7.9, 8.8, 8.4
    FS_PT, FS_KEY, FS_STAR = 6.8, 7.2, 14.8
    LET_DX, V_GAP, P_GAP = 0.36, 0.53, 0.58
    MARG_L, MARG_R = 0.68, 0.16
    STEM = "fig_main_results"

L, R = MARG_L, FW - MARG_R             # drawing gutters
TOPPAD, KEY_H, FOOT_H = 0.08, 0.24, 0.06
TITLE_H, XLAB_H, PAR_XLAB_H = 0.21, 0.29, 0.35

# row 1: the screenshot on the left, the two frontier panels stacked to its
# right in a column of exactly the same height.
BODY = R - L - P_GAP
SHOT_W = 0.55 * BODY
PAR_H = SHOT_W / SHOT_AR               # row height follows the screenshot
PW = BODY - SHOT_W                     # width of the frontier column
FR_H = (PAR_H - PAR_XLAB_H - TITLE_H) / 2      # height of one frontier panel

FH = (TOPPAD + KEY_H + TITLE_H + PAR_H + PAR_XLAB_H
      + 2 * (TITLE_H + VH + XLAB_H) + FOOT_H)

fig = plt.figure(figsize=(FW, FH))


def ax_in(x, y, w, h):
    return fig.add_axes([x / FW, y / FH, w / FW, h / FH])


def fx(x):
    return x / FW


def fy(y):
    return y / FH


# vertical stations, top down
KEY_Y = FH - TOPPAD - KEY_H * 0.55
PAR_T = FH - TOPPAD - KEY_H - TITLE_H
PAR_B = PAR_T - PAR_H
R2_T = PAR_B - PAR_XLAB_H - TITLE_H
R2_B = R2_T - VH
R3_T = R2_B - XLAB_H - TITLE_H
R3_B = R3_T - VH

# row 1: screenshot on the left, frontier column on the right
SHOT_X = L
PX = L + SHOT_W + P_GAP
FR_TOP_B = PAR_T - FR_H                # bottom edge of the upper frontier
FR_BOT_B = PAR_B                       # bottom edge of the lower frontier

# rows 2-3: five violin panels each
NCOL = 5
VW = (R - L - (NCOL - 1) * V_GAP) / NCOL
VX = [L + i * (VW + V_GAP) for i in range(NCOL)]


def panel_title(x, y, letter, title):
    fig.text(fx(x - LET_DX), fy(y), letter, fontsize=FS_TITLE + 1.8,
             fontweight="bold", color=INK, ha="left", va="bottom")
    fig.text(fx(x), fy(y), title, fontsize=FS_TITLE, color=INK,
             ha="left", va="bottom")


# ------------------------------------------------------------------ key ----
# the key doubles as the x-axis legend for every violin panel: within each
# factor the left violin is the baseline level and the right one the raised level
KEY_SEG = [("model", "Sonnet 5", "Opus 5", C_MODEL),
           ("effort", "medium", "xhigh", C_EFFORT),
           ("critic", "off", "on", C_CRITIC)]
# the key sits above the frontier column, clear of the screenshot
SEG_W = PW / 3
D_NAME, D_SW1, D_L1, D_SW2, D_L2 = (0.0, 0.26 * SEG_W, 0.31 * SEG_W,
                                    0.66 * SEG_W, 0.71 * SEG_W)
for si, (name, lo_lab, hi_lab, pair) in enumerate(KEY_SEG):
    x0 = PX + si * SEG_W
    fig.text(fx(x0 + D_NAME), fy(KEY_Y), name, fontsize=FS_KEY, color=MUTED,
             ha="left", va="center")
    for dx_sw, dx_tx, col, lab in ((D_SW1, D_L1, pair[0], lo_lab),
                                   (D_SW2, D_L2, pair[1], hi_lab)):
        fig.add_artist(Line2D([fx(x0 + dx_sw)], [fy(KEY_Y)], marker="s", ms=5.0,
                              mfc=col, mec="none", transform=fig.transFigure))
        fig.text(fx(x0 + dx_tx), fy(KEY_Y), lab, fontsize=FS_KEY, color=INK,
                 ha="left", va="center")

# ============================================================== row 1 ======
sem = lambda s: s.std(ddof=1) / np.sqrt(len(s))
cfg = (D.groupby(["A", "B", "C"])
         .agg(usd=("usd", "mean"), wc=("wallclock_min", "mean"),
              pr=("pr_auc", "mean"),
              se=("pr_auc", sem),
              usd_se=("usd", sem), wc_se=("wallclock_min", sem))
         .reset_index())

SHORT = {"sonnet": "Sonnet", "opus": "Opus"}
CRIT = {"off": "no critic", "on": "+ critic"}
cfg["label"] = [f"{SHORT[r.A]} · {r.B}\n{CRIT[r.C]}" for r in cfg.itertuples()]
cfg["col"] = np.where(cfg.A == "sonnet", BLUE, ORANGE)

best = cfg.loc[cfg.pr.idxmax()]
PR_LO, PR_HI = 0.5235, 0.5515
PR_TICKS = [0.525, 0.530, 0.535, 0.540, 0.545, 0.550]

# hand-placed labels: (dx pt, dy pt, ha, va) keyed by (A,B,C)
OFF_USD = {
    ("sonnet", "medium", "off"): (0, 9, "center", "bottom"),
    ("sonnet", "xhigh", "off"): (-7, 0, "right", "center"),
    ("sonnet", "medium", "on"): (0, 9, "center", "bottom"),
    ("opus", "medium", "off"): (0, -10, "center", "top"),
    ("sonnet", "xhigh", "on"): (0, 9, "center", "bottom"),
    ("opus", "xhigh", "off"): (8, -3, "left", "top"),
    ("opus", "medium", "on"): (0, 9, "center", "bottom"),
    ("opus", "xhigh", "on"): (0, -10, "center", "top"),
}
OFF_WC = {
    ("sonnet", "medium", "off"): (0, 9, "center", "bottom"),
    ("opus", "medium", "off"): (0, -10, "center", "top"),
    ("sonnet", "xhigh", "off"): (-7, 0, "right", "center"),
    ("sonnet", "medium", "on"): (0, 9, "center", "bottom"),
    ("opus", "xhigh", "off"): (0, -10, "center", "top"),
    ("opus", "medium", "on"): (0, -23, "center", "top"),
    ("sonnet", "xhigh", "on"): (-3, 16, "center", "bottom"),
    ("opus", "xhigh", "on"): (0, -10, "center", "top"),
}


def frontier(ax, xcol, xlim, xticks, xlabel, offs, show_y, fold_txt):
    ax.set_xscale("log")
    ax.set_xlim(*xlim)
    ax.set_ylim(PR_LO, PR_HI)
    for yy in PR_TICKS:
        ax.axhline(yy, color=GRID, lw=0.55, zorder=0)
    for xx in xticks:
        ax.axvline(xx, color=GRID, lw=0.55, zorder=0)
    ax.axhline(best.pr, color=MUTED, lw=0.8, ls=(0, (4, 2.5)), zorder=1)

    for r in cfg.itertuples():
        x, xse = getattr(r, xcol), getattr(r, xcol + "_se")
        ax.plot([x, x], [r.pr - r.se, r.pr + r.se], color=r.col, lw=1.3,
                alpha=0.55, zorder=3, solid_capstyle="butt")
        ax.plot(x, r.pr, marker="o", ms=4.0, mfc=r.col, mec=SURFACE, mew=0.7,
                ls="none", zorder=4)
        # x-bar rides above the marker so even a sub-marker SE stays visible
        ax.plot([max(x - xse, ax.get_xlim()[0]), x + xse], [r.pr, r.pr],
                color=r.col, lw=1.3, zorder=5, solid_capstyle="butt")
        dx, dy, ha, va = offs[(r.A, r.B, r.C)]
        # the white bbox lets a label sit over a gridline or the best-mean rule
        ax.annotate(r.label, (x, r.pr), textcoords="offset points",
                    xytext=(dx, dy), ha=ha, va=va, fontsize=FS_PT,
                    color=INK2, linespacing=1.05, zorder=6,
                    bbox=dict(facecolor=SURFACE, edgecolor="none", pad=0.6))

    bx = getattr(best, xcol)
    ax.plot(bx, best.pr, marker="o", ms=10.5, mfc="none", mec=INK, mew=1.0,
            ls="none", zorder=6)

    # the fold-change span along the bottom
    worst = cfg.loc[cfg.pr.idxmin()]
    y0 = PR_LO + 0.07 * (PR_HI - PR_LO)
    ax.annotate("", xy=(getattr(worst, xcol), y0), xytext=(bx, y0),
                arrowprops=dict(arrowstyle="<->", lw=0.7, color=MUTED,
                                shrinkA=0, shrinkB=0), zorder=4)
    ax.text(np.sqrt(bx * getattr(worst, xcol)), y0 + 0.0008, fold_txt,
            fontsize=FS_LAB, color=INK2, ha="center", va="bottom", zorder=5)

    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{t:g}" if xcol == "wc" else f"${t:g}" for t in xticks],
                       fontsize=FS_TICK)
    ax.set_xlabel(xlabel, fontsize=FS_LAB, color=INK2, labelpad=2)
    ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.set_yticks(PR_TICKS)
    if show_y:
        ax.set_yticklabels([f"{t:.3f}" for t in PR_TICKS], fontsize=FS_TICK)
        ax.set_ylabel("mean held-out PR-AUC", fontsize=FS_LAB, color=INK2, labelpad=2)
    else:
        ax.set_yticklabels([])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=2.2, pad=1.6)


axA = ax_in(PX, FR_TOP_B, PW, FR_H)
axB = ax_in(PX, FR_BOT_B, PW, FR_H)
usd_fold = cfg.usd.max() / cfg.usd.min()
wc_fold = cfg.wc.max() / cfg.wc.min()
frontier(axA, "usd", (0.62, 32), [1, 2, 5, 10, 20],
         "mean cost per run  (USD, log scale)", OFF_USD, True,
         f"{usd_fold:.1f}× the cost")
frontier(axB, "wc", (3.2, 155), [5, 10, 20, 50, 100],
         "mean wall-clock per run  (minutes, log scale)", OFF_WC, True,
         f"{wc_fold:.1f}× the time")

# panel A: the experiment designer the 80 runs were specified and launched from
axS = ax_in(SHOT_X, PAR_B, SHOT_W, PAR_H)
axS.imshow(shot_img, interpolation="antialiased", aspect="auto")
axS.set_xticks([])
axS.set_yticks([])
for s in axS.spines.values():
    s.set_visible(True)
    s.set_color(AXIS)
    s.set_linewidth(0.6)

panel_title(SHOT_X, PAR_T + 0.05, "A", "Experiment specification")
panel_title(PX, PAR_T + 0.05, "B", "Held-out PR-AUC vs. cost per run")
panel_title(PX, FR_BOT_B + FR_H + 0.05, "C",
            "Held-out PR-AUC vs. wall-clock time")

# ========================================================== violin rows ====
rng = np.random.default_rng(11)


def violin(ax, vals, xc, half_w, color):
    v = np.asarray(vals, float)
    span = v.max() - v.min()
    if span <= 0:
        span = max(abs(v.mean()) * 0.02, 1e-6)
    kde = gaussian_kde(v, bw_method=0.55)
    grid = np.linspace(v.min() - 0.12 * span, v.max() + 0.12 * span, 240)
    dens = kde(grid)
    dens = dens / dens.max() * half_w
    ax.fill_betweenx(grid, xc - dens, xc + dens, facecolor=tint(color, 0.82),
                     edgecolor=color, lw=0.7, zorder=2)
    j = rng.uniform(-0.42, 0.42, len(v)) * half_w
    ax.plot(xc + j, v, ls="none", marker="o", ms=1.9, mfc=color,
            mec="none", alpha=0.75, zorder=3)
    q1, q2, q3 = np.percentile(v, [25, 50, 75])
    ax.plot([xc, xc], [q1, q3], color=INK, lw=1.5, solid_capstyle="butt", zorder=4)
    ax.plot([xc - half_w * 0.62, xc + half_w * 0.62], [q2, q2], color=INK,
            lw=1.5, solid_capstyle="butt", zorder=5)


def pct(x, _):
    return f"{x * 100:.0f}%"


printed = []


def violin_panel(x, y, w, h, key, ticks, fmt, half, dxw):
    """one endpoint, six violins: baseline vs raised level of each factor"""
    ax = ax_in(x, y, w, h)
    xb = blended_transform_factory(ax.transData, ax.transAxes)
    div = 1e6 if key == "total_tokens" else 1.0

    for gi, (term, fname, levels, pair) in enumerate(FACTORS):
        gc = gi + 1
        for li, lev in enumerate(levels):
            vals = D.loc[D[term] == lev, key].to_numpy(float) / div
            violin(ax, vals, gc + (-dxw if li == 0 else dxw), half, pair[li])
        txt, sig, raw = effect_of(key, term)
        if sig:                              # marker only; sizes live in the text
            ax.text(gc, 1.015, "*", transform=xb, ha="center", va="top",
                    fontsize=FS_STAR, color=INK, zorder=7)
        else:
            ax.text(gc, 0.985, "n.s.", transform=xb, ha="center", va="top",
                    fontsize=FS_LVL, color=MUTED, zorder=7)
        ax.text(gc, -0.055, fname, transform=xb, ha="center", va="top",
                fontsize=FS_LVL, color=INK2)
        printed.append((key, fname, txt, "sig" if sig else "ns", raw))

    for sep in (1.5, 2.5):
        ax.axvline(sep, color=GRID, lw=0.6, zorder=0)

    col = D[key].to_numpy(float) / div
    lo, hi = col.min(), col.max()
    pad = hi - lo
    bottom, top = lo - 0.16 * pad, hi + 0.22 * pad
    if lo >= 0:                              # never dip below zero on a count axis
        bottom = max(0.0, bottom)
    if ticks is not None:                    # widen so every requested tick lands
        bottom, top = min(bottom, min(ticks)), max(top, max(ticks))
    ax.set_ylim(bottom, top)

    if ticks is not None:
        ax.set_yticks(ticks)
        if fmt is not None:
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(fmt))
    else:
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4, integer=True))
    for gl in ax.get_yticks():
        if ax.get_ylim()[0] <= gl <= ax.get_ylim()[1]:
            ax.axhline(gl, color=GRID, lw=0.55, zorder=0)

    ax.set_xlim(0.5, 3.5)
    ax.set_xticks([])                        # levels are named once, in the key
    ax.tick_params(axis="y", length=2.2, pad=1.6, labelsize=FS_TICK)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)


# row 2: held-out accuracy, then what each factor costs
# row 3: iterations, then what each factor does to the feature set
VPANELS = [
    ("pr_auc", "Held-out PR-AUC", [0.52, 0.53, 0.54, 0.55, 0.56],
     lambda v, _: f"{v:.2f}"),
    ("roc_auc", "Held-out ROC-AUC", [0.745, 0.750, 0.755, 0.760, 0.765],
     lambda v, _: f"{v:.3f}"),
    ("usd", "Cost per run (USD)", [0, 10, 20, 30], lambda v, _: f"${v:g}"),
    ("wallclock_min", "Wall-clock (min)", [0, 50, 100, 150], None),
    ("total_tokens", "Tokens (millions)", [0, 1, 2, 3, 4], None),
    ("n_turns_total", "ReAct iterations", None, None),
    ("n_engineered_features", "Engineered created", [10, 20, 30, 40], None),
    ("n_features_after_fs", "Features in model", [30, 60, 90, 120, 150], None),
    ("n_engineered_features_selected", "Engineered retained", None, None),
    ("prop_engineered_selected", "Engineered share",
     [0.0, 0.1, 0.2, 0.3, 0.4], pct),
]

for i, (key, title, ticks, fmt) in enumerate(VPANELS):
    row = 0 if i < NCOL else 1
    x = VX[i % NCOL]
    yb, yt = (R2_B, R2_T) if row == 0 else (R3_B, R3_T)
    violin_panel(x, yb, VW, VH, key, ticks, fmt, 0.168, 0.205)
    panel_title(x, yt + 0.035, "DEFGHIJKLM"[i], title)

# everything explanatory lives in the LaTeX caption, not in the figure

# the PDF backend resamples rasters at the *save* dpi, so panel A is only as
# sharp as that number: the default (figure.dpi = 100) would resample the 2494 px
# screenshot down to 582 px. Save at its native resolution instead -- 1:1 pixels,
# no detail lost, and the plots stay vector regardless.
PDF_DPI = int(np.ceil(shot_img.shape[1] / SHOT_W))
for ext in ("pdf", "png"):
    fig.savefig(OUT / f"{STEM}.{ext}", dpi=600 if ext == "png" else PDF_DPI)
print(f"wrote {OUT / STEM}.pdf  ({FW:.2f} x {FH:.2f} in, panel A at {PDF_DPI} dpi)")

# ------------------------------------------------------- printed numbers ---
print("\nconfiguration means (n = 10 each)")
print(cfg[["A", "B", "C", "usd", "wc", "pr", "se"]]
      .sort_values("usd").to_string(index=False, float_format=lambda v: f"{v:.4f}"))
print(f"\ncost spread {usd_fold:.2f}x   time spread {wc_fold:.2f}x   "
      f"best mean PR-AUC {best.pr:.4f} at ${best.usd:.2f} / {best.wc:.1f} min")
print("\nmain effects behind the * markers")
for k, f, t, s, raw in printed:
    print(f"  {k:32s} {f:8s} {t:>10s}  {s:3s}  (raw {raw:.6g})")
