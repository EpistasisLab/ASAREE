"""Configuration objects for an ASAREE statistical analysis run."""

from __future__ import annotations

from dataclasses import dataclass, field

from .contrasts import ContrastFamily
from .selection import OutcomeSpec


@dataclass
class FactorSpec:
    """An adjustable experimental factor.

    kind='categorical' (levels in the given order; the order fixes labels, not the
    estimates, because coding is sum-to-zero) or kind='continuous' (entered as a
    centered linear covariate; excluded from ART/ART-C).
    """

    name: str
    kind: str = "categorical"
    levels: list | None = None
    description: str = ""

    def __post_init__(self):
        if self.kind not in ("categorical", "continuous"):
            raise ValueError(f"factor {self.name}: kind must be 'categorical' or 'continuous'")


@dataclass
class AnalysisConfig:
    factors: list[FactorSpec]
    outcomes: list[OutcomeSpec]
    replicate_col: str | None = "replicate"
    split_col: str | None = None  # train/test split or dataset identifier
    extra_block_cols: list[str] = field(default_factory=list)
    replicate_as_block: bool = False  # True when replicate r shares seed/test items across configs
    terms: list[tuple] | None = None  # explicit categorical terms; default complete factorial
    max_interaction_order: int | None = None
    contrast_families: list[ContrastFamily] = field(default_factory=list)
    include_default_contrasts: bool = True  # main-effect pairwise + all two-way interaction contrasts
    n_boot: int = 5000  # parametric bootstrap for intervals
    n_boot_lrt: int = 1999  # null-model bootstrap for LR p-values
    boot_lrt_time_budget_s: float = 1800.0  # per outcome; families that do not fit fall back to HC3 Wald
    boot_dispersion_correction: bool = True  # interval bootstrap: dispersion inflated by n/(n-p); False = pure ML
    n_perm: int = 10000
    n_sim_diagnostics: int = 250
    ci_level: float = 0.95
    alpha: float = 0.05
    seed: int = 20261016
    n_jobs: int = 1  # -1 = all cores (joblib)
    make_plots: bool = True

    @property
    def categorical(self) -> list[str]:
        return [f.name for f in self.factors if f.kind == "categorical"]

    @property
    def continuous(self) -> list[str]:
        return [f.name for f in self.factors if f.kind == "continuous"]

    @property
    def blocks(self) -> list[str]:
        b = list(self.extra_block_cols)
        if self.split_col:
            b.insert(0, self.split_col)
        if self.replicate_as_block and self.replicate_col:
            b.append(self.replicate_col)
        return b
