"""Self-contained, script-free HTML from the same summary the application shows."""

from __future__ import annotations

import json
import math
from html import escape


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

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'">
<title>ASAREE statistics — {text(summary["metric"])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;color:#202c38;max-width:1100px;margin:40px auto;padding:0 24px}}
h1,h2{{line-height:1.25}}table{{border-collapse:collapse;width:100%;font-size:14px;margin:16px 0}}
th,td{{border-bottom:1px solid #cbd5e1;text-align:left;padding:10px;vertical-align:top;overflow-wrap:anywhere}}
th{{background:#eff3f6}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}
.test-method{{display:block;margin-top:4px;color:#526170;font-size:12px}}
@media print{{body{{margin:0;max-width:none}}tr{{break-inside:avoid}}}}</style></head>
<body><h1>Statistical results: {text(summary["metric"])}</h1>
{metric_context}
<p>Status: {text(summary["status"])}</p><p>{text(summary["description"])}</p>
{listing(summary["findings"])}
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
<details><summary>What ran and why</summary>{listing(summary["methods"])}</details>
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
