"""General design, execution and fixed-template reporting contracts."""

import itertools
import json
import runpy
import sys
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from asaree.services.stats_html import render_html, report_filename
from asaree.services.stats_source import StatsRequest, compute
from asaree.stats.analysis import finish_fit
from asaree.stats.fit_checks import native_fit_checks
from asaree.stats.validation import rejection_summary


def test_download_filename_identifies_experiment_version_metric_and_saved_utc_date():
    saved = dict(
        created_at="2026-10-30T23:30:00-07:00", status="completed", design_revision=2,
        config={"metric": "internal_metric_id"},
        output={"summary": {"metric_definition": {"label": "Success rate (%)"}}},
    )
    assert report_filename(saved, experiment_name="Reasoning Benchmark", protocol_revision=3) == (
        "reasoning-benchmark_v3_success-rate_2026-10-31.html"
    )
    saved["status"] = "incomplete"
    assert report_filename(saved, experiment_name="Reasoning Benchmark", protocol_revision=3).endswith(
        "_2026-10-31_incomplete.html"
    )


def test_download_filename_sanitizes_paths_headers_and_handles_legacy_metadata():
    saved = dict(created_at="2026-10-30", design_revision=2, config={"metric": "Duration"})
    filename = report_filename(saved, experiment_name='../../Café "trial"\r\nInjected: value')
    assert filename == "cafe-trial-injected-value_design-v2_duration_2026-10-30.html"
    assert report_filename({}, experiment_name="") == "experiment_unversioned_metric_undated.html"
    assert len(report_filename(saved, experiment_name="long" * 1000)) < 200


def source_for_design(levels, *, unbalanced=False, missing=False, beta=False):
    rng = np.random.default_rng(53)
    rows = []
    for index, values in enumerate(itertools.product(*levels.values())):
        if missing and index == 0:
            continue
        for replicate in range(8 + (index % 3 if unbalanced else 0)):
            rows.append(
                dict(
                    id=str(len(rows)),
                    replicate=replicate + 1,
                    factors={**dict(zip(levels, values, strict=True)), "Fixed": "same"},
                    metrics={"value": float(rng.beta(3, 5) if beta else rng.normal(-3, 1))},
                )
            )
    return dict(
        rows=rows,
        fingerprint="design-test",
        metric_metadata={"value": {}},
        factor_metadata={
            **{key: {"levels": value} for key, value in levels.items()},
            "Fixed": {"levels": ["same"]},
            "Unrecorded": {"levels": ["x", "y"]},
        },
        design_revision=1,
        design_revision_id="revision",
        excluded_row_replicates=0,
    )


def run(source, **kwargs):
    return compute(source, StatsRequest(metric="value", n_boot=20, n_perm=99, **kwargs).model_dump())


@pytest.mark.parametrize(
    "levels,order,missing",
    [
        ({"Treatment": ["A", "B", "C"]}, 1, False),
        ({"A": ["a", "b", "c"], "B": ["x", "y"], "C": ["i", "j"]}, 3, False),
        ({"A": ["a", "b", "c"], "B": ["x", "y"]}, 1, True),
    ],
)
def test_specification_tracks_multilevel_unbalanced_and_incomplete_factorials(levels, order, missing):
    source = source_for_design(levels, unbalanced=True, missing=missing)
    result = run(source, max_order=order)
    assert result["validity"] == "valid"
    summary = result["summary"]
    spec = summary["design_specification"]
    assert spec["included_factors"] == sorted(levels)
    assert len(spec["terms"]) == sum(
        len(list(itertools.combinations(levels, k))) for k in range(1, min(order, len(levels)) + 1)
    )
    factors = {row["factor"]: row for row in spec["factors"]}
    assert factors["Fixed"]["status"] == "Constant setting"
    assert "no observed" in factors["Unrecorded"]["reason"]
    assert spec["minimum_runs"] != spec["maximum_runs"]
    assert spec["possible_conditions"] - spec["observed_conditions"] == int(missing)
    html = render_html(dict(id="test", created_at="fixed", output=result))
    for title in ("Experimental design", "Covariance and dispersion", "Numerical fitting", "Multiplicity"):
        assert title in html
    for test in summary["tests"]:
        assert test["explanation"].startswith("Tests whether")
        assert test["interpretation"] == test["result"]
        assert test["result"].startswith("Evidence") == (test["p_adjusted"] < summary["alpha"])


def test_explicit_factor_exclusions_and_dependence_are_explained():
    source = source_for_design({"A": ["a", "b"], "B": ["x", "y"]})
    result = run(source, factors=["A"], excluded_factor_reasons={"B": "Prespecified separate analysis"})
    spec = result["summary"]["design_specification"]
    excluded = next(row for row in spec["factors"] if row["factor"] == "B")
    assert excluded["reason"] == "Prespecified separate analysis"
    source["design_structure"] = {"blocks": ["subject"]}
    result = run(source)
    assert result["validity"] == "unavailable"
    assert "not implemented" in " ".join(result["summary"]["warnings"])
    assert result["summary"]["design_specification"]["structure"] == {"blocks": ["subject"]}


