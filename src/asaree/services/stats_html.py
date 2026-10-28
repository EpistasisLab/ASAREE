"""Self-contained, script-free HTML from the same summary the application shows."""

from __future__ import annotations

import json
import math
from html import escape

from asaree.services.stats_distribution import saved_distribution_plot
from asaree.services.stats_findings import describe_findings

DISTRIBUTION_NAMES = {
    "gaussian": "Gaussian (normal)", "gamma": "Gamma (log link)", "lognormal": "Lognormal",
    "poisson": "Poisson (log link)", "negbin": "Negative binomial (NB2, log link)", "binomial": "Binomial (logit link)",
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


def model_fit_panel(summary, family, text):
    """Selected model, AIC and the candidate comparison; older reports fall back to provenance."""
    fit = summary.get("model_fit") or {}
    candidates = [dict(row) for row in fit.get("candidates") or summary.get("provenance", {}).get("candidates", [])]
    if not family or not candidates:
        return ""
    usable = [row["aic"] for row in candidates if row.get("usable") and isinstance(row.get("aic"), int | float)]
    for row in candidates:
        if "delta_aic" not in row:
            row["delta_aic"] = row["aic"] - min(usable) if row.get("usable") and usable and row.get("aic") else None
        row.setdefault("name", DISTRIBUTION_NAMES.get(row["family"], row["family"]))
        row.setdefault(
            "status",
            "Selected" if row["family"] == family else "Usable, not selected" if row.get("usable") else "Excluded",
        )
    selected = next((row for row in candidates if row["family"] == family), {})
    facts = [
        ("Selected model", fit.get("selected_name") or DISTRIBUTION_NAMES.get(family, family)),
        ("AIC", display(selected.get("aic")) if selected.get("aic") is not None else "Undefined"),
    ]
    if fit.get("n"):
        facts.append(("Runs in every candidate fit", fit["n"]))
    if fit.get("n_parameters"):
        facts.append(("Estimated parameters", fit["n_parameters"]))
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{text(value)}</td>"
            for value in (
                row["name"],
                display(row.get("aic")) if row.get("aic") is not None else "—",
                display(row.get("delta_aic")) if row.get("delta_aic") is not None else "—",
                row["status"],
                row.get("reason") or "—",
            )
        )
        + "</tr>"
        for row in candidates
    )
    diagnostics = fit.get("diagnostics") or []
    diagnostic_table = (
        "<table><caption>Diagnostics for the selected model</caption><thead><tr><th scope='col'>Diagnostic</th>"
        "<th scope='col'>Value</th><th scope='col'>Reading</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{text(row['label'])}</td><td>{text(display(row.get('value')))}</td>"
            f"<td>{text(row.get('note'))}</td></tr>"
            for row in diagnostics
        )
        + "</tbody></table>"
        if diagnostics
        else ""
    )
    note = fit.get("note") or (
        "Lower AIC means better relative fit among comparable candidates fitted to the same runs; "
        "it does not show that the selected model fits adequately in absolute terms."
    )
    return (
        '<section class="model-fit" aria-label="Model fit"><h3>Model fit</h3><dl class="fit-summary">'
        + "".join(f"<div><dt>{text(label)}</dt><dd>{text(value)}</dd></div>" for label, value in facts)
        + "</dl>"
        + (f"<p>{text(fit['rule'])}</p>" if fit.get("rule") else "")
        + "<table><caption>Candidate models</caption><thead><tr><th scope='col'>Distribution</th>"
        "<th scope='col'>AIC</th><th scope='col'>ΔAIC vs best usable</th><th scope='col'>Status</th>"
        f"<th scope='col'>Exclusion reason</th></tr></thead><tbody>{rows}</tbody></table>"
        + (f"<p>Likelihood basis for the selected model: {text(fit['likelihood_basis'])}.</p>"
           if fit.get("likelihood_basis") else "")
        + f"<p>{text(note)}</p>{diagnostic_table}</section>"
    )


