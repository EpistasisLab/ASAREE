"""Prespecified contrast families and the quantities computed from cell means.

A contrast is stored as 2 (pairwise) or 4 (interaction) weight vectors over the
complete factorial cells. Each weight vector averages the cells it covers with
equal weights, so every quantity is a function of the cell means alone. The same
objects drive the GLM estimates, the bootstrap, the link-scale hypotheses for
likelihood-ratio / Wald tests, and the ART-C contrasts.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class ContrastFamily:
    """A prespecified family of contrasts (Holm adjustment is applied within it).

    kind='pairwise'    target=(A,): level comparisons of A, optionally within
                       each combination of ``by`` factors.
    kind='interaction' target=(A, B): difference-in-differences (or ratio of
                       ratios) for pairs of A levels x pairs of B levels,
                       optionally within each combination of ``by`` factors.
    ``at`` restricts the levels used for any factor (by factors or factors
    that are averaged over). ``pairs`` optionally lists the level pairs to
    compare (pairwise: [(a1, a2), ...]; interaction: [((a1, a2), (b1, b2)), ...]).
    """

    name: str
    kind: str
    target: tuple
    by: tuple = ()
    at: dict = field(default_factory=dict)
    pairs: list | None = None
    description: str = ""

    def __post_init__(self):
        self.target = tuple(self.target)
        self.by = tuple(self.by)
        if self.kind not in ("pairwise", "interaction"):
            raise ValueError("kind must be 'pairwise' or 'interaction'")
        if self.kind == "pairwise" and len(self.target) != 1:
            raise ValueError("pairwise families take exactly one target factor")
        if self.kind == "interaction" and len(self.target) != 2:
            raise ValueError("interaction families take exactly two target factors")
        if set(self.target) & set(self.by):
            raise ValueError("a factor cannot be both target and by")


@dataclass
class Contrast:
    family: str
    kind: str
    label: str
    target: tuple
    by_levels: dict
    levels: tuple
    weights: np.ndarray  # (2 or 4, n_cells)


def default_families(factors: list[str]) -> list[ContrastFamily]:
    fams = [
        ContrastFamily(f"main:{f}", "pairwise", (f,), description=f"Pairwise marginal comparisons of {f}")
        for f in factors
    ]
    for a, b in itertools.combinations(factors, 2):
        fams.append(
            ContrastFamily(
                f"int:{a}x{b}",
                "interaction",
                (a, b),
                description=f"{a} x {b} interaction contrasts (averaged over other factors)",
            )
        )
    return fams


def _mask(cells: pd.DataFrame, cond: dict) -> np.ndarray:
    m = np.ones(len(cells), dtype=bool)
    for f, v in cond.items():
        vals = v if isinstance(v, (list, tuple, set)) else [v]
        m &= cells[f].isin(vals).to_numpy()
    return m


def _wrow(cells, cond):
    m = _mask(cells, cond).astype(float)
    s = m.sum()
    if s == 0:
        raise ValueError(f"no cells match {cond}")
    return m / s


def build_contrasts(family: ContrastFamily, cells: pd.DataFrame, levels: dict) -> list[Contrast]:
    for f in family.target + family.by + tuple(family.at):
        if f not in levels:
            raise ValueError(f"contrast family {family.name!r} references unknown factor {f!r}")
    at = {f: list(v) if isinstance(v, (list, tuple)) else [v] for f, v in family.at.items()}
    by_lv = [at.get(f, levels[f]) for f in family.by]
    base = {f: v for f, v in at.items() if f not in family.by and f not in family.target}
    out: list[Contrast] = []
    for combo in itertools.product(*by_lv) if family.by else [()]:
        byd = dict(zip(family.by, combo, strict=True))
        tag = (" | " + ", ".join(f"{k}={v}" for k, v in byd.items())) if byd else ""
        cond0 = {**base, **byd}
        if family.kind == "pairwise":
            a = family.target[0]
            alv = at.get(a, levels[a])
            pairs = family.pairs or list(itertools.combinations(alv, 2))
            for a1, a2 in pairs:
                w = np.vstack([_wrow(cells, {**cond0, a: a1}), _wrow(cells, {**cond0, a: a2})])
                out.append(
                    Contrast(family.name, "pairwise", f"{a}: {a1} vs {a2}{tag}", family.target, byd, (a1, a2), w)
                )
        else:
            a, b = family.target
            alv, blv = at.get(a, levels[a]), at.get(b, levels[b])
            pairs = family.pairs or [
                (pa, pb) for pa in itertools.combinations(alv, 2) for pb in itertools.combinations(blv, 2)
            ]
            for (a1, a2), (b1, b2) in pairs:
                w = np.vstack(
                    [
                        _wrow(cells, {**cond0, a: a1, b: b1}),
                        _wrow(cells, {**cond0, a: a2, b: b1}),
                        _wrow(cells, {**cond0, a: a1, b: b2}),
                        _wrow(cells, {**cond0, a: a2, b: b2}),
                    ]
                )
                out.append(
                    Contrast(
                        family.name,
                        "interaction",
                        f"{a}({a1} vs {a2}) x {b}({b1} vs {b2}){tag}",
                        family.target,
                        byd,
                        (a1, a2, b1, b2),
                        w,
                    )
                )
    return out


class QuantitySet:
    """Maps (cell means, cell link values) to every reported quantity.

    Works on a single vector of cell values or on a batch (B, n_cells).
    """

    def __init__(
        self,
        cells: pd.DataFrame,
        factors: list[str],
        levels: dict,
        contrasts: list[Contrast],
        effect_scale: str,
        report_link: bool,
    ):
        self.cells = cells
        self.factors = factors
        self.contrasts = contrasts
        self.effect_scale = effect_scale
        self.report_link = report_link
        rows = []
        # marginal means
        mw = []
        for f in factors:
            for lv in levels[f]:
                mw.append(_wrow(cells, {f: lv}))
                rows.append(dict(quantity="marginal_mean", family=f"marginal:{f}", label=f"{f}={lv}", scale="mean"))
        self.W_marg = np.vstack(mw) if mw else np.zeros((0, len(cells)))
        self.cell_labels = [", ".join(f"{f}={r[f]}" for f in factors) for _, r in cells.iterrows()]
        meta = [dict(quantity="cell_mean", family="cells", label=l, scale="mean") for l in self.cell_labels] + rows
        # contrasts
        self.W_con = np.vstack([c.weights for c in contrasts]) if contrasts else np.zeros((0, len(cells)))
        self.con_offsets = np.cumsum([0] + [c.weights.shape[0] for c in contrasts])
        for c in contrasts:
            scale = (
                ("difference" if effect_scale == "difference" else "ratio")
                if c.kind == "pairwise"
                else ("difference_in_differences" if effect_scale == "difference" else "ratio_of_ratios")
            )
            meta.append(dict(quantity="contrast", family=c.family, label=c.label, scale=scale))
        if report_link:
            for c in contrasts:
                if c.kind == "interaction":
                    meta.append(
                        dict(quantity="link_interaction", family=c.family, label=c.label, scale="log_odds_ratio_ratio")
                    )
        self.meta = pd.DataFrame(meta)
        self.n_cells = len(cells)

    def _combine(self, pieces: np.ndarray, ratio: bool) -> np.ndarray:
        out = []
        for i, c in enumerate(self.contrasts):
            p = pieces[..., self.con_offsets[i] : self.con_offsets[i + 1]]
            if c.kind == "pairwise":
                out.append(p[..., 0] / p[..., 1] if ratio else p[..., 0] - p[..., 1])
            else:
                out.append(
                    (p[..., 0] / p[..., 1]) / (p[..., 2] / p[..., 3])
                    if ratio
                    else (p[..., 0] - p[..., 1]) - (p[..., 2] - p[..., 3])
                )
        return np.stack(out, axis=-1) if out else np.zeros(pieces.shape[:-1] + (0,))

    def evaluate(self, cell_means: np.ndarray, cell_links: np.ndarray) -> np.ndarray:
        cm = np.asarray(cell_means, float)
        parts = [cm, cm @ self.W_marg.T]
        if self.contrasts:
            parts.append(self._combine(cm @ self.W_con.T, self.effect_scale == "ratio"))
            if self.report_link:
                link = self._combine(np.asarray(cell_links) @ self.W_con.T, False)
                keep = [i for i, c in enumerate(self.contrasts) if c.kind == "interaction"]
                parts.append(link[..., keep])
        return np.concatenate(parts, axis=-1)

    def contrast_slice(self) -> slice:
        start = self.n_cells + self.W_marg.shape[0]
        return slice(start, start + len(self.contrasts))
