"""Automatic metric recognition, native inference, and user-facing report contracts."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import numpy as np
import pytest
import statsmodels.api as sm

from asaree.services.stats_automatic import coerce_values, metric_information, plan_metric
from asaree.services.stats_distribution import distribution_plot, saved_distribution_plot
from asaree.services.stats_findings import describe_findings
from asaree.services.stats_html import render_html
from asaree.services.stats_prototypes import StatsRequest, catalog, compute, metric_definitions, prepare_frame
from asaree.stats.analysis import analyze as native_analyze


def source_for(values, name="measurement", metadata=None):
    return dict(
        rows=[
            dict(id=str(i), replicate=i // 2 + 1, factors={"Treatment": "A" if i % 2 == 0 else "B"}, metrics={name: v})
            for i, v in enumerate(values)
        ],
        fingerprint="demo-snapshot",
        design_revision=1,
        design_revision_id="demo",
        excluded_row_replicates=0,
        metric_metadata={name: metadata or {}},
    )


def analyze(values, name="measurement", metadata=None):
    source = source_for(values, name, metadata)
    config = StatsRequest(metric=name, n_boot=20, n_perm=99).model_dump(mode="json")
    return compute(source, config)


@pytest.mark.parametrize(
    "values,name,metadata,kind",
    [
        ([False, True] * 10, "Custom", {}, "binary"),
        (["yes", "no"] * 10, "Custom", {}, "binary"),
        (["3.2", "4.5", "-1.1"] * 10, "Custom", {}, "continuous"),
        ([1, 2, 3] * 10, "Calls", {}, "count"),
        ([1, 2, 3] * 10, "Duration", {"unit": "seconds"}, "continuous"),
        ([0.1, 0.5, 0.9] * 10, "Custom", {}, "proportion"),
        (["low", "medium", "high"] * 10, "Custom", {}, "ordinal"),
        ([1, 2, 3, 4, 5] * 10, "Satisfaction rating", {}, "ordinal"),
    ],
)
def test_detects_supported_constructions(values, name, metadata, kind):
    assert plan_metric(source_for(values, name, metadata), name)["measurement_type"] == kind


@pytest.mark.parametrize(
    "values,reason",
    [
        (["red", "blue", "green"] * 10, "ordering"),
        (["The patient improved.", "No change."] * 10, "ordering"),
        ([{"nested": 3}, {"nested": 4}] * 10, "structured"),
        ([1, "invalid"] * 10, "mixed"),
        ([None] * 10, "No measured"),
    ],
)
def test_unsupported_values_explain_why_without_inference(values, reason):
    result = analyze(values)
    assert result["validity"] == "unavailable"
    assert reason in " ".join(result["summary"]["warnings"])
    assert result["summary"]["effects"] == []


def test_numeric_text_is_converted_without_modifying_snapshot():
    rng = np.random.default_rng(22)
    values = [str(v) for v in rng.normal(0, 1, 100)]
    source = source_for(values)
    original = deepcopy(source)
    output = compute(source, StatsRequest(metric="measurement").model_dump())
    assert output["validity"] == "valid"
    assert any("String conversion" in s for s in output["summary"]["methods"])
    assert source == original


def test_native_gaussian_effect_and_interval_match_reference(monkeypatch):
    import socket

    def reject_network(*args, **kwargs):
        raise AssertionError("Statistics and report generation must not make network or LLM calls")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    rng = np.random.default_rng(7)
    group = np.arange(160) % 2
    y = 2 * group + rng.normal(0, 1, 160)
    result = analyze(y.tolist())
    assert result["validity"] == "valid"
    effect = result["summary"]["effects"][0]
    x = np.column_stack([np.ones(160), np.where(group == 0, 1, -1)])
    reference = sm.OLS(y, x).fit(cov_type="HC3").t_test([0, 2])
    np.testing.assert_allclose(effect["estimate"], reference.effect.item(), atol=1e-8)
    np.testing.assert_allclose([effect["ci_low"], effect["ci_high"]], reference.conf_int().ravel(), atol=1e-8)
    assert "Wald z-test" in effect["method"]
    assert "Wald chi-square test" in result["summary"]["tests"][0]["method"]
    assert any("Factor tests — Target: Treatment" in line for line in result["summary"]["methods"])
    assert result["summary"]["covariance"]["computed"] == "HC3"
    distribution = result["summary"]["distribution"]
    assert distribution["n"] == len(y)
    assert sum(bin["count"] for bin in distribution["plot"]["bins"]) == len(y)
    np.testing.assert_allclose(
        [distribution[key] for key in ("minimum", "maximum", "mean", "sd")],
        [y.min(), y.max(), y.mean(), y.std(ddof=1)],
    )
    html = render_html(dict(output=result))
    assert "Observed distribution" in html
    assert "Each dot is one replicate" not in html
    assert "Freedman–Diaconis" in html
    assert "Regression Model Selection" in html
    assert "<dt>Selected model</dt><dd>Gaussian (normal)</dd>" in html
    assert "ΔAIC vs best usable" in html
    assert "Standard deviation" in html
    assert "accounting for the experimental factors" in html


def test_html_handles_saved_reports_without_distribution_summaries():
    summary = dict(metric="Legacy", status="valid", description="Saved report",
                   findings=[], effects=[], tests=[], methods=[], warnings=[])
    html = render_html(dict(output=dict(family="gamma", summary=summary)))
    assert "Selected regression model distribution: Gamma" in html
    assert "Observed distribution" not in html


def test_plain_language_findings_describe_agreement_interactions_and_disagreement():
    findings = describe_findings([
        dict(effect="Effort", p_adjusted=.3, permutation_p_adjusted=.4),
        dict(effect="Feature budget", p_adjusted=.01, permutation_p_adjusted=.02),
        dict(effect="Effort:Feature budget", p_adjusted=.4, permutation_p_adjusted=None),
        dict(effect="Other", p_adjusted=.01, permutation_p_adjusted=.4),
        dict(effect="Missing", p_adjusted=None, permutation_p_adjusted=None),
    ])
    assert findings[0] == (
        "Neither the main test nor the permutation check found clear evidence that Effort has an effect on this metric."
    )
    assert findings[1] == (
        "Both the main test and the permutation check found evidence that Feature budget has an effect on this metric."
    )
    assert findings[2] == (
        "The main test found no clear evidence that the effect of Effort changes across Feature budget settings. "
        "A permutation check was not available for this comparison."
    )
    assert "two methods differ" in findings[3]
    assert "main statistical test for Missing was unavailable" in findings[4]


def test_histogram_conserves_observations_and_keeps_extreme_values():
    values = np.arange(100).tolist() + [500]
    plot = distribution_plot(values)
    assert plot["kind"] == "histogram"
    assert sum(bin["count"] for bin in plot["bins"]) == len(values)
    assert plot["bins"][0]["lower"] == 0
    assert plot["bins"][-1]["upper"] == 500
    assert plot["bins"][-1]["count"] >= 1
    assert "values" not in plot
    assert distribution_plot([]) is None


def test_histogram_uses_freedman_diaconis_width():
    plot = distribution_plot(np.arange(2000.0).tolist())
    assert "values" not in plot
    assert sum(bin["count"] for bin in plot["bins"]) == 2000
    expected = np.histogram_bin_edges(np.arange(2000.0), bins="fd")
    np.testing.assert_allclose([bin["lower"] for bin in plot["bins"]] + [plot["bins"][-1]["upper"]], expected)


def test_histogram_falls_back_for_zero_iqr_and_caps_outlier_driven_bins():
    flat = distribution_plot([0] * 100 + list(range(1, 20)))
    assert flat["binning"] == "Sturges (zero interquartile range)"
    assert sum(bin["count"] for bin in flat["bins"]) == 119
    extreme = distribution_plot(list(range(100)) + [1e9])
    assert len(extreme["bins"]) == 40
    assert "limited to 40 bins" in extreme["binning"]
    assert sum(bin["count"] for bin in extreme["bins"]) == 101
    assert extreme["bins"][-1]["upper"] == 1e9


def test_saved_dot_strip_is_rebinned_from_saved_values():
    values = list(range(100))
    output = dict(summary=dict(distribution=dict(plot=dict(kind="histogram", values=values, bins=[]))))
    assert saved_distribution_plot(output) == distribution_plot(values)


def test_frequencies_preserve_category_labels_and_counts():
    plot = distribution_plot([0, 1, 1, 2], ["Low", "Medium", "High"])
    assert plot["kind"] == "frequency"
    assert plot["bins"] == [dict(label="Low", count=1), dict(label="Medium", count=2), dict(label="High", count=1)]


def test_older_reports_plot_only_saved_observations_matching_the_summary_scale():
    output = dict(family="gaussian", summary=dict(distribution=dict(n=3, minimum=1, maximum=3)),
                  tables=dict(residuals=[dict(observed=value) for value in (1, 2, 3)]))
    assert saved_distribution_plot(output)["bins"][-1]["count"] == 1
    output["tables"]["residuals"][-1]["observed"] = 30
    assert saved_distribution_plot(output) is None


@pytest.mark.parametrize("robust", [False, True])
def test_reported_gaussian_test_names_follow_native_reference_distributions(robust):
    source = source_for(np.random.default_rng(9).normal(size=100).tolist())
    config = StatsRequest(metric="measurement", factors=["Treatment"], family="gaussian").model_dump()
    config["robust"] = robust
    frame = prepare_frame(source, config)
    output = native_analyze(frame, config)
    x = np.column_stack([np.ones(100), np.where(np.arange(100) % 2 == 0, 1, -1)])
    reference = sm.OLS(frame.outcome, x).fit(**({"cov_type": "HC3"} if robust else {}))
    term = reference.wald_test([0, 1], scalar=True)
    contrast = reference.t_test([0, 2])
    row = output["tables"]["tests"].iloc[0]
    effect = output["tables"]["contrasts"].iloc[0]
    assert ("Wald F-test" if term.distribution == "F" else "Wald chi-square test") in row.p_method
    assert ("Student's t-test" if contrast.distribution == "t" else "Wald z-test") in effect.p_method
    np.testing.assert_allclose(row.p, float(term.pvalue))
    np.testing.assert_allclose(effect.p, float(contrast.pvalue))


@pytest.mark.parametrize("kind", ["binary", "count", "proportion", "positive", "ordinal"])
def test_nongaussian_comparisons_have_response_scale_uncertainty(kind):
    rng = np.random.default_rng(10)
    group = np.arange(240) % 2
    if kind == "binary":
        values = rng.binomial(1, 0.25 + 0.3 * group).tolist()
    elif kind == "count":
        values = rng.poisson(2 + 2 * group).tolist()
    elif kind == "proportion":
        values = rng.beta(2 + group * 2, 5).tolist()
    elif kind == "positive":
        values = rng.gamma(3, 2 + group).tolist()
    else:
        values = rng.choice(["low", "medium", "high"], 240).tolist()
    result = analyze(values)
    assert result["validity"] == "valid", result["summary"]["warnings"]
    effect = result["summary"]["effects"][0]
    assert effect["ci_low"] < effect["ci_high"]
    assert 0 <= effect["p_adjusted"] <= 1
    assert "Delta-method Wald z-test" in effect["method"]
    assert "Mean predicted" in effect["target"]
    assert effect["bootstrap_ci_low"] < effect["bootstrap_ci_high"]
    assert result["summary"]["resampling"]["bootstrap"]["attempted"] == 20
    assert result["summary"]["tests"][0]["permutation_p_adjusted"] is not None
    html = render_html(dict(output=result))
    assert effect["method"] in html
    assert "<h3>Bootstrap intervals</h3>" in html
    assert "Analytic 95% interval" in html
    assert "Bootstrap 95% interval" in html
    assert "Resampling adjusted p" in html
    json.dumps(result, allow_nan=False)


def test_denominator_selects_binomial_even_with_default_measurement_type():
    rng = np.random.default_rng(20)
    source = source_for(rng.binomial(12, 0.4, 120).tolist(), "successes")
    for row in source["rows"]:
        row["metrics"]["trials"] = 12
    output = compute(source, StatsRequest(metric="successes", n_boot=20, n_perm=99).model_dump())
    assert output["family"] == "binomial"
    assert output["validity"] == "valid"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"dependence": {"paired": True}}, "dependence model"),
        ({"protocol_revision_ids": ["first", "second"]}, "published protocols"),
    ],
)
def test_unsupported_dependence_and_mixed_protocols_withhold_tests(change, reason):
    source = source_for(np.random.default_rng(1).normal(size=100).tolist())
    source.update(change)
    result = compute(source, StatsRequest(metric="measurement").model_dump())
    assert result["validity"] == "unavailable"
    assert reason in result["summary"]["warnings"][0]


def test_separated_binary_data_does_not_produce_conclusions():
    result = analyze([0, 1] * 50)
    assert result["validity"] == "unavailable"
    assert not result["summary"]["effects"]


def test_declared_opaque_and_unmeasured_metrics_are_visible_in_catalog():
    source = source_for([{"text": "opaque"}] * 10, "Custom")
    source["metric_metadata"]["Not measured"] = dict(kind="custom")
    entries = {m["name"]: m for m in catalog(source)["metrics"]}
    assert entries["Custom"]["n"] == 10
    assert entries["Not measured"]["n"] == 0


def test_metric_identities_come_from_declarations_and_producer_bindings():
    definitions = metric_definitions(
        {"metrics": [dict(id="a", name="Latency", kind="runtime")]},
        dict(
            metrics=[
                dict(id="a", name="Latency", unit="seconds"),
                dict(id="b", name="Explanation", value_type="opaque"),
            ],
            producers=[dict(producer_id="asaree.runtime", kind="runtime", outputs={"duration_seconds": "a"})],
        ),
    )
    assert definitions["Latency"]["output_key"] == "duration_seconds"
    assert definitions["Explanation"]["value_type"] == "opaque"


def test_html_escapes_custom_labels_and_never_embeds_images_or_scripts():
    result = analyze(
        np.random.default_rng(5).normal(size=100).tolist(),
        "<script>alert(1)</script>",
        metadata={"description": '<img src=x onerror="alert(1)">', "unit": "<svg>"},
    )
    html = render_html(dict(id="example", created_at="demo", output=result))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html
    assert "<svg" not in html and "<img" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    assert "Unit: &lt;svg&gt;" in html
    assert "What ran and why" in html and "Analytic adjusted p" in html
    assert "demo-snapshot" in html


def test_builtin_descriptions_require_producer_identity_not_just_a_matching_name():
    builtin = source_for([1, 2], "Latency", {"kind": "runtime", "output_key": "duration_seconds"})
    info = metric_information(builtin, "Latency")
    assert info["description"] == "Wall-clock duration of the run."
    assert info["unit"] == "seconds"
    custom = source_for([1, 2], "Duration", {"kind": "custom"})
    assert metric_information(custom, "Duration")["description"] == "Description not provided."


def test_numeric_coercion_does_not_accept_infinite_or_non_numeric_strings():
    assert coerce_values(["1e999", "2"])[1] == {}
    assert coerce_values(["NaN", "2"])[1] == {}


def test_structured_treatments_use_declared_short_labels_without_merging_conditions():
    source = source_for(np.random.default_rng(9).normal(size=100).tolist())
    for i, row in enumerate(source["rows"]):
        row["factors"]["Treatment"] = {"model": "small" if i % 2 == 0 else "large", "effort": "low"}
    source["factor_metadata"] = {
        "Treatment": dict(
            levels=[dict(model="small", effort="low"), dict(model="large", effort="low")],
            level_labels=["Small model", "Large model"],
        )
    }
    output = compute(source, StatsRequest(metric="measurement").model_dump())
    assert output["validity"] == "valid"
    comparison = output["summary"]["effects"][0]["comparison"]
    assert "Small model" in comparison and "Large model" in comparison
    assert "{'model'" not in comparison


def test_constant_metric_reports_unavailable_instead_of_zero_effect():
    result = analyze([4] * 40)
    assert result["validity"] == "unavailable"
    assert "constant" in result["summary"]["warnings"][0]


def test_missing_measurement_report_retains_the_producers_explanation():
    source = source_for([None] * 10, "Custom")
    source["metric_errors"] = {"Custom": ["The Agent did not call the configured metric tool."]}
    output = compute(source, StatsRequest(metric="Custom").model_dump())
    assert "did not call the configured metric tool" in output["summary"]["warnings"][0]


def test_numeric_string_ratings_keep_the_inferred_category_mapping():
    values = np.random.default_rng(2).choice(["1", "2", "3", "4", "5"], 200).tolist()
    result = analyze(values, "Rating")
    assert result["validity"] == "valid"
    assert result["family"] == "ordinal_logit"


def test_declared_order_takes_precedence_over_numeric_coercion():
    source = source_for(["1", "2", "3"] * 30, metadata={"ordinal_levels": ["1", "2", "3"]})
    plan = plan_metric(source, "measurement")
    assert plan["ordinal_levels"] == ["1", "2", "3"]
    assert plan["conversion"] == {}


def test_declared_proportion_with_only_endpoints_uses_binary_model():
    values = np.random.default_rng(8).binomial(1, 0.4, 160).tolist()
    output = analyze(values, metadata={"measurement_type": "proportion"})
    assert output["family"] == "binomial"
    assert output["validity"] == "valid"


def test_empty_declared_ordinal_categories_have_an_actionable_explanation():
    output = analyze(["low", "high"] * 60, metadata={"ordinal_levels": ["low", "medium", "high"]})
    assert output["validity"] == "unavailable"
    assert "no observations" in output["summary"]["warnings"][0]


def test_two_unordered_category_labels_use_binary_probability_coding():
    values = np.random.default_rng(14).choice(["red", "blue"], 200).tolist()
    output = analyze(values)
    assert output["family"] == "binomial"
    assert output["validity"] == "valid"
    assert any("category coded 1" in method for method in output["summary"]["methods"])
    assert any("blue = 0" in line and "red = 1" in line for line in output["summary"]["metric_info"]["coding"])


def test_ordered_labels_are_normalized_without_losing_their_order():
    values = np.random.default_rng(15).choice(["Low", "low", " Medium ", "HIGH"], 200).tolist()
    output = analyze(values)
    assert output["family"] == "ordinal_logit"
    assert output["validity"] == "valid"


@pytest.mark.parametrize("kind", ["gaussian", "binary"])
def test_seeded_null_simulations_do_not_systematically_find_effects(kind):
    """A coarse calibration check, not a claim of universal statistical validity."""
    rng = np.random.default_rng(96)
    rejections = 0
    coverage = 0
    for _ in range(40):
        values = rng.normal(size=160) if kind == "gaussian" else rng.binomial(1, 0.4, 160)
        output = analyze(values.tolist())
        assert output["validity"] == "valid"
        effect = output["summary"]["effects"][0]
        rejections += effect["p_adjusted"] < 0.05
        coverage += effect["ci_low"] <= 0 <= effect["ci_high"]
    assert rejections <= 8
    assert coverage >= 32


def test_log_normal_aic_is_converted_to_the_original_measurement_scale():
    rng = np.random.default_rng(19)
    y = rng.lognormal(3, 0.25, 200)
    output = analyze(y.tolist(), "Duration")
    candidate = next(c for c in output["summary"]["provenance"]["candidates"] if c["family"] == "lognormal")
    group = np.arange(200) % 2
    reference = sm.OLS(np.log(y), np.column_stack([np.ones(200), group])).fit()
    expected = reference.aic + 2 * np.log(y).sum() + 2
    np.testing.assert_allclose(candidate["aic"], expected, rtol=1e-10)


async def test_database_snapshot_reads_only_scoped_declared_runtime_observations(monkeypatch):
    from asaree.services import stats_prototypes as service

    experiment, revision_id, replicate_id, run_id = [uuid4() for _ in range(4)]
    plan = dict(
        metrics=[dict(id="duration", name="Latency", unit="seconds")],
        producers=[dict(producer_id="asaree.runtime", kind="runtime", outputs={"duration_seconds": "duration"})],
    )
    revision = SimpleNamespace(id=revision_id, revision=2, design_spec={})
    replicate = SimpleNamespace(
        id=replicate_id,
        replicate_number=1,
        run_id=run_id,
        factor_values={"Treatment": "A"},
        metric_values={"Custom": "yes"},
    )
    run = SimpleNamespace(
        id=run_id,
        protocol_revision_id=uuid4(),
        replicate_result_id=replicate_id,
        status="completed",
        usage_summary={"duration_seconds": 2.3, "not_a_metric": 999},
        attempt_result=dict(
            measurement_plan_snapshot=plan,
            measurement=dict(observations=[dict(metric_name="Latency", status="measured", value=2.5)]),
        ),
    )
    monkeypatch.setattr(service, "get_current_revision", AsyncMock(return_value=revision))
    monkeypatch.setattr(service, "list_replicates", AsyncMock(return_value=[replicate]))
    db = AsyncMock()
    db.scalar.return_value = 1
    db.get.return_value = SimpleNamespace(measurement_plan=plan)
    db.scalars.side_effect = [SimpleNamespace(all=lambda: [run]), SimpleNamespace(all=lambda: [])]
    snapshot = await service.read_source(db, experiment)
    assert snapshot["rows"][0]["metrics"] == {"Custom": "yes", "Latency": 2.5}
    assert snapshot["metric_metadata"]["Latency"]["kind"] == "runtime"
    assert "protocols.experiment_id" in str(db.scalars.call_args_list[0].args[0])
    assert "not_a_metric" not in {metric["name"] for metric in catalog(snapshot)["metrics"]}


async def test_html_export_enforces_experiment_ownership(monkeypatch):
    from fastapi import HTTPException

    from asaree.api import stats_prototypes as api

    monkeypatch.setattr(api, "_get_owned_experiment", AsyncMock(side_effect=HTTPException(404)))
    db = AsyncMock()
    with pytest.raises(HTTPException):
        await api.report(uuid4(), uuid4(), SimpleNamespace(), db)
    db.get.assert_not_awaited()


async def test_automatic_batch_pins_one_snapshot_before_enqueuing(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from asaree.api import stats_prototypes as api

    source = source_for(["yes", "no"] * 30, "Custom")
    source["metric_metadata"]["Unmeasured"] = dict(kind="custom")
    monkeypatch.setattr(api, "_get_owned_experiment", AsyncMock())
    monkeypatch.setattr(api, "read_source", AsyncMock(return_value=source))
    db, saved = AsyncMock(), []
    db.scalar.return_value = None

    def add(row):
        row.id, row.created_at = uuid4(), datetime.now(UTC)
        saved.append(row)

    async def enqueue(*args, **kwargs):
        db.commit.assert_awaited_once()
        assert all(row.request["source"] is source for row in saved)
        assert any(call.args[0].is_delete for call in db.execute.call_args_list)

    db.add = MagicMock(side_effect=add)
    monkeypatch.setattr(api, "_get_pool", AsyncMock(return_value=SimpleNamespace(enqueue_job=enqueue)))
    experiment_id, version_id = uuid4(), uuid4()
    results = await api.automatic(experiment_id, SimpleNamespace(), db, version_id)
    api.read_source.assert_awaited_once_with(db, experiment_id, version_id)
    assert {row["config"]["metric"] for row in results} == {"Custom", "Unmeasured"}
    assert all(row["config"]["family"] == "auto" and "backend" not in row for row in results)


@pytest.mark.parametrize("kind", ["boundary", "hurdle", "overdispersed_trials"])
def test_specialized_custom_models_are_checked_before_being_reported(kind):
    rng = np.random.default_rng(25)
    group = np.arange(240) % 2
    if kind == "boundary":
        values = np.where(rng.random(240) < 0.15, 0, rng.beta(2 + group, 5)).tolist()
        result = analyze(values)
        expected = "zoib"
    elif kind == "hurdle":
        values = np.where(rng.random(240) < 0.3, 0, rng.gamma(3, 2 + group)).tolist()
        result = analyze(values, "Duration", {"unit": "seconds"})
        expected = "gamma_hurdle"
    else:
        source = source_for(rng.binomial(20, rng.beta(2 + group, 3)).tolist(), "successes")
        for row in source["rows"]:
            row["metrics"]["trials"] = 20
        result = compute(source, StatsRequest(metric="successes", n_boot=20, n_perm=99).model_dump())
        expected = "betabinomial"
    assert result["family"] == expected, result["summary"]["warnings"]
    assert result["validity"] == "valid"
    assert result["summary"]["effects"][0]["ci_low"] is not None
    assert "Wald" in result["summary"]["tests"][0]["method"]
    effect = result["summary"]["effects"][0]
    assert "delta-method" in effect["interval_method"].lower()
    assert "delta-method Wald z-test" in effect["method"]
    assert effect["bootstrap_ci_low"] is not None
    assert effect["bootstrap_ci_high"] > effect["bootstrap_ci_low"]
    bootstrap = result["summary"]["resampling"]["bootstrap"]
    assert "Parametric-bootstrap" in bootstrap["method"]
    assert bootstrap["successful"] + bootstrap["failed"] == bootstrap["attempted"] == 20
    assert result["summary"]["tests"][0]["permutation_p_adjusted"] is not None
