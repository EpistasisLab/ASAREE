"""File-free comparison tests using synthetic replicated factorial observations."""

import json
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import numpy as np
import pytest
from fastapi import HTTPException

from asaree.services.stats_prototypes import StatsRequest, catalog, compute, prepare_frame


@pytest.mark.asyncio
async def test_history_exposes_report_status_without_loading_reports(monkeypatch):
    from asaree.api import stats_prototypes as api

    experiment_id = uuid4()
    analysis = SimpleNamespace(
        id=uuid4(), backend="stats_sm", status="completed", error=None,
        created_at=datetime.now(UTC),
        request={"config": {"family": "auto", "metric": "duration"},
                 "source": {"fingerprint": "snapshot", "design_revision": 1}},
    )
    # No output attribute: history must use the projected status, not load report JSON.
    result = MagicMock()
    result.all.return_value = [(analysis, "valid"), (analysis, "unavailable")]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(api, "_get_owned_experiment", AsyncMock())
    user = object()
    saved = await api.history(experiment_id, user, db, "stats_sm")
    assert [item["report_status"] for item in saved] == ["valid", "unavailable"]
    assert all("output" not in item for item in saved)
    api._get_owned_experiment.assert_awaited_once_with(db, experiment_id, user)


async def test_history_filters_by_publication_before_limit(monkeypatch):
    from asaree.api import stats_prototypes as api

    monkeypatch.setattr(api, "_get_owned_experiment", AsyncMock())
    result = MagicMock()
    result.all.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    version = uuid4()
    assert await api.history(uuid4(), object(), db, "stats_sm", version) == []
    statement = db.execute.call_args.args[0]
    assert str(version) in statement.compile().params.values()
    assert "protocol_revision_id" in statement.compile().params.values()


async def test_version_source_uses_frozen_attempts_and_publication_scope(monkeypatch):
    from asaree.services import stats_prototypes as service
    from asaree.services.result_reads import ResultScope

    experiment, protocol, version, design, replicate, run_id = [uuid4() for _ in range(6)]
    scope = ResultScope(
        experiment, protocol, version, design,
        {"factors": [{"name": "treatment", "levels": ["A", "B"]}], "replicates": 3},
        {}, True, None,
        {"metrics": [{"name": "Original score", "measurement_type": "continuous"}]},
        protocol_revision=2, design_revision=1,
    )
    resolver = AsyncMock(return_value=scope)
    monkeypatch.setattr(service, "resolve_result_scope", resolver)
    current = AsyncMock(side_effect=AssertionError("Must not read the current design"))
    monkeypatch.setattr(service, "get_current_revision", current)
    run = SimpleNamespace(
        id=run_id, status="completed", protocol_revision_id=version, replicate_result_id=replicate,
        attempt_result={"metric_values": {"Original score": 2.5}}, usage_summary={},
    )
    result = MagicMock()
    result.all.return_value = [(replicate, 1, {"treatment": "A"}, run)]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    snapshot = await service.read_source(db, experiment, version)
    resolver.assert_awaited_once_with(db, experiment_id=experiment, protocol_revision_id=version)
    assert snapshot["protocol_revision_id"] == str(version)
    assert snapshot["protocol_revision"] == 2
    assert snapshot["rows"][0]["metrics"] == {"Original score": 2.5}
    assert snapshot["metric_metadata"]["Original score"]["measurement_type"] == "continuous"
    assert catalog(snapshot)["n_measured_replicates"] == 1
    statement = db.execute.call_args.args[0]
    # The shared slot query constrains BOTH the publication and design, plus
    # its frozen factor levels/replicate count, before selecting each latest attempt.
    parameters = statement.compile().params
    assert version in parameters.values()
    assert design in parameters.values()
    assert "completed" in parameters.values()
    assert "protocol_runs.created_at DESC" in str(statement)


@pytest.mark.parametrize("reason", ["legacy", "row"])
async def test_version_source_refuses_unsupported_scopes(monkeypatch, reason):
    from asaree.services import stats_prototypes as service

    monkeypatch.setattr(service, "resolve_result_scope", AsyncMock(return_value=SimpleNamespace(
        versioned=reason != "legacy", unit="row" if reason == "row" else "replicate",
    )))
    db = AsyncMock()
    with pytest.raises(ValueError):
        await service.read_source(db, uuid4(), uuid4())
    db.execute.assert_not_awaited()


