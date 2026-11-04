"""Descriptive plots preserve the saved analysis population and measurement scale."""

import re
from copy import deepcopy

import pandas as pd
import pytest

from asaree.services.stats_boxplots import box_summary, factor_boxplots, render_boxplots, snapshot_boxplots
from asaree.services.stats_distribution import observed_distribution
from asaree.services.stats_html import render_html
from asaree.services.stats_source import StatsRequest


def saved_report(values):
    source = dict(
        rows=[dict(id=str(i), replicate=i + 1, factors={"A": "low" if i < 4 else "high", "B": str(i % 2)},
                   metrics={"Score": value}) for i, value in enumerate(values)],
        metric_metadata={"Score": {}}, factor_metadata={}, fingerprint="saved",
    )
    config = StatsRequest(metric="Score", factors=["A", "B"]).model_dump()
    output = dict(family="gaussian", n_observations=len(values), summary=dict(
        metric="Score", status="valid", description="Saved report", methods=[], effects=[], tests=[], warnings=[],
        distribution=observed_distribution(values),
        metric_info=dict(description="Measured score", unit="ms", coding=[]),
    ))
    return source, config, output


def test_tukey_boxes_use_linear_quartiles_observed_whiskers_and_distinct_outliers():
    assert box_summary([0, 1, 2, 3, 4, 5, 100, 100]) == dict(
        n=8, q1=1.75, median=3.5, q3=28.75, low=0, high=5, outliers=[100],
    )
    assert box_summary([3]) == dict(n=1, q1=3, median=3, q3=3, low=3, high=3, outliers=[])


def test_factor_and_interaction_groups_keep_observation_counts_and_skip_higher_orders():
    frame = pd.DataFrame(dict(
        factor_0=["a", "a", "a", "b"], factor_1=["x", "x", "y", "x"],
        factor_2=["i", "j", "i", "j"], outcome=[1, 3, 5, 9],
    ))
    plots = factor_boxplots(frame, ["A", "B", "C"], max_order=3)
    assert len(plots["main"]) == len(plots["interaction"]) == 3
    assert plots["main"][0]["groups"][0]["n"] == 3
    pair = plots["interaction"][0]
    assert pair["factors"] == ["A", "B"]
    assert [group["levels"] for group in pair["groups"]] == [["a", "x"], ["a", "y"], ["b", "x"]]
    assert sum(group["n"] for group in pair["groups"]) == 4
    assert not factor_boxplots(frame, ["A", "B", "C"], max_order=1)["interaction"]


def test_snapshot_recovery_does_not_refit_or_mutate_saved_data(monkeypatch):
    source, config, output = saved_report([1, 2, 3, 4, 5, 6, 7, 8])
    original = deepcopy((source, config, output))
    monkeypatch.setattr("asaree.services.stats_source.compute", lambda *_: pytest.fail("must not refit"))
    plots = snapshot_boxplots(output, source, config)
    assert len(plots["main"]) == 2
    assert len(plots["interaction"]) == 1
    assert (source, config, output) == original
    output["summary"]["distribution"]["n"] += 1
    assert snapshot_boxplots(output, source, config) is None
    output["summary"]["distribution"]["n"] -= 1
    output["summary"]["distribution"]["maximum"] = 99
    assert snapshot_boxplots(output, source, config) is None


def test_snapshot_omits_missing_measurements_and_keeps_binomial_success_count_scale():
    source, config, output = saved_report([1, 2, 3, 4, 5, 6, 7, 8])
    source["rows"].append(dict(id="missing", replicate=9, factors={"A": "low", "B": "0"}, metrics={}))
    config["denominator"] = "Trials"
    for row in source["rows"]:
        row["metrics"]["Trials"] = 10
    output["family"] = "binomial"
    plots = snapshot_boxplots(output, source, config)
    assert sum(group["n"] for group in plots["main"][0]["groups"]) == 8
    assert max(group["high"] for group in plots["main"][0]["groups"]) == 8


def test_html_places_boxes_below_each_test_section_and_formats_header_fields():
    source, config, output = saved_report([1, 2, 3, 4, 5, 6, 7, 8])
    plots = snapshot_boxplots(output, source, config)
    output["summary"]["boxplots"] = plots
    html = render_html(dict(output=output, config=config, created_at="2026-11-04T12:00:00Z"))
    assert html.index("Main-effect tests</h2>") < html.index("Main-effect distributions</h3>")
    assert html.index("Interaction tests</h2>") < html.index("Two-factor interaction distributions</h3>")
    assert html.index("Main-effect distributions</h3>") < html.index("Interaction tests</h2>")
    assert "Score (ms)" in html
    assert "A levels · boxes within each group: B levels" in html
    assert '<header class="report-header">' in html
    assert "<dt>Unit</dt><dd>ms</dd>" in html
    assert "<dt>Analyzed observations</dt><dd>8</dd>" in html
    assert "<script" not in html
    del output["summary"]["boxplots"]
    assert "Main-effect distributions</h3>" in render_html(dict(output=output, config=config), source=source)


def test_sparse_interaction_rendering_escapes_labels_and_preserves_empty_combinations():
    frame = pd.DataFrame(dict(factor_0=["<script>", "b"], factor_1=["x", "y"], outcome=[-3, -3]))
    plots = factor_boxplots(frame, ["Agent:model", "Prompt"])
    html = render_boxplots(plots, "interaction", "Score<script>")
    assert "Agent › model" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert html.count("No observations") == 2
    assert not re.search(r"\b(?:nan|inf)\b", html)
    assert "within 1.5 interquartile ranges" in html
