"""Self-contained, script-free HTML from the same summary the application shows."""

from __future__ import annotations

import json
import math
from html import escape

from asaree.services.stats_distribution import saved_distribution_plot
from asaree.services.stats_findings import describe_findings

DISTRIBUTION_NAMES = {
    "gaussian": "Gaussian (normal)", "gamma": "Gamma", "lognormal": "Lognormal",
    "poisson": "Poisson", "negbin": "Negative binomial", "binomial": "Binomial",
    "beta": "Beta", "betabinomial": "Beta-binomial", "ordinal_logit": "Ordered categorical (cumulative logit)",
    "zip": "Zero-inflated Poisson", "zinb": "Zero-inflated negative binomial",
    "zoib": "Zero/one-inflated beta", "gamma_hurdle": "Hurdle Gamma",
}


def display(value, p=False):
    if value is None:
        return "—"
    if isinstance(value, int | float):
        if not math.isfinite(value):
            return "—"
        if p:
            return "<0.001" if value < 0.001 else f"{value:.3f}"
        return f"{value:.4g}"
    return str(value)


def render_html(analysis: dict) -> str:
    output = analysis.get("output") or {}
    summary = output.get("summary")
    if not summary:
        raise ValueError("This analysis predates the automatic report. Run an automatic analysis to create a report.")

    def text(value):
        return escape(display(value), quote=True)

    def listing(items):
        return "<ul>" + "".join(f"<li>{text(item)}</li>" for item in items) + "</ul>"

    def table(rows, columns):
        if not rows:
            return "<p>No statistical comparisons are available.</p>"
        head = "".join(f"<th scope='col'>{text(title)}</th>" for _, title in columns)

        def cell(row, key):
            content = escape(display(row.get(key), key in {"p_adjusted", "permutation_p_adjusted"}))
            if key in {"effect", "comparison"} and row.get("method"):
                content += f'<small class="test-method">{text(row["method"])}</small>'
            if key == "permutation_p_adjusted":
                detail = row.get("permutation_result") if row.get(key) is not None else row.get("permutation_status")
                content += f'<small class="test-method">{text(detail)}</small>'
            if key == "bootstrap_interval":
                detail = (
                    row.get("interval_agreement")
                    if row.get("bootstrap_ci_low") is not None
                    else row.get("bootstrap_status")
                )
                content += f'<small class="test-method">{text(detail)}</small>'
            return f"<td>{content}</td>"

        body = "".join("<tr>" + "".join(cell(row, key) for key, _ in columns) + "</tr>" for row in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    effects = [
        {
            **row,
            "scale": str(row["scale"]).replace("_", " ") + (f" ({row['unit']})" if row.get("unit") else ""),
            "interval": f"{display(row['ci_low'])} to {display(row['ci_high'])}"
            if row.get("ci_low") is not None and row.get("ci_high") is not None
            else "Unavailable",
            "bootstrap_interval": f"{display(row['bootstrap_ci_low'])} to {display(row['bootstrap_ci_high'])}"
            if row.get("bootstrap_ci_low") is not None and row.get("bootstrap_ci_high") is not None
            else "Unavailable",
        }
        for row in summary["effects"]
    ]
    definition = summary.get("metric_definition") or {}
    info = summary.get("metric_info") or dict(
        description=definition.get("description") or "Description not provided.",
        unit=definition.get("unit"),
        coding=[],
    )
    metric_context = f"<p>{text(info['description'])}</p>"
    if info.get("unit"):
        metric_context += f"<p>Unit: {text(info['unit'])}</p>"
    metric_context += "".join(f"<p>{text(line)}</p>" for line in info.get("coding", []))
    family = output.get("family")
    distribution_section = ""
    if family:
        distribution_section += (
            f"<p>Selected model distribution: {text(DISTRIBUTION_NAMES.get(family, family))}</p>"
        )
    distribution = summary.get("distribution")
    if distribution and distribution.get("n", 0) > 0:
        distribution_section += "<h2>Observed distribution</h2>"
        plot = saved_distribution_plot(output)
        if plot and plot.get("values"):
            values = plot["values"]
            minimum, maximum = min(values), max(values)
            span = maximum - minimum or 1.0
            seen: dict[float, int] = {}
            dots = []
            for dot in values:
                rank = seen.get(dot, 0)
                seen[dot] = rank + 1
                dots.append(
                    f'<span class="dot" style="left:{100 * (dot - minimum) / span:.4f}%;bottom:{rank % 3 * 10}px"'
                    f' title="{text(dot)}"></span>'
                )
            distribution_section += (
                '<figure aria-label="Observed distribution"><figcaption>Each dot is one replicate</figcaption>'
                f'<div class="dots">{"".join(dots)}</div>'
                f'<p class="dot-scale"><span>{text(minimum)}</span><span>{text(maximum)}</span></p></figure>'
            )
        elif plot and plot.get("bins"):
            maximum = max(1, *(bin["count"] for bin in plot["bins"]))
            bars = "".join(
                f'<li><span>{text(bin["label"])}</span><span class="track">'
                f'<span class="bar" style="width:{100 * bin["count"] / maximum:.4f}%"></span>'
                f'</span><span>{text(bin["count"])}</span></li>'
                for bin in plot["bins"]
            )
            distribution_section += (
                '<figure aria-label="Observed distribution"><figcaption>Metric value · Replicate count</figcaption>'
                f'<ol class="distribution">{bars}</ol></figure>'
            )
        distribution_section += (
            "<p>Observed values pool the analyzed replicates across conditions. "
            "The selected model describes outcomes after accounting for the experimental factors.</p>"
        )
        if info.get("coding"):
            distribution_section += (
                "<p>Categorical outcomes use the analyzed category codes. "
                "Their numeric summaries depend on that coding.</p>"
            )
        distribution_section += table(
            [{"statistic": title, "value": distribution.get(key)} for key, title in (
                ("n", "Replicates"), ("minimum", "Minimum"), ("maximum", "Maximum"),
                ("mean", "Mean"), ("sd", "Standard deviation"), ("distinct", "Distinct values"),
                ("zeros", "Zero values"),
            )],
            [("statistic", "Statistic"), ("value", "Value")],
        )
    resampled = bool(summary.get("resampling"))
    resampling_note = ""
    if resampled:
        bootstrap = summary["resampling"]["bootstrap"]
        resampling_note = (
            f"<p>Bootstrap: {text(bootstrap['successful'])}/{text(bootstrap['requested'])} successful refits; "
            f"{text(bootstrap['failed'])} failed. Status: {text(bootstrap['status'])}.</p>"
            "<p>Permutation results are working-scale sensitivity checks and may test a different null hypothesis. "
            "See “What ran and why” for assumptions.</p>"
        )

    def test_methods(rows):
        methods = {row.get("method"): row.get("purpose") for row in rows if row.get("method")}
        return "".join(
            f"<p><strong>{text(method)}</strong></p>" + (f"<p>{text(purpose)}</p>" if purpose else "")
            for method, purpose in methods.items()
        )

    overview = [
        ("01", "Model selection", f"<h4>{text(DISTRIBUTION_NAMES.get(family, family))}</h4>"
         + (f"<p>{text(summary['methods'][0])}</p>" if summary["methods"] else "")),
        ("02", "Tests and comparisons", "<h4>Factor tests</h4>" + test_methods(summary["tests"])
         + "<h4>Comparisons</h4>" + test_methods(summary["effects"])),
        ("03", "Uncertainty checks", resampling_note or "<p>No resampling results were recorded.</p>"),
        ("04", "How to interpret it", listing([
            "Adjusted p-values account for multiple tests within each metric.",
            "Confidence intervals are not adjusted for multiple comparisons.",
            "A result without clear evidence does not mean the effect is zero.",
            "Results across separate metrics are exploratory.",
        ])),
    ]
    methodology = (
        '<section class="methodology" aria-label="What ran and why"><header><p class="eyebrow">Analysis approach</p>'
        '<h2>What ran and why</h2><p>Statistical calculations · no AI</p></header><div class="method-grid">'
        + "".join(
            f'<section class="method-step"><p class="eyebrow">{number}</p><h3>{title}</h3>{content}</section>'
            for number, title, content in overview
        )
        + '</div><details><summary>Full technical details</summary>'
        '<p>Calculated with statistical libraries. Explanations use fixed templates; '
        'no AI is used to generate this report.</p>'
        + listing(summary["methods"]) + '</details></section>'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'">
<title>ASAREE statistics — {text(summary["metric"])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;color:#202c38;max-width:1100px;margin:40px auto;padding:0 24px}}
h1,h2{{line-height:1.25}}table{{border-collapse:collapse;width:100%;font-size:14px;margin:16px 0}}
th,td{{border-bottom:1px solid #cbd5e1;text-align:left;padding:10px;vertical-align:top;overflow-wrap:anywhere}}
th{{background:#eff3f6}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}
.test-method{{display:block;margin-top:4px;color:#526170;font-size:12px}}
.distribution{{list-style:none;padding:0}}.distribution li{{display:grid;grid-template-columns:minmax(0,9rem) 1fr 3rem;
gap:12px;align-items:center;font:12px monospace;margin:8px 0;overflow-wrap:anywhere}}
.track{{height:16px;background:#eff3f6}}.bar{{display:block;height:100%;background:#167fa2}}
.dots{{position:relative;height:40px;border-bottom:1px solid #cbd5e1;margin:12px 0 0}}
.dot{{position:absolute;width:8px;height:8px;margin-left:-4px;border-radius:50%;background:#167fa2}}
.dot-scale{{display:flex;justify-content:space-between;font:12px monospace;color:#526170;margin:4px 0 12px}}
.methodology{{margin:24px 0;border:1px solid #b6d9e4;border-radius:12px;overflow:hidden}}
.methodology header{{padding:20px;background:#eef8fb;border-bottom:1px solid #b6d9e4}}
.methodology h2,.methodology h3{{margin:4px 0 12px}}.methodology p{{font-size:13px}}
.eyebrow{{font:11px monospace;color:#167fa2;text-transform:uppercase;letter-spacing:2px}}
.method-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:12px;padding:16px}}
.method-step{{padding:16px;border:1px solid #dce6eb;border-radius:8px;min-width:0;overflow-wrap:anywhere}}
.method-step h3{{font-size:15px}}.method-step h4{{font-size:13px;margin:12px 0 4px}}
.method-step li{{font-size:13px;margin:8px 0}}.methodology details{{padding:16px;border-top:1px solid #dce6eb}}
@media print{{body{{margin:0;max-width:none}}tr{{break-inside:avoid}}}}</style></head>
<body><h1>Statistical results: {text(summary["metric"])}</h1>
{metric_context}
<p>Status: {text(summary["status"])}</p><p>{text(summary["description"])}</p>
{listing(describe_findings(summary["tests"]))}
{distribution_section}
<h2>Statistical tests</h2>{
        table(
            summary["tests"],
            [("effect", "Effect"), ("p_adjusted", "Analytic adjusted p" if resampled else "Adjusted p-value")]
            + ([("permutation_p_adjusted", "Permutation adjusted p")] if resampled else [])
            + [("result", "Analytic result")],
        )
    }
<h2>Comparisons</h2>{
        table(
            effects,
            [
                ("comparison", "Comparison"),
                ("estimate", "Estimate"),
                ("scale", "Scale"),
                ("interval", "Analytic 95% interval" if resampled else "95% interval"),
            ]
            + ([("bootstrap_interval", "Bootstrap 95% interval")] if resampled else [])
            + [
                ("p_adjusted", "Analytic adjusted p" if resampled else "Adjusted p-value"),
                ("result", "Result"),
            ],
        )
    }
{resampling_note}
<p>A result without clear evidence does not establish that the effect is zero.
Statistical evidence does not establish practical importance.</p>
{methodology}
<h2>Limitations and exclusions</h2>{listing(summary["warnings"])}
<details><summary>Reproducibility</summary>
<p>Analysis: {text(analysis.get("id"))} · Created: {text(analysis.get("created_at"))}</p>
<pre>{
        text(
            json.dumps(
                dict(summary.get("provenance", {}), resampling=summary.get("resampling")), indent=2, ensure_ascii=False
            )
        )
    }</pre>
</details>
</body></html>"""
