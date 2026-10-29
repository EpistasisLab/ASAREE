"""Sum-coded factorial design matrices, reference grids and term bookkeeping.

Categorical factors use sum-to-zero (deviation) coding so that main effects
are defined as averages over the levels of the other factors even when
interactions are present. This makes Type III tests (drop a term's columns
from the full model) well defined and matches the equally weighted marginal
means reported elsewhere in the pipeline.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def sum_code(values: pd.Series | np.ndarray, levels: list) -> np.ndarray:
    """Sum-to-zero coding. Returns (n, K-1); the last level is coded -1."""
    values = np.asarray(values, dtype=object)
    k = len(levels)
    out = np.zeros((len(values), max(k - 1, 0)))
    index = {lv: i for i, lv in enumerate(levels)}
    for row, v in enumerate(values):
        j = index[v]
        if j == k - 1:
            out[row, :] = -1.0
        else:
            out[row, j] = 1.0
    return out


def term_name(term: tuple[str, ...]) -> str:
    return ":".join(term)


def full_factorial_terms(factors: list[str], max_order: int | None = None) -> list[tuple[str, ...]]:
    max_order = len(factors) if max_order is None else max_order
    terms: list[tuple[str, ...]] = []
    for order in range(1, max_order + 1):
        terms.extend(itertools.combinations(factors, order))
    return terms


@dataclass
class DesignSpec:
    """Everything needed to rebuild the same design on any data frame."""

    factors: list[str]  # categorical experimental factors
    levels: dict[str, list]  # ordered levels per categorical factor
    terms: list[tuple[str, ...]]  # categorical terms (main + interactions)
    covariates: list[str] = field(default_factory=list)  # continuous, centered
    covariate_means: dict[str, float] = field(default_factory=dict)
    blocks: list[str] = field(default_factory=list)  # fixed blocking factors (split, dataset)
    block_levels: dict[str, list] = field(default_factory=dict)
    intercept: bool = True

    def __post_init__(self):
        # hierarchy is not enforced (Type III with sum coding does not need it), but
        # terms must reference known factors
        for t in self.terms:
            for f in t:
                if f not in self.factors:
                    raise ValueError(f"term {t} references unknown factor {f}")


class DesignBuilder:
    """Builds X and keeps a term -> column-index map."""

    def __init__(self, spec: DesignSpec):
        self.spec = spec
        self.columns: list[str] = []
        self.term_columns: dict[str, np.ndarray] = {}
        self._layout()

    # ------------------------------------------------------------------ layout
    def _layout(self):
        s = self.spec
        cols: list[str] = []
        if s.intercept:
            self.term_columns["Intercept"] = np.array([0])
            cols.append("Intercept")
        for t in s.terms:
            names = [""]
            for f in t:
                lv = s.levels[f][:-1]
                names = [f"{n}:{f}[{l}]" if n else f"{f}[{l}]" for n in names for l in lv]
            idx = np.arange(len(cols), len(cols) + len(names))
            self.term_columns[term_name(t)] = idx
            cols.extend(names)
        for c in s.covariates:
            self.term_columns[c] = np.array([len(cols)])
            cols.append(c)
        for b in s.blocks:
            lv = s.block_levels[b][:-1]
            idx = np.arange(len(cols), len(cols) + len(lv))
            self.term_columns[f"block:{b}"] = idx
            cols.extend(f"{b}[{l}]" for l in lv)
        self.columns = cols

    # ------------------------------------------------------------------- build
    def build(self, df: pd.DataFrame) -> np.ndarray:
        s = self.spec
        n = len(df)
        coded = {f: sum_code(df[f].to_numpy(), s.levels[f]) for f in s.factors}
        blocks = []
        if s.intercept:
            blocks.append(np.ones((n, 1)))
        for t in s.terms:
            m = np.ones((n, 1))
            for f in t:
                c = coded[f]
                m = (m[:, :, None] * c[:, None, :]).reshape(n, -1)
            blocks.append(m)
        for c in s.covariates:
            blocks.append((df[c].to_numpy(dtype=float) - s.covariate_means[c])[:, None])
        for b in s.blocks:
            blocks.append(sum_code(df[b].to_numpy(), s.block_levels[b]))
        return np.hstack(blocks) if blocks else np.zeros((n, 0))

    # -------------------------------------------------------- reference grids
    def cell_table(self) -> pd.DataFrame:
        """All combinations of categorical factor levels (complete factorial)."""
        s = self.spec
        if not s.factors:
            return pd.DataFrame(index=[0])
        combos = list(itertools.product(*[s.levels[f] for f in s.factors]))
        return pd.DataFrame(combos, columns=s.factors)

    def grid(self) -> tuple[pd.DataFrame, np.ndarray, int]:
        """Cells x block-level combinations, covariates at their means.

        Returns (grid_df, X_grid, n_block_combos). Rows are ordered cell-major, so
        X_grid.reshape(n_cells, n_block_combos, p) groups each cell's rows.
        """
        s = self.spec
        cells = self.cell_table()
        if s.blocks:
            bcombo = pd.DataFrame(list(itertools.product(*[s.block_levels[b] for b in s.blocks])), columns=s.blocks)
        else:
            bcombo = pd.DataFrame(index=[0])
        nb = len(bcombo)
        rows = cells.loc[cells.index.repeat(nb)].reset_index(drop=True)
        brows = pd.concat([bcombo] * len(cells), ignore_index=True)
        grid = pd.concat([rows, brows], axis=1)
        for c in s.covariates:
            grid[c] = s.covariate_means[c]
        return grid, self.build(grid), nb


def cell_index(df: pd.DataFrame, factors: list[str], levels: dict[str, list]) -> np.ndarray:
    """Integer index of each row's factorial cell, in the order of DesignBuilder.cell_table()."""
    if not factors:
        return np.zeros(len(df), dtype=int)
    idx = np.zeros(len(df), dtype=int)
    for f in factors:
        lv = {l: i for i, l in enumerate(levels[f])}
        idx = idx * len(levels[f]) + df[f].map(lv).to_numpy(dtype=int)
    return idx


def stratum_index(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    if not cols:
        return np.zeros(len(df), dtype=int)
    return df.groupby(cols, sort=True, observed=True).ngroup().to_numpy()
