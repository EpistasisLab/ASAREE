"""Calibration accounting, with failures visible and exact binomial uncertainty."""

from __future__ import annotations

from scipy.stats import binomtest


def rejection_summary(outcomes, confidence=0.95):
    """Each outcome is rejected, not_rejected, withheld, failed, or incomplete.

    The binomial interval conditions on reportable simulations. Worst-case
    bounds over all simulations show how withheld/failed results limit it.
    """
    counts = {key: outcomes.count(key) for key in ("rejected", "not_rejected", "withheld", "failed", "incomplete")}
    if sum(counts.values()) != len(outcomes):
        raise ValueError("Unknown simulation outcome")
    tested = counts["rejected"] + counts["not_rejected"]
    interval = binomtest(counts["rejected"], tested).proportion_ci(confidence, method="exact") if tested else None
    missing = len(outcomes) - tested
    return dict(
        simulations=len(outcomes),
        **counts,
        reportable=tested,
        rejection_rate=counts["rejected"] / tested if tested else None,
        confidence=confidence,
        uncertainty_method="Two-sided Clopper–Pearson exact binomial interval",
        uncertainty_low=interval.low if interval else None,
        uncertainty_high=interval.high if interval else None,
        denominator="Reportable simulations only; withheld, failed and incomplete simulations excluded",
        all_simulations_bounds=[counts["rejected"] / len(outcomes), (counts["rejected"] + missing) / len(outcomes)]
        if outcomes
        else None,
        interpretation="Calibration applies only to the recorded model, design, data-generating process and "
        "analysis configuration. It is not validation of other reports or designs.",
    )