def render_html(analysis: dict) -> str:
    output = analysis.get("output") or {}
    summary = output.get("summary")
    if not summary:
        raise ValueError("This analysis predates the automatic report. Run an automatic analysis to create a report.")

    def text(value):
        return escape(display(value), quote=True)

    def listing(items):
        return "<ul>" + "".join(f"<li>{text(item)}</li>" for item in items) + "</ul>"

    def table(rows, columns, subtitles=None, caption=""):
        """Columns are (key, title); subtitles map a key to the method shared by that column."""
        if not rows:
            return "<p>No statistical comparisons are available.</p>"
        subtitles = subtitles or {}
        head = "".join(
            f"<th scope='col'>{text(title)}"
            + (f'<small class="column-method">{text(subtitles[key])}</small>' if subtitles.get(key) else "")
            + "</th>"
            for key, title in columns
        )

        def cell(row, key):
            content = escape(display(row.get(key), key in {"p_adjusted", "permutation_p_adjusted"}))
            if key == "p_adjusted":
                if row.get(key) is None and row.get("unavailable_reason"):
                    content = "Unavailable" + small(row["unavailable_reason"])
                elif row.get(key) is not None and not subtitles.get(key):
                    # Methods differ between rows, so each analytic cell names its own.
                    content += small(row.get("method"))
            if key == "estimate" and row.get("p_adjusted") is None and row.get("unavailable_reason"):
                content += small("Descriptive only")
            if key == "permutation_p_adjusted":
                if row.get(key) is None:
                    content += small(row.get("permutation_status"))
                else:
                    content += small(row.get("permutation_result"))
                content += small(row.get("resampling_label"))
            if key == "interval":
                if row.get("ci_low") is None and row.get("unavailable_reason"):
                    content = "Unavailable"
                elif row.get("ci_low") is not None and not subtitles.get(key):
                    content += small(row.get("interval_method"))
            if key == "bootstrap_interval":
                detail = (
                    row.get("interval_agreement")
                    if row.get("bootstrap_ci_low") is not None
                    else row.get("bootstrap_status")
                )
                content += small(detail)
            return f"<td>{content}</td>"

        body = "".join("<tr>" + "".join(cell(row, key) for key, _ in columns) + "</tr>" for row in rows)
        caption = f"<caption>{text(caption)}</caption>" if caption else ""
        return f"<table>{caption}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    inference_reason = summary.get("inference_reason")

    def small(value):
        if value and inference_reason and inference_reason.rstrip(".") in str(value):
            # One prominent notice explains a fit-wide failure; cells point to it.
            value = "Not computed: see the notice above."
        return f'<small class="test-method">{text(value)}</small>' if value else ""

    def plain_table(rows, columns):
        head = "".join(f"<th scope='col'>{text(title)}</th>" for _, title in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{text(row.get(key))}</td>" for key, _ in columns) + "</tr>" for row in rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    effects = [
        {
            **row,
            "comparison": row.get("comparison"),
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
    if family and not model_fit_panel(summary, family, text):
        distribution_section += (
            f"<p>Selected regression model distribution: {text(DISTRIBUTION_NAMES.get(family, family))}</p>"
        )
    distribution = summary.get("distribution")
    if distribution and distribution.get("n", 0) > 0:
        distribution_section += "<h2>Observed distribution</h2>"
        plot = saved_distribution_plot(output)
        if plot and plot.get("bins"):
            bins = plot["bins"]
            step = max(1, math.ceil(max(bin["count"] for bin in bins) / 4))
            maximum = step * 4
            bars = "".join(
                f'<li title="{text(bin["label"])}: {text(bin["count"])} replicates">'
                f'<span class="sr-only">{text(bin["label"])}: {text(bin["count"])} replicates</span>'
                f'<span aria-hidden="true" class="bar" style="height:{100 * bin["count"] / maximum:.4f}%"></span></li>'
                for bin in bins
            )
            ticks = "".join(f'<span>{text(step * tick)}</span>' for tick in (4, 3, 2, 1, 0))
            if plot["kind"] == "histogram":
                low = bins[0].get("lower", distribution["minimum"])
                high = bins[-1].get("upper", distribution["maximum"])
                labels = "".join(f'<span>{text(low + (high - low) * tick / 4)}</span>' for tick in range(5))
                axis = f'<div aria-hidden="true" class="numeric-ticks">{labels}</div>'
            else:
                labels = "".join(f'<span>{text(bin["label"])}</span>' for bin in bins)
                axis = f'<div aria-hidden="true" class="x-ticks" style="grid-template-columns:repeat({len(bins)},minmax(0,1fr))">{labels}</div>'
            caption = "Histogram" if plot["kind"] == "histogram" else "Observed frequencies"
            method = f' · {text(plot["binning"])}' if plot.get("binning") else ""
            unit = f' ({text(info["unit"])})' if info.get("unit") else ""
            kind = "frequencies" if plot["kind"] == "frequency" else "histogram"
            distribution_section += (
                f'<figure aria-label="Observed distribution"><figcaption>{caption}{method}</figcaption>'
                '<div class="chart"><span class="y-title">Replicates</span>'
                f'<div aria-hidden="true" class="y-ticks">{ticks}</div><div class="chart-body">'
                f'<ol class="distribution {kind}">{bars}</ol>'
                f'{axis}'
                f'<p class="x-title">Metric value{unit}</p></div></div></figure>'
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
    distribution_section += model_fit_panel(summary, family, text)
    resampled = bool(summary.get("resampling"))
    resampling_note = ""
    if resampled:
        bootstrap = summary["resampling"]["bootstrap"]
        resampling_note = (
            f"<p>Bootstrap: {text(bootstrap['successful'])}/{text(bootstrap['requested'])} successful refits; "
            f"{text(bootstrap['failed'])} failed. Status: {text(str(bootstrap['status']).rstrip('.'))}.</p>"
            "<p>Resampling p-values: main effects exchange factor labels within strata of the other factors and use "
            "a working-scale OLS F, a sensitivity check that can test a different null than the analytic test. "
            "Interactions resample the fitted model's own HC3 Wald statistic: Freedman–Lane permutation where "
            "errors are exchangeable, otherwise a parametric null-model bootstrap. Each cell names its procedure; "
            "assumptions are in the technical details.</p>"
        )

    def named_entries(rows, name_key):
        return "<ul>" + "".join(
            f"<li><strong>{text(row.get('label') or row.get(name_key))}</strong> — "
            f"{text(row.get('question') or row.get('purpose'))}</li>"
            for row in rows
        ) + "</ul>"

    covariance = summary.get("covariance") or {}
    estimator = covariance.get("computed")
    overview = [
        ("01", "Regression Model Selection", f"<h4>{text(DISTRIBUTION_NAMES.get(family, family))}</h4>"
         + (f"<p>{text(summary['methods'][0])}</p>" if summary["methods"] else "")
         + (f"<p>{text((summary.get('model_fit') or {}).get('rule'))}</p>"
            if (summary.get("model_fit") or {}).get("rule") else "")),
        ("02", "Tests and comparisons",
         "<h4>Factor tests</h4>"
         + (f"<p>Wald chi-square tests of each model term with {text(estimator)} robust covariance.</p>"
            if estimator else "")
         + named_entries(summary["tests"], "effect")
         + "<h4>Comparisons</h4>"
         + "<p>Model-predicted averages, each condition weighted equally.</p>"
         + named_entries(summary["effects"], "comparison")),
        ("03", "Uncertainty checks", resampling_note or "<p>No resampling results were recorded.</p>"),
        ("04", "How to interpret it", listing([
            "Analytic adjusted p-values are Holm-adjusted across the factor tests, and within each comparison "
            "family; resampling p-values are Holm-adjusted separately.",
            "“Analytic result” means the analytic adjusted p-value is below 0.05.",
            "Confidence intervals are not adjusted for multiple comparisons.",
            "A result without clear evidence does not mean the effect is zero.",
            "Results across separate metrics are exploratory.",
        ])),
    ]

    def detail_section(section):
        content = "".join(f"<p>{text(item)}</p>" for item in section.get("items", []))
        if section.get("rows"):
            content += plain_table(section["rows"], section["columns"])
        return f'<section class="detail"><h3>{text(section["title"])}</h3>{content}</section>'

    details = summary.get("details")
    technical = "".join(detail_section(section) for section in details) if details else listing(summary["methods"])
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
        + technical + '</details></section>'
    )
    columns = summary.get("column_methods") or {}
    test_methods, effect_methods = columns.get("tests") or {}, columns.get("effects") or {}

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'">
<title>ASAREE statistics — {text(summary["metric"])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;color:#202c38;max-width:1100px;margin:40px auto;padding:0 24px}}
h1,h2{{line-height:1.25}}table{{border-collapse:collapse;width:100%;font-size:14px;margin:16px 0}}
th,td{{border-bottom:1px solid #cbd5e1;text-align:left;padding:10px;vertical-align:top;overflow-wrap:anywhere}}
th{{background:#eff3f6}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}
.test-method{{display:block;margin-top:4px;color:#526170;font-size:12px}}
.column-method{{display:block;margin-top:4px;color:#526170;font-size:11px;font-weight:normal}}
.model-fit{{margin:20px 0;padding:20px;border:1px solid #dce6eb;border-radius:8px}}.model-fit h3{{margin:0 0 12px}}
.fit-summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:0}}
.fit-summary dt{{font-size:12px;color:#526170}}.fit-summary dd{{margin:2px 0 0;font:15px monospace}}
.detail h3{{font-size:14px;margin:20px 0 4px}}
.notice{{padding:12px 16px;border:1px solid #d97706;border-radius:8px;background:#fff7ed}}
.detail table{{font-size:12px}}
figure{{margin:20px 0;padding:20px;border:1px solid #dce6eb;border-radius:8px;break-inside:avoid}}
figcaption{{font-size:12px;color:#526170;margin-bottom:16px}}
.chart{{display:flex;gap:8px}}.chart-body{{flex:1;min-width:0}}
.y-title{{writing-mode:vertical-rl;transform:rotate(180deg);height:180px;text-align:center;font-size:12px}}
.y-ticks{{display:flex;height:180px;flex-direction:column;justify-content:space-between;text-align:right;font:11px monospace;color:#526170}}
.distribution{{display:flex;gap:1px;list-style:none;padding:0;margin:0;height:180px;border-bottom:1px solid #94a3b8;
background:repeating-linear-gradient(to top,transparent 0,transparent calc(25% - 1px),#dce6eb 25%)}}
.distribution.frequencies{{gap:8px}}.distribution li{{display:flex;align-items:flex-end;flex:1;min-width:0}}
.bar{{display:block;width:100%;background:#318eaa;border:1px solid #167fa2;box-sizing:border-box}}
.x-ticks{{display:grid;margin-top:8px;font:10px monospace;color:#526170;overflow-wrap:anywhere}}
.x-ticks span{{text-align:center}}.x-title{{text-align:center;font-size:12px;color:#526170}}
.numeric-ticks{{display:flex;justify-content:space-between;gap:4px;margin-top:8px;font:10px monospace;color:#526170}}
.sr-only{{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}}
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
{f'<p role="alert" class="notice"><strong>Inference unavailable.</strong> {text(inference_reason)} '
 'Tests, confidence intervals, bootstrap intervals and resampling checks were not computed.</p>'
 if inference_reason else ""}
<h2>Statistical tests</h2>{
        table(
            [dict(row, effect=row.get("label") or row["effect"]) for row in summary["tests"]],
            [("effect", "Effect"), ("p_adjusted", "Analytic adjusted p" if resampled else "Adjusted p-value")]
            + ([("permutation_p_adjusted", "Resampling adjusted p")] if resampled else [])
            + [("result", "Analytic result (adjusted p < 0.05)")],
            dict(p_adjusted=test_methods.get("p_adjusted"), permutation_p_adjusted=test_methods.get("permutation")),
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
                ("result", "Analytic result (adjusted p < 0.05)"),
            ],
            dict(
                interval=effect_methods.get("interval"),
                bootstrap_interval=effect_methods.get("bootstrap"),
                p_adjusted=effect_methods.get("p_adjusted"),
            ),
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