@pytest.mark.parametrize("version", [None, "empty"])
async def test_automatic_requires_a_version_with_measured_completed_replicates(monkeypatch, version):
    from asaree.api import stats_prototypes as api

    monkeypatch.setattr(api, "_get_owned_experiment", AsyncMock())
    monkeypatch.setattr(api, "read_source", AsyncMock(return_value={
        "rows": [{"factors": {}, "metrics": {"declared": None}}],
        "design_revision": 1, "design_revision_id": str(uuid4()),
        "fingerprint": "empty", "excluded_row_replicates": 0,
    }))
    db = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await api.automatic(uuid4(), object(), db, uuid4() if version else None)
    assert error.value.status_code == 422
    db.flush.assert_not_awaited()


@pytest.fixture
def source():
    rng = np.random.default_rng(74)
    rows = []
    for i in range(160):
        a, b = i % 2, (i // 2) % 2
        mu = 1 + 0.3 * a + 0.2 * b + 0.15 * a * b
        rows.append(
            dict(
                id=str(i),
                replicate=i // 4 + 1,
                factors={"model": str(a), "prompt": str(b)},
                metrics={
                    "continuous": mu + rng.normal(0, 0.3),
                    "positive": rng.gamma(4, mu / 4),
                    "count": int(rng.negative_binomial(3, 3 / (3 + mu * 2))) if rng.random() > 0.15 else 0,
                    "binary": int(rng.random() < 0.3 + 0.2 * a),
                    "beta": float(rng.beta(mu * 3, 5)),
                    "successes": int(rng.binomial(10, 0.3 + 0.2 * a)),
                    "trials": 10,
                    "ordinal": rng.choice(["low", "medium", "high"]),
                },
            )
        )
    return dict(
        rows=rows,
        fingerprint="same-input",
        design_revision=1,
        design_revision_id=str(uuid4()),
        excluded_row_replicates=0,
    )


def test_saved_report_findings_refresh_without_mutating_stored_results(source):
    from asaree.api.stats_prototypes import summary

    output = dict(summary=dict(findings=["Old dense wording"], tests=[
        dict(effect="Effort:Budget", p_adjusted=.4, permutation_p_adjusted=None),
    ]))
    analysis = SimpleNamespace(
        id=uuid4(), backend="stats_sm", status="completed", error=None,
        created_at=datetime.now(UTC), request=dict(source=source, config={}), output=output,
    )
    report = summary(analysis, detail=True)
    assert "the effect of Effort changes across Budget settings" in report["output"]["summary"]["findings"][0]
    assert output["summary"]["findings"] == ["Old dense wording"]


@pytest.mark.parametrize("has_summary", [True, False])
def test_saved_ordinal_report_recovers_chart_from_its_input_snapshot(source, has_summary):
    from asaree.api.stats_prototypes import summary

    source["metric_metadata"] = {"ordinal": {"ordinal_levels": ["low", "medium", "high"]}}
    values = [row["metrics"]["ordinal"] for row in source["rows"]]
    output = dict(family="ordinal_logit", tables={}, n_observations=len(values), summary=dict(
        status="valid", findings=[], tests=[], distribution=dict(n=len(values), minimum=0, maximum=2),
    ))
    if not has_summary:
        del output["summary"]["distribution"]
    analysis = SimpleNamespace(
        id=uuid4(), backend="stats_sm", status="completed", error=None, created_at=datetime.now(UTC),
        request=dict(source=source, config=StatsRequest(metric="ordinal").model_dump()), output=output,
    )
    report = summary(analysis, detail=True)
    plot = report["output"]["summary"]["distribution"]["plot"]
    assert plot is not None
    assert plot["bins"] == [dict(label=label, count=values.count(label)) for label in ["low", "medium", "high"]]
    assert "plot" not in output["summary"].get("distribution", {})


def test_saved_binomial_chart_uses_success_counts_not_transformed_residuals(source):
    from asaree.services.stats_distribution import report_distribution

    source["metric_metadata"] = {"successes": {"denominator": "trials"}}
    values = [row["metrics"]["successes"] for row in source["rows"]]
    output = dict(family="binomial", n_observations=len(values), summary=dict(
        distribution=dict(n=len(values), minimum=min(values), maximum=max(values)),
    ), tables=dict(residuals=[dict(observed=value / 10) for value in values]))
    restored = report_distribution(output, source, StatsRequest(metric="successes").model_dump())
    assert restored["maximum"] == max(values)
    assert sum(bin["count"] for bin in restored["plot"]["bins"]) == len(values)
    assert restored["plot"]["bins"] == [
        dict(label=str(value), count=values.count(value)) for value in sorted(set(values))
    ]


def configuration(**kwargs):
    kwargs = {"family": "gaussian", "measurement_type": "continuous", **kwargs}
    return StatsRequest(metric="continuous", factors=["model", "prompt"], n_boot=20, n_perm=99, **kwargs).model_dump(
        mode="json"
    )


@pytest.mark.parametrize(
    ("family", "metric"),
    [
        ("gaussian", "continuous"),
        ("lognormal", "positive"),
        ("gamma", "positive"),
        ("poisson", "count"),
        ("negbin", "count"),
        ("zip", "count"),
        ("zinb", "count"),
        ("beta", "beta"),
        ("binomial", "binary"),
        ("ordinal_logit", "ordinal"),
        ("tweedie", "positive"),
    ],
)
def test_mapped_models_produce_database_serializable_outputs(source, family, metric):
    config = configuration(family=family)
    config["metric"] = metric
    config["ordinal_levels"] = ["low", "medium", "high"]
    result = compute("stats_sm", source, config)
    json.dumps(result, allow_nan=False)
    assert len(result["tables"]["cell_means"]) == 4
    assert len(result["tables"]["marginal_means"]) == 4
    assert len(result["tables"]["contrasts"]) == 3
    assert result["source_fingerprint"] == "same-input"
    assert all(np.isfinite(row["estimate"]) for row in result["tables"]["cell_means"])
    assert any(item["title"] == "ART / ART-C" for item in result["unavailable"])


@pytest.mark.parametrize(
    ("family", "metric"),
    [
        ("gaussian", "continuous"),
        ("binomial", "binary"),
        ("poisson", "count"),
        ("gamma", "positive"),
        ("lognormal", "positive"),
        ("beta", "beta"),
    ],
)
def test_original_and_statsmodels_estimates_agree(source, family, metric):
    config = configuration(family=family)
    config["metric"] = metric
    original = compute("stats", source, config)
    established = compute("stats_sm", source, config)
    for table in ("cell_means", "marginal_means", "contrasts"):
        np.testing.assert_allclose(
            [row["estimate"] for row in original["tables"][table]],
            [row["estimate"] for row in established["tables"][table]],
            rtol=1e-5,
            atol=1e-7,
        )
    assert "art_terms" in original["tables"]
    assert "art_terms" not in established["tables"]
    assert "comparison_terms" in original["tables"]


def test_subprocess_runner_uses_only_json_streams(source):
    completed = subprocess.run(
        [sys.executable, "-m", "asaree.services.stats_runner"],
        input=json.dumps(dict(backend="stats_sm", source=source, config=configuration())),
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert json.loads(completed.stdout)["source_fingerprint"] == "same-input"


def test_binomial_denominator_is_used(source):
    config = configuration(family="binomial", denominator="trials")
    config["metric"] = "successes"
    for backend in ("stats", "stats_sm"):
        output = compute(backend, source, config)
        assert all(0 < row["estimate"] < 1 for row in output["tables"]["cell_means"])


def test_unsupported_mapping_does_not_call_original(source):
    config = configuration(family="zoib")
    with pytest.raises(ValueError, match="No established mapping"):
        compute("stats_sm", source, config)


def test_reserved_factor_names_cannot_overwrite_outcomes(source):
    for row in source["rows"]:
        row["factors"]["outcome"] = row["factors"].pop("model")
    config = configuration()
    config["factors"] = ["outcome", "prompt"]
    output = compute("stats_sm", source, config)
    assert len(output["tables"]["cell_means"]) == 4


@pytest.mark.parametrize(
    "change, message",
    [
        ({"factors": ["missing"]}, "varying factors"),
        ({"factors": ["model", "model"]}, "distinct factors"),
        ({"family": "poisson"}, "integer outcomes"),
        ({"denominator": "trials"}, "denominator is only supported"),
        ({"metric": "missing"}, "five scored"),
    ],
)
def test_invalid_analysis_inputs_are_rejected(source, change, message):
    config = configuration()
    config.update(change)
    with pytest.raises(ValueError, match=message):
        prepare_frame(source, config)


def test_catalog_and_missing_outcome_exclusions(source):
    source["rows"][0]["metrics"]["continuous"] = None
    assert catalog(source)["metrics"]
    output = compute("stats_sm", source, configuration())
    assert output["n_observations"] == 159
    assert output["n_excluded"] == 1


async def test_ownership_is_checked_before_source_reads(monkeypatch):
    from asaree.api import stats_prototypes as api

    owned = AsyncMock(side_effect=HTTPException(404, "Experiment not found"))
    read = AsyncMock()
    monkeypatch.setattr(api, "_get_owned_experiment", owned)
    monkeypatch.setattr(api, "read_source", read)
    with pytest.raises(HTTPException):
        await api.source(uuid4(), SimpleNamespace(), AsyncMock())
    read.assert_not_awaited()


async def test_snapshot_cannot_be_reused_across_experiments(monkeypatch):
    from asaree.api import stats_prototypes as api

    monkeypatch.setattr(api, "_get_owned_experiment", AsyncMock())
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(experiment_id=uuid4())
    request = StatsRequest(metric="score", factors=["model"], source_analysis_id=uuid4())
    with pytest.raises(HTTPException) as error:
        await api.create(uuid4(), "stats_sm", request, SimpleNamespace(), db)
    assert error.value.status_code == 404
    db.commit.assert_not_awaited()


async def test_snapshot_is_committed_before_worker_enqueue_and_can_be_read(source, monkeypatch):
    from asaree.api import stats_prototypes as api

    monkeypatch.setattr(api, "_get_owned_experiment", AsyncMock())
    monkeypatch.setattr(api, "read_source", AsyncMock(return_value=source))
    db = AsyncMock()
    saved = []

    def add(row):
        row.id, row.created_at = uuid4(), datetime.now(UTC)
        saved.append(row)

    db.add = MagicMock(side_effect=add)

    async def enqueue(*args, **kwargs):
        db.commit.assert_awaited_once()
        assert saved[0].request["source"] == source
        assert args[0] == "execute_stats_task"

    pool = SimpleNamespace(enqueue_job=AsyncMock(side_effect=enqueue))
    monkeypatch.setattr(api, "_get_pool", AsyncMock(return_value=pool))
    experiment = uuid4()
    result = await api.create(experiment, "stats_sm", StatsRequest(**configuration()), SimpleNamespace(), db)
    assert result["status"] == "pending"
    assert saved[0].backend == "stats_sm"
    db.get.return_value = saved[0]
    detail = await api.detail(experiment, saved[0].id, SimpleNamespace(), db)
    assert detail["fingerprint"] == "same-input"


async def test_database_source_pins_design_revision_and_excludes_row_slots(monkeypatch):
    from asaree.services import stats_prototypes as service

    experiment, revision_id, whole_id, row_id = uuid4(), uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(
        service, "get_current_revision", AsyncMock(return_value=SimpleNamespace(id=revision_id, revision=3))
    )
    read = AsyncMock(
        return_value=[
            SimpleNamespace(id=whole_id, replicate_number=1, factor_values={"model": "a"}, metric_values={"score": 2}),
            SimpleNamespace(id=row_id, replicate_number=2, factor_values={"model": "b"}, metric_values={"score": 9}),
        ]
    )
    monkeypatch.setattr(service, "list_replicates", read)
    db = AsyncMock()
    db.scalar.return_value = 2
    db.scalars.return_value = SimpleNamespace(all=lambda: [row_id])
    source = await service.read_source(db, experiment)
    read.assert_awaited_once_with(db, experiment_id=experiment, revision_id=revision_id)
    assert source["design_revision"] == 3
    assert [row["id"] for row in source["rows"]] == [str(whole_id)]
    assert source["excluded_row_replicates"] == 1


async def test_worker_persists_failure_and_terminates_child(monkeypatch):
    from asaree.worker import stats_task

    row = SimpleNamespace(status="pending", backend="stats", request={}, error=None)
    db = AsyncMock()
    db.get.return_value = row

    @asynccontextmanager
    async def session():
        yield db

    process = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"bad model")))
    monkeypatch.setattr(stats_task, "get_session", session)
    monkeypatch.setattr(stats_task.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    await stats_task.execute_stats_task({}, str(uuid4()))
    assert row.status == "failed"
    assert "bad model" in row.error
