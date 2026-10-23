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
