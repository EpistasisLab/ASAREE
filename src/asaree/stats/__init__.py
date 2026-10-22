"""ASAREE statistical analysis component.

Factorial GLM analysis of repeated experimental runs with rule-based outcome
families, parametric-bootstrap intervals, bootstrap-LRT / HC3 Wald p-values,
ART / ART-C rank-based sensitivity analyses and design-preserving permutation
tests.

    from asaree.stats import AnalysisConfig, FactorSpec, OutcomeSpec, ContrastFamily, run_analysis
    results = run_analysis(df, config)
    results.save("out/")
"""

from .config import AnalysisConfig, FactorSpec
from .contrasts import ContrastFamily
from .families import FAMILIES, make_family
from .pipeline import AnalysisResults, run_analysis
from .selection import OutcomeSpec

__all__ = [
    "AnalysisConfig",
    "FactorSpec",
    "OutcomeSpec",
    "ContrastFamily",
    "run_analysis",
    "AnalysisResults",
    "FAMILIES",
    "make_family",
]
__version__ = "0.1.0"