@pytest.mark.stats_validation
def test_beta_uses_joint_hc3_and_records_quantile_residual_permutation():
    source = source_for_design({"A": ["a", "b"], "B": ["x", "y"]}, beta=True)
    result = run(source)
    repeated = run(source)
    assert {key: value for key, value in result.items() if key != "runtime_s"} == {
        key: value for key, value in repeated.items() if key != "runtime_s"
    }
    assert result["family"] == "beta"
    summary = result["summary"]
    assert summary["covariance"]["computed"] == "HC3"
    assert "mean/precision" in summary["covariance"]["implementation"]
    test = next(row for row in summary["tests"] if ":" in row["effect"])
    assert test["permutation_p_adjusted"] is not None
    assert "quantile-residual permutation" in test["resampling_label"]
    assert test["resampling_procedure"] == "quantile_residual_permutation"
    html = render_html(dict(id="beta", created_at="fixed", output=result))
    assert "Freedman–Lane analogue" in html
    assert "primary" in html.lower()
    assert "precision" in html


def test_beta_does_not_need_the_hc0_fallback():
    result = run(source_for_design({"A": ["a", "b"]}, beta=True), hc3_fallback="withhold")
    assert result["family"] == "beta"
    assert result["summary"]["covariance"]["computed"] == "HC3"
    assert all(row["p_adjusted"] is not None for row in result["summary"]["tests"])


def test_configured_threshold_and_reproducible_output():
    source = source_for_design({"A": ["a", "b"]})
    original = deepcopy(source)
    first = run(source, alpha=0.99)
    second = run(source, alpha=0.99)
    first.pop("runtime_s")
    second.pop("runtime_s")
    assert first == second
    assert source == original
    summary = first["summary"]
    assert summary["alpha"] == 0.99
    assert summary["tests"][0]["p_adjusted"] < 0.99
    assert summary["tests"][0]["result"] == "Evidence of an effect"
    assert "adjusted p &lt; 0.99" in render_html(dict(id="x", created_at="fixed", output=first))
    assert any("found evidence" in finding for finding in summary["findings"])


def test_optimizer_retry_records_failed_attempt_and_independent_score_gate(monkeypatch):
    monkeypatch.setattr(
        "asaree.stats.fit_checks.native_diagnostics", lambda result, config: dict(critical_failures=[], unavailable=[])
    )

    class FakeModel:
        def score(self, params):
            return np.array([1e-9])

        def fit(self, *, method, **kwargs):
            if method == "lbfgs":
                raise ValueError("retry failed")
            return fitted(True)

    model = FakeModel()

    def fitted(converged):
        return SimpleNamespace(
            model=model,
            params=np.array([1.0]),
            llf=-4.0,
            nobs=10,
            cov_params=lambda: np.eye(1),
            mle_retvals={"converged": converged, "warnflag": 0},
            predict=lambda: np.full(10, 0.5),
        )

    result = finish_fit(fitted(False), {"maximum_scaled_score": 1e-6}, "beta")
    record = result._stats_fit_execution
    assert [row["method"] for row in record["attempts"]] == ["bfgs (gtol=1e-9)", "lbfgs", "newton"]
    assert record["attempts"][1]["error"] == "retry failed"
    assert record["checks"]["independently_checked_stationary_point"]
    assert not native_fit_checks(result, {"maximum_scaled_score": 1e-12})["accepted"]
    result.llf = float("nan")
    assert not native_fit_checks(result, {})["accepted"]


def test_validation_counts_failures_and_does_not_rule_out_ten_percent_with_small_samples():
    summary = rejection_summary(["not_rejected"] * 6 + ["withheld", "failed", "incomplete"])
    assert summary["rejected"] == 0
    assert summary["reportable"] == 6
    assert summary["simulations"] == 9
    assert summary["uncertainty_high"] > 0.1
    assert summary["withheld"] == summary["failed"] == summary["incomplete"] == 1
    assert summary["all_simulations_bounds"] == [0, 1 / 3]
    assert rejection_summary(["withheld"])["uncertainty_high"] is None


def test_invalid_dispersion_boundary_is_not_retried_with_unconstrained_newton():
    def unexpected_retry(**kwargs):
        raise AssertionError("A known invalid parameter boundary must not be retried")

    model = SimpleNamespace(score=lambda params: np.zeros(2), fit=unexpected_retry)
    result = SimpleNamespace(
        model=model,
        params=np.array([1.0, 1e-9]),
        llf=-2.0,
        nobs=10,
        cov_params=lambda: np.eye(2),
        mle_retvals={"converged": False},
    )
    result = finish_fit(result, {}, "negbin")
    assert len(result._stats_fit_execution["attempts"]) == 1
    assert not result._stats_fit_execution["checks"]["dispersion_boundary_gate"]
    assert "withheld" in result._stats_fit_execution["reportability_reason"]


def test_saved_requests_regenerate_reviewable_reports_without_database_writes(tmp_path, monkeypatch):
    source = source_for_design({"A": ["a", "b"]})
    config = StatsRequest(metric="value", n_boot=20, n_perm=99).model_dump()
    exported = tmp_path / "export.json"
    exported.write_text(
        json.dumps([dict(id="saved-report", created_at="fixed", request=dict(source=source, config=config))])
    )
    destination = tmp_path / "reports"
    monkeypatch.setattr(sys, "argv", ["scripts/stats_regenerate.py", str(exported), str(destination)])
    runpy.run_path("scripts/stats_regenerate.py", run_name="__main__")
    manifest = json.loads((destination / "manifest.json").read_text())
    entry = manifest["analyses"][0]
    assert entry["execution_status"] == "completed"
    assert entry["requested_config"] == entry["resolved_config"] == config
    analysis = json.loads((destination / entry["json"]).read_text())
    assert analysis["output"]["summary"]["provenance"]["fingerprint"] == source["fingerprint"]
    assert "Experimental design and analysis specification" in (destination / entry["html"]).read_text()
