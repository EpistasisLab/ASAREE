"""asaree-sklearn-stats — statistical-analysis MCP server.

A thin FastMCP wrapper over :mod:`asaree_sklearn_core.stats` (issue #1457): each
tool parses its JSON arguments, calls the pure core function, and serializes the
result. Invalid inputs surface as the same ``{"error": ...}`` payload the
monolith returned. These tools take explicit record/score arguments and touch no
workspace, so they need no ambient context.
"""

from __future__ import annotations

import json

from mcp.server import FastMCP

from asaree_sklearn_core import stats
from asaree_sklearn_core.errors import ComputeError

INSTRUCTIONS = """\
Run a statistical test or summary over records and scores you pass in \
directly.

Self-contained: these tools take their data as arguments and touch no \
dataset or workspace, so they can be called at any point without setting \
anything up first."""

mcp = FastMCP("asaree-sklearn-stats", instructions=INSTRUCTIONS)


@mcp.tool()
def friedman_test(scores_json: str) -> str:
    """Non-parametric Friedman test for comparing k conditions with repeated measures.

    Args:
        scores_json: JSON string mapping condition names to lists of scores.
            Example: '{"cond_a": [0.71, 0.73, ...], "cond_b": [0.68, 0.70, ...]}'
            All lists must have the same length (number of replicates).
    """
    try:
        scores = json.loads(scores_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"})
    try:
        return json.dumps(stats.friedman_test(scores))
    except ComputeError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def pairwise_posthoc(
    scores_json: str, method: str = "dunn", correction: str = "benjamini-hochberg"
) -> str:
    """Pairwise post-hoc comparisons with multiple testing correction.

    Args:
        scores_json: JSON string mapping condition names to lists of scores (same format as friedman_test).
        method: 'dunn' (Dunn's test) or 'wilcoxon' (pairwise Wilcoxon signed-rank).
        correction: 'benjamini-hochberg' (BH FDR) or 'bonferroni'.
    """
    try:
        scores = json.loads(scores_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"})
    try:
        return json.dumps(stats.pairwise_posthoc(scores, method=method, correction=correction))
    except ComputeError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def summarize_experiment(
    results_json: str, factor_cols: str, response_col: str = "balanced_accuracy"
) -> str:
    """Descriptive statistics per experimental condition for reporting.

    Args:
        results_json: JSON string — list of result records, each with factor columns and response column.
            Example: '[{"knowledge": "rag", "quality": "ifa", "safety": "timeout", "balanced_accuracy": 0.72}, ...]'
        factor_cols: Comma-separated column names for the experimental factors.
        response_col: Name of the response variable column (default 'balanced_accuracy').
    """
    try:
        records = json.loads(results_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid results JSON: {e}"})
    factors = [f.strip() for f in factor_cols.split(",")]
    try:
        return json.dumps(stats.summarize_experiment(records, factors, response_col))
    except ComputeError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def art_anova(
    results_json: str, within_factors: str, response_col: str = "balanced_accuracy"
) -> str:
    """Aligned Rank Transform ANOVA for factorial repeated-measures designs.

    Implements the ART procedure (Wobbrock et al., 2011): aligns each response
    by stripping main effect and interaction contributions, then ranks, then runs
    a standard ANOVA on the aligned-ranked values.

    Args:
        results_json: JSON string — list of result records with factor columns and response column.
            Example: '[{"subject": 1, "knowledge": "rag", "quality": "ifa", "balanced_accuracy": 0.72}, ...]'
        within_factors: Comma-separated column names for the within-subject factors.
        response_col: Name of the response variable column (default 'balanced_accuracy').
    """
    try:
        records = json.loads(results_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid results JSON: {e}"})
    factors = [f.strip() for f in within_factors.split(",")]
    try:
        return json.dumps(stats.art_anova(records, factors, response_col))
    except ComputeError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def ping() -> str:
    """Health check — returns 'pong' to verify the server is running."""
    return "pong"
