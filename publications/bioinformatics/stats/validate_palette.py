"""
Python twin of the data-viz skill's validate_palette.js.

Same thresholds, same Machado-Oliveira-Fernandes (2009) severity-1.0 CVD
transforms, same OKLab delta-E (x100). Ported because this machine has no Node
runtime and the rule is that palette safety is COMPUTED, never eyeballed.

    python validate_palette.py "#2a78d6,#eb6834" --mode light --pairs all
"""
from __future__ import annotations

import argparse
import itertools
import math
import sys

BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}     # OKLCH L
CHROMA_FLOOR = 0.10
CVD_TARGET, CVD_FLOOR = 8.0, 6.0                          # OKLab dE x100
NORMAL_FLOOR = 15.0
CONTRAST_MIN = 3.0
DEFAULT_SURFACE = {"light": "#fcfcfb", "dark": "#1a1a19"}

MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868),
               (0.114503, 0.786281, 0.099216),
               (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968),
               (0.280085, 0.672501, 0.047413),
               (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779),
               (-0.078411, 0.930809, 0.147602),
               (0.004733, 0.691367, 0.303900)),
}


def _srgb(h):
    h = h.strip().lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def _s2lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lin(h):
    return [_s2lin(c) for c in _srgb(h)]


def rel_lum(h):
    r, g, b = lin(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((rel_lum(a), rel_lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def oklab_from_lin(rgb):
    r, g, b = rgb
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklch(h):
    L, a, b = oklab_from_lin(lin(h))
    return L, math.hypot(a, b)


def simulate(h, kind):
    r, g, b = lin(h)
    M = MACHADO[kind]
    return [min(1.0, max(0.0, M[i][0] * r + M[i][1] * g + M[i][2] * b))
            for i in range(3)]


def delta_e(h1, h2, kind=None):
    a = oklab_from_lin(simulate(h1, kind) if kind else lin(h1))
    b = oklab_from_lin(simulate(h2, kind) if kind else lin(h2))
    return 100 * math.dist(a, b)


def validate(palette, mode="light", surface=None, pairs="adjacent"):
    surface = surface or DEFAULT_SURFACE[mode]
    lo, hi = BAND[mode]
    rows, ok = [], True

    off = [(c, round(oklch(c)[0], 3)) for c in palette
           if not (lo <= oklch(c)[0] <= hi)]
    ok &= not off
    rows.append(("Lightness band", not off,
                 f"outside band: {off}" if off
                 else f"all {len(palette)} inside L {lo}-{hi}"))

    lowc = [(c, round(oklch(c)[1], 3)) for c in palette
            if oklch(c)[1] < CHROMA_FLOOR]
    ok &= not lowc
    rows.append(("Chroma floor", not lowc,
                 f"below floor (reads gray): {lowc}" if lowc
                 else f"all {len(palette)} >= {CHROMA_FLOOR}"))

    n = len(palette)
    pl = (list(itertools.combinations(range(n), 2)) if pairs == "all"
          else [(i, i + 1) for i in range(n - 1)])
    label = "all-pairs" if pairs == "all" else "adjacent"

    worst = min(((delta_e(palette[i], palette[j], k), k, palette[i], palette[j])
                 for k in ("protan", "deutan") for i, j in pl),
                default=(99, "-", "", ""))
    tri = min((delta_e(palette[i], palette[j], "tritan") for i, j in pl),
              default=99)
    state = ("pass" if worst[0] >= CVD_TARGET
             else "floor" if worst[0] >= CVD_FLOOR else "fail")
    ok &= state != "fail"
    rows.append(("CVD separation", state,
                 f"worst {label} {worst[3]}<->{worst[2]} dE {worst[0]:.1f} "
                 f"({worst[1]}) - tritan {tri:.1f}"))

    nw = min(((delta_e(palette[i], palette[j]), palette[i], palette[j])
              for i, j in pl), default=(99, "", ""))
    nstate = "pass" if nw[0] >= NORMAL_FLOOR else "fail"
    ok &= nstate == "pass"
    rows.append(("Normal-vision floor", nstate,
                 f"worst {label} {nw[2]}<->{nw[1]} dE {nw[0]:.1f} (normal)"))

    low = [(c, round(contrast(c, surface), 2)) for c in palette
           if contrast(c, surface) < CONTRAST_MIN]
    rows.append(("Contrast vs surface", "relief" if low else "pass",
                 f"below {CONTRAST_MIN}:1 - relief required "
                 f"(visible labels or table view): {low}" if low
                 else f"all {len(palette)} >= {CONTRAST_MIN}:1"))
    return rows, ok


GLYPH = {True: "PASS", False: "FAIL", "pass": "PASS", "floor": "WARN",
         "fail": "FAIL", "relief": "WARN"}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("palette")
    ap.add_argument("--mode", default="light", choices=["light", "dark"])
    ap.add_argument("--surface")
    ap.add_argument("--pairs", default="adjacent", choices=["adjacent", "all"])
    a = ap.parse_args()
    pal = [c.strip() for c in a.palette.split(",") if c.strip()]
    surf = a.surface or DEFAULT_SURFACE[a.mode]
    rows, ok = validate(pal, a.mode, surf, a.pairs)
    print(f"\nPalette ({a.mode}, surface {surf}, categorical): {len(pal)} slots")
    for name, state, detail in rows:
        print(f"  [{GLYPH.get(state, state):<4}] {name:<22} {detail}")
    print(f"\n  -> {'ALL CHECKS PASS' if ok else 'FAILED - fix the marked checks'}\n")
    sys.exit(0 if ok else 1)
