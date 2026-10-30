"""File-free comparison tests using synthetic replicated factorial observations."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import numpy as np
import pytest
from fastapi import HTTPException

from asaree.services.stats_source import StatsRequest, catalog, json_safe, prepare_frame
from asaree.stats.analysis import analyze


@pytest.mark.parametrize("metric", [None, "score"])
async def test_replacement_deletes_old_reports_only_in_the_selected_scope(metric):
    import sqlalchemy as sa

    from asaree.api.stats import replace_automatic_reports

    metadata = sa.MetaData()
    experiments = sa.Table("research_experiments", metadata, sa.Column("id", sa.Uuid, primary_key=True))
    reports = sa.Table(
        "stats_analyses", metadata,
        sa.Column("id", sa.Uuid, primary_key=True), sa.Column("experiment_id", sa.Uuid),
        sa.Column("status", sa.String), sa.Column("request", sa.JSON),
    )
    experiment, other_experiment, version, other_version = [uuid4() for _ in range(4)]

    def row(*, exp=experiment, ver=version, family="auto", name="score", status="completed"):
        return dict(id=uuid4(), experiment_id=exp, status=status,
                    request=dict(source=dict(protocol_revision_id=str(ver)), config=dict(family=family, metric=name)))

    obsolete = [row(), row(), row(name="cost")]
    preserved = [row(exp=other_experiment), row(ver=other_version), row(family="gaussian")]
    with sa.create_engine("sqlite://").begin() as connection:
        metadata.create_all(connection)
        connection.execute(experiments.insert(), [dict(id=experiment), dict(id=other_experiment)])
        connection.execute(reports.insert(), obsolete + preserved)
        db = SimpleNamespace(execute=AsyncMock(side_effect=connection.execute),
                             scalar=AsyncMock(side_effect=connection.scalar))
        await replace_automatic_reports(db, experiment, version, metric=metric)
        remaining = set(connection.scalars(sa.select(reports.c.id)))
        expected = {report["id"] for report in preserved}
        if metric:
            expected.add(obsolete[-1]["id"])
        assert remaining == expected
        assert "FOR UPDATE" in str(db.execute.call_args_list[0].args[0])


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_replacement_rejects_an_active_batch_without_deleting_reports(status):
    from asaree.api.stats import replace_automatic_reports

    db = AsyncMock()
    db.scalar.return_value = uuid4()
    with pytest.raises(HTTPException) as error:
        await replace_automatic_reports(db, uuid4(), uuid4())
    assert error.value.status_code == 409
    assert all(not call.args[0].is_delete for call in db.execute.call_args_list)
    assert status in db.scalar.call_args.args[0].compile().params["status_1"]


@pytest.mark.asyncio
async def test_history_exposes_report_status_without_loading_reports(monkeypatch):
    from asaree.api import stats as api

    experiment_id = uuid4()
    analysis = SimpleNamespace(
        id=uuid4(), status="completed", error=None,
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
    saved = await api.history(experiment_id, user, db)
    assert [item["report_status"] for item in saved] == ["valid", "unavailable"]
    assert all("output" not in item for item in saved)
    api._get_owned_experiment.assert_awaited_once_with(db, experiment_id, user)


async def test_history_filters_by_publication_before_limit(monkeypatch):
    from asaree.api import stats as api

    monkeypatch.setattr(api, "_get_owned_experiment", AsyncMock())
    result = MagicMock()
    result.all.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    version = uuid4()
    assert await api.history(uuid4(), object(), db, version) == []
    statement = db.execute.call_args.args[0]
    assert str(version) in statement.compile().params.values()
    assert "protocol_revision_id" in statement.compile().params.values()


async def test_version_source_uses_frozen_attempts_and_publication_scope(monkeypatch):
    from asaree.services import stats_source as service
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
    from asaree.services import stats_source as service

    monkeypatch.setattr(service, "resolve_result_scope", AsyncMock(return_value=SimpleNamespace(
        versioned=reason != "legacy", unit="row" if reason == "row" else "replicate",
    )))
    db = AsyncMock()
    with pytest.raises(ValueError):
        await service.read_source(db, uuid4(), uuid4())
    db.execute.assert_not_awaited()


@pytest.mark.parametrize("version", [None, "empty"])
async def test_automatic_requires_a_version_with_measured_completed_replicates(monkeypatch, version):
    from asaree.api import stats as api

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
    from asaree.api.stats import summary

    output = dict(summary=dict(findings=["Old dense wording"], tests=[
        dict(effect="Effort:Budget", p_adjusted=.4, permutation_p_adjusted=None),
    ]))
    analysis = SimpleNamespace(
        id=uuid4(), status="completed", error=None,
        created_at=datetime.now(UTC), request=dict(source=source, config={}), output=output,
    )
    report = summary(analysis, detail=True)
    assert "the effect of Effort changes across Budget settings" in report["output"]["summary"]["findings"][0]
    assert output["summary"]["findings"] == ["Old dense wording"]


@pytest.mark.parametrize("has_summary", [True, False])
def test_saved_ordinal_report_recovers_chart_from_its_input_snapshot(source, has_summary):
    from asaree.api.stats import summary

    source["metric_metadata"] = {"ordinal": {"ordinal_levels": ["low", "medium", "high"]}}
    values = [row["metrics"]["ordinal"] for row in source["rows"]]
    output = dict(family="ordinal_logit", tables={}, n_observations=len(values), summary=dict(
        status="valid", findings=[], tests=[], distribution=dict(n=len(values), minimum=0, maximum=2),
    ))
    if not has_summary:
        del output["summary"]["distribution"]
    analysis = SimpleNamespace(
        id=uuid4(), status="completed", error=None, created_at=datetime.now(UTC),
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


def native(source, config):
    """Fit one explicitly chosen statsmodels family, as the automatic pipeline does per candidate."""
    frame = prepare_frame(source, config)
    output = json_safe(analyze(frame, config))
    return dict(output, n_observations=len(frame), n_excluded=len(source["rows"]) - len(frame))


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
    ],
)
def test_mapped_models_produce_database_serializable_outputs(source, family, metric):
    config = configuration(family=family)
    config["metric"] = metric
    config["ordinal_levels"] = ["low", "medium", "high"]
    result = native(source, config)
    json.dumps(result, allow_nan=False)
    assert len(result["tables"]["cell_means"]) == 4
    assert len(result["tables"]["marginal_means"]) == 4
    assert len(result["tables"]["contrasts"]) == 3
    assert all(np.isfinite(row["estimate"]) for row in result["tables"]["cell_means"])


def test_subprocess_runner_uses_only_json_streams(source):
    completed = subprocess.run(
        [sys.executable, "-m", "asaree.services.stats_runner"],
        input=json.dumps(
            dict(source=source, config=StatsRequest(metric="continuous", n_boot=20, n_perm=99).model_dump())
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert json.loads(completed.stdout)["source_fingerprint"] == "same-input"


def test_binomial_denominator_is_used(source):
    config = configuration(family="binomial", denominator="trials")
    config["metric"] = "successes"
    output = native(source, config)
    assert all(0 < row["estimate"] < 1 for row in output["tables"]["cell_means"])


def test_unsupported_mapping_does_not_call_original(source):
    config = configuration(family="zoib")
    with pytest.raises(ValueError, match="No established mapping"):
        native(source, config)


def test_reserved_factor_names_cannot_overwrite_outcomes(source):
    for row in source["rows"]:
        row["factors"]["outcome"] = row["factors"].pop("model")
    config = configuration()
    config["factors"] = ["outcome", "prompt"]
    output = native(source, config)
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
    output = native(source, configuration())
    assert output["n_observations"] == 159
    assert output["n_excluded"] == 1


async def test_ownership_is_checked_before_source_reads(monkeypatch):
    from asaree.api import stats as api

    owned = AsyncMock(side_effect=HTTPException(404, "Experiment not found"))
    read = AsyncMock()
    monkeypatch.setattr(api, "_get_owned_experiment", owned)
    monkeypatch.setattr(api, "read_source", read)
    with pytest.raises(HTTPException):
        await api.source(uuid4(), SimpleNamespace(), AsyncMock())
    read.assert_not_awaited()
