"""Observed factor distributions, computed only from the analyzed snapshot."""

from html import escape
from itertools import combinations

import numpy as np

from asaree.services.stats_labels import display_factor_name


def box_summary(values):
    values = np.asarray(values, dtype=float)
    q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
    iqr = q3 - q1
    inside = values[(values >= q1 - 1.5 * iqr) & (values <= q3 + 1.5 * iqr)]
    low, high = float(inside.min()), float(inside.max())
    return dict(
        n=len(values), q1=float(q1), median=float(median), q3=float(q3), low=low, high=high,
        outliers=np.unique(values[(values < low) | (values > high)]).tolist(),
    )


def factor_boxplots(frame, factors, *, max_order=2):
    plots = {"main": [], "interaction": []}
    for order in range(1, min(2, max_order) + 1):
        for indices in combinations(range(len(factors)), order):
            columns = [f"factor_{i}" for i in indices]
            groups = []
            for levels, group in frame.groupby(columns, sort=False, observed=True):
                groups.append(dict(levels=list(levels), **box_summary(group.outcome)))
            plots["main" if order == 1 else "interaction"].append(
                dict(factors=[factors[i] for i in indices], groups=groups)
            )
    return plots


def snapshot_boxplots(output, source, config):
    """Recover old reports without refitting or consulting current experiment results."""
    from asaree.services.stats_automatic import automatic_inputs, plan_metric
    from asaree.services.stats_source import measurement_frame

    try:
        plan = plan_metric(source, config["metric"])
        converted, resolved, _ = automatic_inputs(source, config, plan)
        frame = measurement_frame(converted, resolved)
        distribution = (output.get("summary") or {}).get("distribution") or {}
        if frame.empty or len(frame) != distribution.get("n", output.get("n_observations")):
            return None
        values = frame.outcome.to_numpy(float)
        if output.get("family") == "descriptive" and resolved.get("denominator"):
            # Constant-outcome reports describe proportions rather than success counts.
            frame = frame.assign(outcome=frame.outcome / frame.trials)
            values = frame.outcome.to_numpy(float)
        if (
            not np.isfinite(values).all()
            or not np.isclose(values.min(), distribution.get("minimum", np.nan))
            or not np.isclose(values.max(), distribution.get("maximum", np.nan))
        ):
            return None
        return factor_boxplots(frame, resolved["factors"], max_order=config.get("max_order", 2))
    except (ValueError, KeyError, TypeError):
        return None


def render_boxplots(plots, kind, metric, unit=None):
    """Script-free, vector box plots with a common metric scale in each figure."""
    figures = []
    for plot in (plots or {}).get(kind, []):
        groups = plot["groups"]
        if not groups:
            continue
        names = [display_factor_name(name) for name in plot["factors"]]
        title = " × ".join(names)
        low = min(min([group["low"], *group["outliers"]]) for group in groups)
        high = max(max([group["high"], *group["outliers"]]) for group in groups)
        padding = (high - low) * 0.08 if high != low else max(abs(low) * 0.05, 0.5)
        low, high = low - padding, high + padding

        def y(value, low=low, high=high):
            return 200 - 180 * (value - low) / (high - low)

        ticks = "".join(f"<span>{value:.4g}</span>" for value in np.linspace(high, low, 5))
        levels = list(dict.fromkeys(group["levels"][0] for group in groups))
        traces = list(dict.fromkeys(group["levels"][1] for group in groups)) if len(names) == 2 else [None]
        columns = []
        for level in levels:
            boxes = []
            for trace in traces:
                group = next((group for group in groups if group["levels"] == (
                    [level, trace] if trace is not None else [level]
                )), None)
                label = escape(str(trace if trace is not None else level), quote=True)
                if group is None:
                    boxes.append(
                        f'<div class="box-slot"><div class="missing-box">No observations</div><p>{label}</p></div>'
                    )
                    continue
                description = escape(
                    f"{title}: {', '.join(str(value) for value in group['levels'])}; n={group['n']}; "
                    f"median={group['median']:.4g}; Q1={group['q1']:.4g}; Q3={group['q3']:.4g}; "
                    f"whiskers={group['low']:.4g} to {group['high']:.4g}; "
                    f"{len(group['outliers'])} distinct outlier values", quote=True,
                )
                outliers = "".join(
                    f'<circle cx="40" cy="{y(value):.4f}" r="2.5"><title>{value:.6g}</title></circle>'
                    for value in group["outliers"]
                )
                boxes.append(
                    f'<div class="box-slot"><svg viewBox="0 0 80 220" role="img" aria-label="{description}">'
                    f'<title>{description}</title><path d="M40 {y(group["high"]):.4f} V{y(group["low"]):.4f} '
                    f'M25 {y(group["high"]):.4f} H55 M25 {y(group["low"]):.4f} H55"/>'
                    f'<rect x="15" y="{y(group["q3"]):.4f}" width="50" '
                    f'height="{y(group["q1"]) - y(group["q3"]):.4f}"/>'
                    f'<path class="box-median" d="M15 {y(group["median"]):.4f} H65"/>{outliers}</svg>'
                    f'<p>{label}<small>n={group["n"]}</small></p></div>'
                )
            columns.append(
                '<div class="box-group"><div class="box-traces">' + "".join(boxes) + '</div>'
                + (f'<p class="box-level">{escape(str(level))}</p>' if len(names) == 2 else '') + '</div>'
            )
        axis = f"{metric} ({unit})" if unit else metric
        x_title = f"{names[0]} levels"
        if len(names) == 2:
            x_title += f" · boxes within each group: {names[1]} levels"
        figures.append(
            f'<figure class="box-figure" aria-label="Box plot: {escape(title, quote=True)}">'
            f'<figcaption>{escape(title)}</figcaption><div class="box-scroll" tabindex="0" '
            f'role="region" aria-label="{escape(title, quote=True)} distributions">'
            f'<div class="box-chart"><span class="box-y-title">{escape(axis)}</span>'
            f'<div class="box-y-ticks" aria-hidden="true">{ticks}</div>'
            f'<div class="box-groups">{"".join(columns)}</div></div></div>'
            f'<p class="x-title">{escape(x_title)}</p></figure>'
        )
    if not figures:
        return ""
    note = (
        "Observed distributions pool analyzed observations over the other factors, "
        "with each observation weighted equally. "
        "They are descriptive, not model-adjusted effects or evidence of an interaction. "
        "Boxes show the 25th–75th percentiles and median; whiskers reach the furthest observations "
        "within 1.5 interquartile ranges. Dots show distinct outlier values; repeated values overlap."
    )
    title = "Main-effect distributions" if kind == "main" else "Two-factor interaction distributions"
    return f'<section class="boxplots"><h3>{title}</h3><p>{note}</p>{"".join(figures)}</section>'
