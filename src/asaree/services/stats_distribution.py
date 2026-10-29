"""Bounded empirical distributions for reports, without fitting a model."""

import math

import numpy as np

# Bound chart complexity when extreme outliers imply very small bin widths.
MAX_BINS = 40


def distribution_plot(values, category_labels=None) -> dict | None:
    values = np.asarray(values, dtype=float)
    if not len(values) or not np.isfinite(values).all():
        return None
    unique, counts = np.unique(values, return_counts=True)
    if category_labels or len(unique) <= 12:
        bins = []
        for value, count in zip(unique, counts, strict=True):
            label = (
                str(category_labels[int(value)])
                if category_labels and value == int(value) and 0 <= value < len(category_labels)
                else f"{value:.4g}"
            )
            bins.append(dict(label=label, count=int(count)))
        return dict(kind="frequency", bins=bins)
    # Freedman–Diaconis adapts to sample size and spread; Sturges handles zero IQR.
    width = 2 * float(np.subtract(*np.percentile(values, [75, 25]))) / np.cbrt(len(values))
    requested = float(values.max() - values.min()) / width if width > 0 else math.ceil(math.log2(len(values)) + 1)
    counts, edges = np.histogram(values, bins=max(1, math.ceil(min(MAX_BINS, requested))))
    return dict(kind="histogram", binning=(
        "Freedman–Diaconis" if width > 0 else "Sturges (zero interquartile range)"
    ) + ("; limited to 40 bins for readability" if requested > MAX_BINS else ""), bins=[
        dict(label=f"{low:.4g}–{high:.4g}", count=int(count), lower=float(low), upper=float(high))
        for low, high, count in zip(edges[:-1], edges[1:], counts, strict=True)
    ])


def observed_distribution(values, category_labels=None) -> dict:
    values = np.asarray(values, dtype=float)
    return dict(
        n=len(values), minimum=float(values.min()), maximum=float(values.max()),
        mean=float(values.mean()), sd=float(values.std(ddof=1)) if len(values) > 1 else None,
        distinct=len(np.unique(values)), zeros=int((values == 0).sum()),
        plot=distribution_plot(values, category_labels),
    )


def snapshot_distribution(output: dict, source: dict, config: dict) -> dict | None:
    """Rebuild presentation data from the original inputs, without model fitting."""
    from asaree.services.stats_automatic import automatic_inputs, plan_metric
    from asaree.services.stats_source import prepare_frame

    try:
        plan = plan_metric(source, config["metric"])
        converted, resolved, _ = automatic_inputs(source, dict(config, family=output["family"]), plan)
        frame = prepare_frame(converted, resolved)
        stored = (output.get("summary") or {}).get("distribution") or {}
        expected = stored.get("n", output.get("n_observations"))
        if expected is not None and len(frame) != expected:
            return None
        labels = plan.get("category_labels") or plan.get("ordinal_levels")
        return observed_distribution(
            frame.outcome.to_numpy(float), labels if plan["measurement_type"] == "ordinal" else None,
        )
    except (ValueError, KeyError, TypeError):
        return None


def report_distribution(output: dict, source: dict, config: dict) -> dict | None:
    stored = (output.get("summary") or {}).get("distribution")
    if stored and stored.get("plot"):
        return dict(stored, plot=saved_distribution_plot(output))
    recovered = snapshot_distribution(output, source, config)
    if recovered:
        return recovered
    return dict(stored, plot=saved_distribution_plot(output)) if stored else None


def saved_distribution_plot(output: dict, source: dict | None = None, config: dict | None = None) -> dict | None:
    """Older reports retain analyzed observations in their residuals table.

    Only use them when they match the saved summary's scale and count; some
    models record residuals on a transformed scale. Never read live results.
    """
    distribution = (output.get("summary") or {}).get("distribution") or {}
    if distribution.get("plot"):
        plot = distribution["plot"]
        # Upgrade historical dot strips using saved observations, never live data.
        if plot.get("kind") == "histogram" and plot.get("values"):
            return distribution_plot(plot["values"])
        return plot
    if source is not None and config is not None:
        recovered = snapshot_distribution(output, source, config)
        if recovered:
            return recovered["plot"]
    values = [row.get("observed") for row in output.get("tables", {}).get("residuals", [])]
    if not values or len(values) != distribution.get("n"):
        return None
    if any(not isinstance(value, int | float) or not math.isfinite(value) for value in values):
        return None
    if not np.isclose(min(values), distribution.get("minimum", np.nan)) or not np.isclose(
        max(values), distribution.get("maximum", np.nan)
    ):
        return None
    # Historical ordinal residuals do not reliably retain the original labels.
    if output.get("family") == "ordinal_logit":
        return None
    return distribution_plot(values)
