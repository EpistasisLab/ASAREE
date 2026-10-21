"""Read contracts, SQL scoping and optional isolated PostgreSQL integration checks.

Set ASAREE_RESULTS_TEST_DATABASE_URL to a disposable PostgreSQL database. All
migrations and fixtures live in a unique schema inside a rolled-back transaction.
This suite never reads application credential files or touches application tables.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.result_job import ResultJobMember
from asaree.models.result_read_model import ResultRefresh
from asaree.models.user import User
from asaree.services import result_jobs
from asaree.services.factorial_cells import summarize_cells
from asaree.services.result_reads import (
    ResultScope,
    cell_attempt_usage_query,
    cell_predicates,
    completed_replicates_query,
    decode_cursor,
    encode_cursor,
    execution_search_selection,
    page_size,
    parent_predicates,
    read_attempt_detail,
    read_attempts,
    read_cell,
    read_cells,
    read_conversation,
    read_evidence,
    read_execution_neighbors,
    read_executions,
    read_summary,
    read_usage_distribution,
    resolve_result_scope,
    runtime_metrics,
    slot_query,
    usage_distribution_query,
)
from asaree.services.result_rollups import rebuild_result_summary, rollup_query


def scope(**changes):
    values = dict(
        experiment_id=uuid.UUID(int=1),
        protocol_id=uuid.UUID(int=2),
        protocol_revision_id=uuid.UUID(int=3),
        design_revision_id=uuid.UUID(int=4),
        design_spec=None,
        graph={},
        versioned=True,
        row_plan=None,
    )
    return ResultScope(**(values | changes))


def test_slot_reads_do_not_select_evidence_or_agent_runs():
    query = select(slot_query(scope()).subquery()).limit(21)
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "LIMIT 21" in sql
    assert "protocol_runs.node_runs" not in sql
    assert "protocol_runs.conversation" not in sql
    assert "protocol_runs.attempt_result" not in sql
    assert "agent_runs" not in sql
    assert "factorial_cells.experiment_id" in sql
    assert "protocol_runs.protocol_revision_id" in sql
    assert "protocol_runs.design_revision_id" in sql
    assert "LATERAL" in sql


def test_rollup_query_uses_compact_data_and_database_aggregates():
    sql = str(rollup_query(scope()).compile(dialect=postgresql.dialect()))
    assert "GROUP BY" in sql
    assert "protocol_runs.result_summary" in sql
    assert "protocol_runs.attempt_result" not in sql
    assert "protocol_runs.conversation" not in sql


@pytest.mark.parametrize("metric", ["total_tokens", "cost_usd", "duration_seconds"])
def test_usage_distribution_is_aggregated_in_sql_and_scoped(metric):
    sql = str(usage_distribution_query(scope(), uuid.UUID(int=5), metric).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))
    assert "percentile_cont" in sql
    assert "GROUP BY" in sql
    assert "usage_stats.maximum > usage_stats.minimum" in sql
    assert "usage_slots.status = 'completed'" in sql
    assert "factorial_cells.id = '00000000-0000-0000-0000-000000000005'" in sql
    assert "factorial_cells.experiment_id =" in sql
    assert "factorial_cells.design_revision_id =" in sql
    assert "protocol_runs.protocol_revision_id =" in sql
    assert "protocol_runs.is_test_run IS false" in sql
    assert "protocol_runs.attempt_result" not in sql
    assert "protocol_runs.node_runs" not in sql


def test_usage_distribution_rejects_custom_metrics():
    with pytest.raises(ValueError, match="unsupported_usage_metric"):
        usage_distribution_query(scope(), uuid.UUID(int=5), "accuracy")


@pytest.mark.parametrize("count,value", [(0, None), (7, 0), (7, 0.000001)])
async def test_usage_distribution_empty_and_constant_samples(count, value):
    db = AsyncMock()
    db.scalar.return_value = uuid.UUID(int=5)
    result = MagicMock()
    result.mappings.return_value.all.return_value = [{
        "completed_count": 9, "count": count, "minimum": value, "maximum": value,
        "p25": value, "median": value, "p75": value, "bucket": 0 if count else None, "bin_count": count,
    }]
    db.execute.return_value = result
    distribution = await read_usage_distribution(db, scope(), cell_id=uuid.UUID(int=5), metric="cost_usd")
    assert distribution["count"] == count
    assert distribution["completed_count"] == 9
    assert distribution["median"] == value
    assert distribution["bins"] == ([{
        "lower": value, "upper": value, "upper_inclusive": True, "count": count,
    }] if count else [])


async def test_usage_distribution_rejects_cell_outside_scope():
    from asaree.services.experiment_run_results import RunResultsProjectionError

    db = AsyncMock()
    db.scalar.return_value = None
    with pytest.raises(RunResultsProjectionError, match="cell_not_found"):
        await read_usage_distribution(db, scope(), cell_id=uuid.uuid4(), metric="total_tokens")
    db.execute.assert_not_called()


@pytest.mark.parametrize("options,error", [
    ({"usage_metric": "accuracy", "usage_min": 0, "usage_max": 1}, "unsupported_usage_metric"),
    ({"usage_min": 0}, "usage_metric_required"),
    ({"usage_metric": "cost_usd", "usage_min": 2, "usage_max": 1}, "invalid_usage_range"),
    ({"usage_metric": "cost_usd", "usage_min": float("nan"), "usage_max": 1}, "invalid_usage_range"),
    ({"usage_metric": "cost_usd", "usage_min": 0, "usage_max": float("inf")}, "invalid_usage_range"),
    ({"sort_metric": "accuracy"}, "unsupported_usage_metric"),
    ({"sort_direction": "sideways"}, "invalid_sort_direction"),
])
async def test_execution_usage_filters_validate_before_querying(options, error):
    db = AsyncMock()
    with pytest.raises(ValueError, match=error):
        await read_executions(db, scope(), **options)
    db.execute.assert_not_called()


@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_execution_usage_sort_uses_bound_keyset_cursors(direction):
    db = AsyncMock()
    db.scalar.return_value = 2
    result = MagicMock()
    result.mappings.return_value.all.return_value = [{
        "slot_id": uuid.UUID(int=index), "cell_id": uuid.UUID(int=5), "replicate_id": uuid.UUID(int=index),
        "replicate_number": index, "row_index": None,
        "label_order": "condition", "sort_value": 100 if direction == "asc" else -100,
    } for index in (1, 2)]
    db.execute.return_value = result
    selected = scope()
    first = await read_executions(db, selected, limit=1, sort_metric="total_tokens", sort_direction=direction)
    assert first["total"] == 2
    assert first["next_cursor"]
    await read_executions(db, selected, limit=1, sort_metric="total_tokens", sort_direction=direction,
                          cursor=first["next_cursor"])
    query = db.execute.call_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "ORDER BY" in sql and "total_tokens" in sql
    assert "IS NOT NULL" in sql
    assert "slots.replicate_number" in sql and "slots.slot_id" in sql
    assert "-100" in sql if direction == "desc" else "100" in sql
    with pytest.raises(ValueError, match="cursor_scope_mismatch"):
        await read_executions(db, selected, limit=1, sort_metric="cost_usd", sort_direction=direction,
                              cursor=first["next_cursor"])


@pytest.mark.parametrize("inclusive", [False, True])
async def test_execution_usage_range_compiles_in_database_with_completed_scope(inclusive):
    db = AsyncMock()
    db.scalar.return_value = 0
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    db.execute.return_value = result
    await read_executions(db, scope(), usage_metric="cost_usd", usage_min=0, usage_max=0.001,
                          usage_upper_inclusive=inclusive)
    sql = str(db.scalar.call_args.args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))
    assert "slots.status = 'completed'" in sql
    assert "cost_usd" in sql
    assert "<= 0.001" in sql if inclusive else "< 0.001" in sql
    assert "protocol_runs.protocol_revision_id" in sql
    assert "protocol_runs.node_runs" not in sql


@pytest.mark.parametrize("search,row_mode,expected_number,expected_row", [
    ("12", False, 12, None), ("12", True, 12, 11),
    ("Replicate 12", True, 12, None), ("row 12", True, None, 11),
    ("source row #12", True, None, 11), ("row 12", False, None, None),
    ("0", True, None, None), ("999999999999999999999", True, None, None),
])
def test_execution_search_matches_numbers_and_one_based_source_rows(search, row_mode, expected_number, expected_row):
    parent_match, row_index = execution_search_selection(search, row_mode)
    sql = str(parent_match.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert row_index == expected_row
    assert sql == (f"factorial_replicate_results.replicate_number = {expected_number}"
                   if expected_number is not None else "false")


def test_execution_search_treats_labels_as_literal_text():
    match, row_index = execution_search_selection("a%_b", False)
    compiled = match.compile(dialect=postgresql.dialect())
    assert row_index is None
    assert "%a\\%\\_b%" in compiled.params.values()


async def test_source_row_search_returns_bounded_unplanned_rows_without_expanding_source():
    db = AsyncMock()
    selected = scope(row_plan={"driver_dataset_id": str(uuid.UUID(int=6))})
    db.get.return_value = MagicMock(summary={"source_row_count": 1000000})
    db.scalar.return_value = 2
    parent_result = MagicMock()
    parent_result.mappings.return_value.all.return_value = [{
        "replicate_id": uuid.UUID(int=number), "replicate_label": f"rep{number}", "replicate_number": number,
        "cell_id": uuid.UUID(int=5), "cell_label": "condition", "factor_values": {}, "label_order": "condition",
        "matches_execution_search": False,
    } for number in (2, 10)]
    existing = MagicMock()
    existing.mappings.return_value.all.return_value = []
    db.execute.side_effect = [parent_result, existing]
    page = await read_executions(db, selected, execution_search="row 12")
    assert page["total"] == 2
    assert [(row["replicate_number"], row["row_index"]) for row in page["items"]] == [(2, 11), (10, 11)]
    assert all(row["status"] == "not_started" for row in page["items"])
    sql = str(db.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    assert "LIMIT" in sql and "generate_series" not in sql


def test_cell_attempt_usage_is_bounded_and_scoped():
    sql = str(cell_attempt_usage_query(scope(), [uuid.UUID(int=5)]).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))
    assert "sum(" in sql
    assert "GROUP BY factorial_cells.id" in sql
    assert "factorial_cells.id IN ('00000000-0000-0000-0000-000000000005')" in sql
    assert "factorial_cells.experiment_id =" in sql
    assert "factorial_cells.design_revision_id =" in sql
    assert "protocol_runs.protocol_revision_id =" in sql
    assert "protocol_runs.design_revision_id =" in sql
    assert "protocol_runs.is_test_run IS false" in sql
    assert "protocol_runs.target_node_id IS NULL" in sql
    assert "protocol_runs.row_result_id IS NULL" in sql
    assert "protocol_runs.attempt_result" not in sql
    assert "protocol_runs.node_runs" not in sql


def test_row_replicate_progress_requires_all_planned_rows():
    query = completed_replicates_query(scope(row_plan={"driver_dataset_id": str(uuid.UUID(int=6))}),
                                       [uuid.UUID(int=5)], 10)
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "HAVING count(*) = 10" in sql
    assert "FILTER (WHERE replicate_progress_slots.status = 'completed') = 10" in sql
    assert "replicate_progress_slots.cell_id IN ('00000000-0000-0000-0000-000000000005')" in sql
    assert "factorial_cells.experiment_id" in sql
    assert "protocol_runs.protocol_revision_id" in sql


async def test_row_cell_progress_counts_complete_replicates(results_db):
    from dataclasses import replace

    db, selected, cell, user = results_db
    dataset = RegisteredDataset(name="Progress rows", owner_id=user.id)
    parents = [FactorialReplicateResult(cell_id=cell.id, replicate_number=number, replicate_label=f"rep{number}")
               for number in (1, 2, 3)]
    db.add_all([dataset, *parents])
    await db.flush()
    selected = replace(selected, row_plan={"driver_dataset_id": str(dataset.id)})
    # Replicate 1 is complete, replicate 2 has an unfinished row, and replicate 3 is missing a planned row.
    for number, indices in [(1, (0, 1)), (2, (0, 1)), (3, (0,))]:
        parent = parents[number - 1]
        for index in indices:
            row = FactorialRowResult(replicate_result_id=parent.id, protocol_revision_id=selected.protocol_revision_id,
                                     dataset_id=dataset.id, raw_sha256="a" * 64, row_index=index)
            db.add(row)
            await db.flush()
            db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id,
                               protocol_revision_id=selected.protocol_revision_id,
                               design_revision_id=selected.design_revision_id,
                               replicate_result_id=parent.id, row_result_id=row.id,
                               status="running" if (number, index) == (2, 1) else "completed"))
    await db.flush()
    # Result-change triggers already create this scope's refresh row.
    refresh = await db.get(ResultRefresh, selected.key)
    assert refresh is not None
    refresh.summary = {"source_row_count": 2}
    await db.flush()
    result = await read_cells(db, selected)
    assert result["items"][0]["completed_replicate_count"] == 1


async def test_cell_attempt_usage_counts_retries_and_excludes_other_scopes(results_db):
    db, selected, cell, user = results_db
    parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label="replicate")
    db.add(parent)
    await db.flush()
    base = dict(protocol_id=selected.protocol_id, owner_id=user.id,
                protocol_revision_id=selected.protocol_revision_id, design_revision_id=selected.design_revision_id,
                replicate_result_id=parent.id, replicate_label=parent.replicate_label, node_runs={})
    for tokens, overrides in [
        (100, {"status": "failed"}), (200, {"status": "completed"}),
        (1000, {"status": "completed", "is_test_run": True}),
        (1000, {"status": "completed", "target_node_id": "preview"}),
        (1000, {"status": "completed", "protocol_revision_id": None}),
        (1000, {"status": "completed", "design_revision_id": None}),
    ]:
        db.add(ProtocolRun(**(base | overrides), usage_summary={
            "total_tokens": tokens, "cost_usd": tokens / 10000, "duration_seconds": tokens / 10,
        }))
    await db.flush()
    await rebuild_result_summary(db, selected, 1)
    latest = await read_cells(db, selected)
    assert "all_attempt_usage" not in latest["items"][0]
    result = await read_cells(db, selected, include_all_attempt_usage=True)
    usage = result["items"][0]["all_attempt_usage"]
    assert usage["total_tokens"] == 300
    assert usage["cost_usd"] == pytest.approx(0.03)
    assert usage["duration_seconds"] == 30


def test_publication_cohort_compiles_without_materializing_factor_combinations():
    selected = scope(design_spec={"factors": [{"name": "model", "levels": ["a", "b"]}], "replicates": 3})
    sql = str(slot_query(selected).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "factorial_replicate_results.replicate_number <= 3" in sql
    assert "IN (" in sql and "JSONB" in sql
    assert "CAST(ARRAY['model'] AS TEXT[])" in sql
    assert not cell_predicates(scope(versioned=False, design_spec=selected.design_spec))
    assert not parent_predicates(scope(versioned=False, design_spec=selected.design_spec))


def test_scalar_metadata_uses_builtin_binding_identity_and_retains_node_runtime_metrics():
    selected = scope(
        design_spec={"metrics": [{"id": "node-tokens", "name": "Tokens for critic", "kind": "custom"}]},
        measurement_plan={
            "metrics": [{"id": "node-tokens", "name": "Tokens for critic", "primary": True, "direction": "minimize"}],
            "producers": [
                {
                    "id": "node",
                    "producer_id": "asaree.node_runtime",
                    "kind": "runtime",
                    "outputs": {"total_tokens": "node-tokens"},
                    "config": {"node_id": "critic"},
                }
            ],
            "inputs": [],
        },
    )
    definition = runtime_metrics(selected)["node-tokens"]
    assert definition["key"] == "node-tokens"
    assert definition["name"] == "Tokens for critic"
    assert definition["aggregation"] == "sum" and definition["valueType"] == "number"
    assert definition['direction'] == 'minimize'
    reported = scope(
        design_spec=selected.design_spec,
        measurement_plan={
            **selected.measurement_plan,
            "producers": [
                {
                    "id": "node",
                    "producer_id": "asaree.python_script",
                    "kind": "reported",
                    "outputs": {"total_tokens": "node-tokens"},
                    "config": {},
                }
            ],
        },
    )
    assert runtime_metrics(reported) == {}


def test_catalog_defaults_determine_comparison_direction_when_omitted():
    selected = scope(
        design_spec={
            "metrics": [{"id": "cost", "name": "Cost", "kind": "runtime", "catalogKey": "cost_usd", "primary": True}]
        }
    )
    assert runtime_metrics(selected)['cost']['direction'] == 'minimize'


def test_cursor_binds_scope_kind_and_filters():
    selected = scope()
    filters = {"cell_id": "first"}
    cursor = encode_cursor(selected, "executions", [str(uuid.UUID(int=8))], filters)
    assert decode_cursor(cursor, selected, "executions", filters) == [str(uuid.UUID(int=8))]
    for other, kind, other_filters in [
        (scope(protocol_revision_id=uuid.UUID(int=9)), "executions", filters),
        (selected, "cells", filters),
        (selected, "executions", {}),
    ]:
        with pytest.raises(ValueError):
            decode_cursor(cursor, other, kind, other_filters)


@pytest.mark.parametrize("cursor", ["!invalid!", "", "x" * 2049, "e30=", "bnVsbA=="])
def test_invalid_cursors_are_rejected(cursor):
    with pytest.raises(ValueError):
        decode_cursor(cursor, scope(), "executions", {})


@pytest.mark.parametrize("size", [-1, 0, 101, 100000])
def test_page_size_is_bounded(size):
    with pytest.raises(ValueError):
        page_size(size)


@pytest_asyncio.fixture
async def results_db():
    url = os.environ.get("ASAREE_RESULTS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set ASAREE_RESULTS_TEST_DATABASE_URL for isolated PostgreSQL checks")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        schema = "results_test_" + uuid.uuid4().hex
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

        def migrate(sync_connection):
            scripts = ScriptDirectory(str(Path(__file__).parents[1] / "src/asaree/migrations"))
            with Operations.context(MigrationContext.configure(sync_connection)):
                for revision in reversed(list(scripts.walk_revisions())):
                    revision.module.upgrade()

        await connection.run_sync(migrate)
        async with AsyncSession(bind=connection, expire_on_commit=False) as db:
            user = User(email="results@example.test", hashed_password="test", display_name="Results test")
            db.add(user)
            await db.flush()
            experiment = ResearchExperiment(name="Results test", owner_id=user.id, design_type="factorial")
            db.add(experiment)
            await db.flush()
            design = ExperimentDesignRevision(experiment_id=experiment.id, revision=1, design_spec={"factors": []})
            db.add(design)
            await db.flush()
            protocol = Protocol(name="Results protocol", owner_id=user.id, experiment_id=experiment.id, graph={})
            db.add(protocol)
            await db.flush()
            from datetime import UTC, datetime

            publication = ProtocolRevision(
                protocol_id=protocol.id,
                revision=1,
                graph={},
                experiment_snapshot={"design_spec": {"factors": []}},
                design_revision_id=design.id,
                published_at=datetime.now(UTC),
            )
            db.add(publication)
            await db.flush()
            protocol.published_revision_id = publication.id
            cell = FactorialCell(
                experiment_id=experiment.id, design_revision_id=design.id, cell_label="condition", factor_values={}
            )
            db.add(cell)
            await db.flush()
            selected = await resolve_result_scope(db, experiment_id=experiment.id, protocol_id=protocol.id)
            yield db, selected, cell, user
        await transaction.rollback()
    await engine.dispose()


@pytest.mark.parametrize("row_mode", [False, True])
async def test_usage_distribution_counts_latest_completed_values_across_all_slots(results_db, row_mode):
    from dataclasses import replace

    db, selected, cell, user = results_db
    dataset = RegisteredDataset(name="Distribution rows", owner_id=user.id)
    db.add(dataset)
    await db.flush()
    if row_mode:
        selected = replace(selected, row_plan={"driver_dataset_id": str(dataset.id)})
    now = datetime.now(UTC)
    for index in range(29):
        parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=index + 1,
                                         replicate_label=f"distribution-{index}")
        db.add(parent)
        await db.flush()
        row_id = None
        if row_mode:
            row = FactorialRowResult(replicate_result_id=parent.id,
                protocol_revision_id=selected.protocol_revision_id, dataset_id=dataset.id,
                raw_sha256="a" * 64, row_index=index)
            db.add(row)
            await db.flush()
            row_id = row.id
        usage = {"total_tokens": index * 1000, "cost_usd": index / 1000, "duration_seconds": index * 10}
        if index in (25, 26):
            usage = {key: None if index == 25 else True for key in usage}
        common = dict(protocol_id=selected.protocol_id, owner_id=user.id,
            protocol_revision_id=selected.protocol_revision_id, design_revision_id=selected.design_revision_id,
            replicate_result_id=parent.id, row_result_id=row_id, replicate_label=parent.replicate_label)
        db.add(ProtocolRun(**common, status="failed" if index == 27 else "running" if index == 28 else "completed",
                           usage_summary=usage, created_at=now))
        if index == 0:
            # A retry, test run and another publication must not enter the distribution.
            db.add(ProtocolRun(**common, status="completed", created_at=now - timedelta(seconds=1),
                usage_summary={key: 999999 for key in usage}))
            db.add(ProtocolRun(**common, status="completed", created_at=now + timedelta(seconds=1),
                usage_summary={key: 999999 for key in usage}, is_test_run=True))
            db.add(ProtocolRun(**(common | {"protocol_revision_id": None}), status="completed", created_at=now,
                usage_summary={key: 999999 for key in usage}))
    # Another cell with valid usage cannot affect this cell's histogram.
    other = FactorialCell(experiment_id=selected.experiment_id, design_revision_id=selected.design_revision_id,
                         cell_label="other", factor_values={})
    db.add(other)
    await db.flush()
    parent = FactorialReplicateResult(cell_id=other.id, replicate_number=1, replicate_label="other")
    db.add(parent)
    await db.flush()
    row_id = None
    if row_mode:
        row = FactorialRowResult(replicate_result_id=parent.id, protocol_revision_id=selected.protocol_revision_id,
            dataset_id=dataset.id, raw_sha256="a" * 64, row_index=0)
        db.add(row)
        await db.flush()
        row_id = row.id
    db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id,
        protocol_revision_id=selected.protocol_revision_id, design_revision_id=selected.design_revision_id,
        replicate_result_id=parent.id, row_result_id=row_id, replicate_label=parent.replicate_label,
        status="completed", usage_summary={"total_tokens": 999999}))
    await db.flush()
    for metric, scale in [("total_tokens", 1000), ("cost_usd", 0.001), ("duration_seconds", 10)]:
        distribution = await read_usage_distribution(db, selected, cell_id=cell.id, metric=metric)
        assert distribution["unit"] == ("row" if row_mode else "replicate")
        assert distribution["count"] == 25
        assert distribution["completed_count"] == 27
        assert distribution["minimum"] == 0
        assert distribution["maximum"] == pytest.approx(24 * scale)
        assert distribution["median"] == pytest.approx(12 * scale)
        assert distribution["p25"] == pytest.approx(6 * scale)
        assert distribution["p75"] == pytest.approx(18 * scale)
        assert len(distribution["bins"]) == 12
        assert sum(bucket["count"] for bucket in distribution["bins"]) == 25
        assert distribution["bins"][-1]["count"] >= 1  # Includes the upper endpoint.
        for bucket in distribution["bins"]:
            page = await read_executions(db, selected, cell_id=cell.id, usage_metric=metric,
                usage_min=bucket["lower"], usage_max=bucket["upper"], usage_upper_inclusive=bucket["upper_inclusive"])
            assert page["total"] == bucket["count"]
        for direction in ("asc", "desc"):
            items, cursor = [], None
            while True:
                page = await read_executions(db, selected, cell_id=cell.id, sort_metric=metric,
                    sort_direction=direction, limit=7, cursor=cursor)
                items.extend(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            values = [item["usage"][metric] for item in items]
            assert len(values) == 27  # Recorded failed/running usage is sortable, too.
            assert values == sorted(values, reverse=direction == "desc")


@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_usage_sorted_pages_keep_tied_values(results_db, direction):
    db, selected, cell, user = results_db
    for number in range(1, 7):
        parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=number, replicate_label=f"tie-{number}")
        db.add(parent)
        await db.flush()
        db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id, status="completed",
            protocol_revision_id=selected.protocol_revision_id, design_revision_id=selected.design_revision_id,
            replicate_result_id=parent.id, replicate_label=parent.replicate_label,
            usage_summary={"total_tokens": 100 if number < 6 else None}))
    await db.flush()
    numbers, cursor = [], None
    while True:
        page = await read_executions(db, selected, cell_id=cell.id, sort_metric="total_tokens",
                                    sort_direction=direction, limit=2, cursor=cursor)
        assert page["total"] == 5
        numbers.extend(item["replicate_number"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert numbers == [1, 2, 3, 4, 5]


async def test_execution_neighbors_stay_in_cell_and_publication_and_skip_unstarted(results_db):
    db, selected, cell, user = results_db
    other_cell = FactorialCell(experiment_id=selected.experiment_id, design_revision_id=selected.design_revision_id,
                               cell_label="other", factor_values={"model": "other"})
    other_publication = ProtocolRevision(protocol_id=selected.protocol_id, revision=2, graph={},
        experiment_snapshot={"design_spec": {"factors": []}}, design_revision_id=selected.design_revision_id,
        published_at=datetime.now(UTC))
    db.add_all([other_cell, other_publication])
    await db.flush()
    parents = {number: FactorialReplicateResult(cell_id=cell.id, replicate_number=number,
               replicate_label=f"replicate-{number}") for number in range(1, 13)}
    foreign = FactorialReplicateResult(cell_id=other_cell.id, replicate_number=2, replicate_label="other-2")
    db.add_all([*parents.values(), foreign])
    await db.flush()
    for parent, publication, status in [
        (parents[1], selected.protocol_revision_id, "completed"),
        (parents[2], other_publication.id, "completed"),
        (foreign, selected.protocol_revision_id, "completed"),
        (parents[10], selected.protocol_revision_id, "completed"),
        (parents[11], selected.protocol_revision_id, "failed"),
        (parents[12], selected.protocol_revision_id, "cancelled"),
    ]:
        db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id, status=status,
            protocol_revision_id=publication, design_revision_id=selected.design_revision_id,
            replicate_result_id=parent.id, replicate_label=parent.replicate_label, factor_values={}))
    await db.flush()
    assert await read_execution_neighbors(db, selected, slot_id=parents[10].id) == {
        "previous_slot_id": str(parents[1].id), "next_slot_id": str(parents[11].id)}
    assert await read_execution_neighbors(db, selected, slot_id=parents[1].id) == {
        "previous_slot_id": None, "next_slot_id": str(parents[10].id)}
    assert await read_execution_neighbors(db, selected, slot_id=parents[12].id) == {
        "previous_slot_id": str(parents[11].id), "next_slot_id": None}
    with pytest.raises(ValueError, match="slot_not_found"):
        await read_execution_neighbors(db, selected, slot_id=uuid.uuid4())


async def test_row_neighbors_follow_replicate_then_source_row_order(results_db):
    from dataclasses import replace

    db, selected, cell, user = results_db
    dataset = RegisteredDataset(name="Navigation rows", owner_id=user.id)
    parents = {number: FactorialReplicateResult(cell_id=cell.id, replicate_number=number,
               replicate_label=f"rep{number}") for number in (2, 10)}
    db.add_all([dataset, *parents.values()])
    await db.flush()
    selected = replace(selected, row_plan={"driver_dataset_id": str(dataset.id)})
    rows = []
    for number, index, status in [(2, 0, "completed"), (2, 1, None), (2, 9, "failed"), (10, 0, "completed")]:
        parent = parents[number]
        row = FactorialRowResult(replicate_result_id=parent.id, protocol_revision_id=selected.protocol_revision_id,
            dataset_id=dataset.id, raw_sha256="a" * 64, row_index=index)
        db.add(row)
        await db.flush()
        rows.append(row)
        if status:
            db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id, status=status,
                protocol_revision_id=selected.protocol_revision_id, design_revision_id=selected.design_revision_id,
                replicate_result_id=parent.id, row_result_id=row.id, replicate_label=parent.replicate_label))
    await db.flush()
    assert await read_execution_neighbors(db, selected, slot_id=rows[2].id) == {
        "previous_slot_id": str(rows[0].id), "next_slot_id": str(rows[3].id)}


async def test_current_attempt_history_and_rerun_rollups(results_db):
    db, selected, cell, user = results_db
    parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label="same-label")
    db.add(parent)
    await db.flush()
    ids = []
    for index, status in enumerate(("completed", "failed")):
        run = ProtocolRun(
            protocol_id=selected.protocol_id,
            owner_id=user.id,
            status=status,
            created_at=datetime.now(UTC) + timedelta(seconds=index),
            protocol_revision_id=selected.protocol_revision_id,
            design_revision_id=selected.design_revision_id,
            replicate_result_id=parent.id,
            replicate_label=parent.replicate_label,
            factor_values={},
            node_runs={"agent": {"status": status, "output_text": "x" * 100000}},
            conversation={"messages": [{"text": "y" * 100000}]},
        )
        db.add(run)
        await db.flush()
        ids.append(run.id)
    page = await read_executions(db, selected)
    assert len(page["items"]) == 1
    assert page["items"][0]["run_id"] == str(ids[-1])
    assert "node_runs" not in page["items"][0]
    history = await read_attempts(db, selected, slot_id=parent.id, limit=1)
    assert len(history["items"]) == 1
    assert history["next_cursor"]
    assert "conversation" not in history["items"][0]
    next_page = await read_attempts(db, selected, slot_id=parent.id, limit=1, cursor=history["next_cursor"])
    assert next_page["items"][0]["run_id"] == str(ids[0])
    detail = await read_attempt_detail(db, selected, ids[0])
    assert len(detail["node_runs"]["agent"]["output_text"]) == 4096
    messages = await read_conversation(db, selected, ids[0], limit=1)
    assert messages["items"][0]["truncated"] is True
    assert len(messages["items"][0]["preview"]) == 4096
    assert (await read_evidence(db, selected, ids[0], document="node_runs"))["next_offset"] == 16384
    refresh = await db.get(ResultRefresh, selected.key)
    await rebuild_result_summary(db, selected, refresh.generation)
    await db.refresh(refresh)
    summary = await read_summary(db, selected)
    assert summary["total"] == 1
    assert summary["counts"]["failed"] == 1
    assert summary["counts"]["completed"] == 0
    assert summary["protocol_revision"] == 1
    assert summary["design_revision"] == 1
    assert len((await read_cells(db, selected))["items"]) == 1


async def test_cell_result_readiness_requires_all_planned_replicates_and_applicable_measurements(results_db):
    from dataclasses import replace

    db, selected, first, user = results_db
    selected = replace(selected, design_spec={'metrics': [
        {'id': 'auc', 'name': 'ROC AUC', 'kind': 'custom', 'valueType': 'number'},
        {'id': 'calibration', 'name': 'Calibration', 'kind': 'custom', 'valueType': 'number'},
    ]})
    good = ('completed', 'measured', 'measured', False, 'completed')
    waiting = ('not_started', None, None, False, None)
    cases = {
        'ready_all': ([good, good], 'ready'),
        'ready_not_applicable': ([('completed', 'measured', 'not_applicable', False, 'completed')], 'ready'),
        'missing_measurement': ([('completed', 'measured', None, False, 'completed')], 'needs_attention'),
        'unavailable_measurement': ([('completed', 'measured', 'unavailable', False, 'completed')], 'needs_attention'),
        'failed_and_pending': ([('failed', None, None, False, None), waiting], 'needs_attention'),
        'cancelled': ([('cancelled', None, None, False, None)], 'needs_attention'),
        'truncated': ([('completed', 'measured', 'measured', True, 'completed')], 'needs_attention'),
        'no_measured_values': (
            [('completed', 'not_applicable', 'not_applicable', False, 'completed')], 'needs_attention',
        ),
        'measurement_failed': ([('completed', 'measured', None, False, 'failed')], 'needs_attention'),
        'scoring_pending': ([('completed', 'measured', None, False, 'running')], 'awaiting_results'),
        'partial': ([good, waiting], 'awaiting_results'),
        'not_started': ([waiting], 'awaiting_results'),
    }
    for index, (label, (attempts, _expected)) in enumerate(cases.items()):
        cell = first if index == 0 else FactorialCell(experiment_id=selected.experiment_id,
            design_revision_id=selected.design_revision_id, cell_label=label, factor_values={})
        cell.cell_label = label
        db.add(cell)
        await db.flush()
        for number, (status, auc, calibration, truncated, evaluation) in enumerate(attempts, 1):
            parent = FactorialReplicateResult(
                cell_id=cell.id, replicate_number=number, replicate_label=f'{label}-{number}',
            )
            db.add(parent)
            await db.flush()
            if status != 'not_started':
                if label == 'ready_all' and number == 1:
                    db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id,
                        protocol_revision_id=selected.protocol_revision_id,
                        design_revision_id=selected.design_revision_id,
                        replicate_result_id=parent.id, replicate_label=parent.replicate_label, status='failed',
                        created_at=datetime.now(UTC) - timedelta(days=1), result_summary={}))
                db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id,
                    protocol_revision_id=selected.protocol_revision_id, design_revision_id=selected.design_revision_id,
                    replicate_result_id=parent.id, replicate_label=parent.replicate_label, status=status,
                    node_runs={'agent': {'truncation': {}}} if truncated else {},
                    attempt_result={'evaluation_state': evaluation, 'measurement': {'observations': [
                        {'metric_id': identifier, 'status': state}
                        for identifier, state in [('auc', auc), ('calibration', calibration)] if state
                    ]}}))
    await db.flush()
    await read_summary(db, selected)
    refresh = await db.get(ResultRefresh, selected.key)
    await rebuild_result_summary(db, selected, refresh.generation)
    await db.refresh(refresh)
    cells = (await read_cells(db, selected, limit=100))['items']
    for cell in cells:
        assert cell['result_status'] == cases[cell['cell_label']][1], cell['cell_label']
        assert sum(cell['result_issues'].values()) == cell['results_needing_attention']
    by_label = {cell['cell_label']: cell for cell in cells}
    assert by_label['missing_measurement']['result_issues']['measurements_missing'] == 1
    assert by_label['failed_and_pending']['result_issues']['failed'] == 1
    assert by_label['cancelled']['result_issues']['cancelled'] == 1
    assert by_label['truncated']['result_issues']['limit_reached'] == 1
    assert by_label['measurement_failed']['result_issues']['measurement_failed'] == 1
    assert by_label['ready_all']['result_issues']['failed'] == 0
    counts = (await read_summary(db, selected))['cell_result_counts']
    assert counts == {'ready': 2, 'needs_attention': 7, 'awaiting_results': 3}
    assert sum(counts.values()) == len(cases)
    for status, total in counts.items():
        filtered = await read_cells(db, selected, result_status=status, limit=100)
        assert filtered['total'] == total
        assert len(filtered['items']) == total
        assert all(item['result_status'] == status for item in filtered['items'])
    searched = await read_cells(db, selected, result_status='needs_attention', search='missing')
    assert searched['total'] == 1
    assert searched['items'][0]['cell_label'] == 'missing_measurement'
    first_page = await read_cells(db, selected, result_status='ready', limit=1)
    second_page = await read_cells(db, selected, result_status='ready', limit=1, cursor=first_page['next_cursor'])
    assert first_page['total'] == second_page['total'] == 2
    assert first_page['items'][0]['cell_id'] != second_page['items'][0]['cell_id']
    with pytest.raises(ValueError, match='cursor_scope_mismatch'):
        await read_cells(db, selected, result_status='needs_attention', limit=1, cursor=first_page['next_cursor'])


async def test_results_context_uses_published_dataset_names(results_db):
    db, selected, _cell, _user = results_db
    publication = await db.get(ProtocolRevision, selected.protocol_revision_id)
    publication.graph = {'nodes': [
        {'id': 'dataset', 'type': 'dataset', 'data': {'config': {
            'dataset_id': 'dataset-id', 'dataset_name': 'Published dataset',
        }}},
        {'id': 'unused', 'type': 'dataset', 'data': {'config': {
            'dataset_id': 'unused', 'dataset_name': 'Disconnected',
        }}},
        {'id': 'agent', 'type': 'agent', 'data': {'config': {}}},
    ], 'edges': [{'id': 'input', 'source': 'dataset', 'target': 'agent', 'sourceHandle': 'dataset'}]}
    protocol = await db.get(Protocol, selected.protocol_id)
    protocol.graph = {'nodes': [
        {'id': 'draft', 'type': 'dataset', 'data': {'config': {'dataset_name': 'Draft dataset'}}},
    ], 'edges': []}
    await db.flush()
    pinned = await resolve_result_scope(db, experiment_id=selected.experiment_id, protocol_id=selected.protocol_id)
    context = await read_summary(db, pinned)
    assert context['datasets'] == [{'id': 'dataset-id', 'name': 'Published dataset'}]


async def test_row_cell_waits_for_all_forecast_rows_before_becoming_ready(results_db):
    from dataclasses import replace

    db, selected, cell, user = results_db
    dataset = RegisteredDataset(name='Row readiness', owner_id=user.id)
    parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label='rep1')
    db.add_all([dataset, parent])
    await db.flush()
    selected = replace(selected, row_plan={'driver_dataset_id': str(dataset.id)}, design_spec={
        'metrics': [{'id': 'auc', 'name': 'ROC AUC', 'kind': 'custom', 'valueType': 'number'}],
    })
    await read_summary(db, selected)
    refresh = await db.get(ResultRefresh, selected.key)
    refresh.summary = {'source_row_count': 2}
    for index in range(2):
        row = FactorialRowResult(replicate_result_id=parent.id, protocol_revision_id=selected.protocol_revision_id,
            dataset_id=dataset.id, raw_sha256='a' * 64, row_index=index)
        db.add(row)
        await db.flush()
        db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id,
            protocol_revision_id=selected.protocol_revision_id, design_revision_id=selected.design_revision_id,
            replicate_result_id=parent.id, row_result_id=row.id,
            replicate_label=parent.replicate_label, status='completed',
            attempt_result={'measurement': {'observations': [{'metric_id': 'auc', 'status': 'measured'}]}}))
        await db.flush()
        await rebuild_result_summary(db, selected, refresh.generation)
        await db.refresh(refresh)
        counts = (await read_summary(db, selected))['cell_result_counts']
        assert counts == ({'ready': 0, 'needs_attention': 0, 'awaiting_results': 1} if index == 0
                          else {'ready': 1, 'needs_attention': 0, 'awaiting_results': 0})


async def test_older_summary_requests_one_cell_result_rebuild(results_db):
    db, selected, _cell, _user = results_db
    await read_summary(db, selected)
    refresh = await db.get(ResultRefresh, selected.key)
    refresh.summary = {'total': 0, 'cell_result_counts': {'ready': 0, 'needs_attention': 0, 'awaiting_results': 0}}
    refresh.generation = refresh.processed_generation = 1
    await db.flush()
    first = await read_summary(db, selected)
    second = await read_summary(db, selected)
    assert first['refreshing'] is True and second['refreshing'] is True
    assert refresh.generation == 2



@pytest.mark.parametrize('labels', [('Cell 10', 'Cell 2'), ('size=10|model=A', 'size=2|model=A')])
async def test_run_browser_natural_order_filters_and_cursor_scope(results_db, labels):
    db, selected, first, user = results_db
    first.cell_label = labels[0]
    first.factor_values = {'model': 'B', 'temperature': 10}
    second = FactorialCell(experiment_id=selected.experiment_id, design_revision_id=selected.design_revision_id,
                           cell_label=labels[1], factor_values={'model': 'A', 'temperature': 2})
    db.add(second)
    await db.flush()
    for cell in (first, second):
        for number in (10, 2):
            parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=number,
                                             replicate_label=f'{cell.cell_label}__rep{number}')
            db.add(parent)
            await db.flush()
            db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id,
                               protocol_revision_id=selected.protocol_revision_id,
                               design_revision_id=selected.design_revision_id, replicate_result_id=parent.id,
                               replicate_label=parent.replicate_label, factor_values=cell.factor_values,
                               status='failed' if number == 2 else 'completed', node_runs={}))
    await db.flush()
    page = await read_cells(db, selected, limit=1)
    assert [cell['cell_label'] for cell in page['items']] == [labels[1]]
    assert page['items'][0]['cell_number'] == 1
    next_page = await read_cells(db, selected, limit=1, cursor=page['next_cursor'])
    assert page['total'] == next_page['total'] == 2
    assert [cell['cell_label'] for cell in next_page['items']] == [labels[0]]
    assert next_page['items'][0]['cell_number'] == 2
    filtered_second = await read_cells(db, selected, search='model', status='failed', factors={'temperature': 10})
    assert filtered_second['items'][0]['cell_number'] == 2
    assert (await read_cell(db, selected, first.id, None))['cell_number'] == 2
    first_detail = await read_cell(db, selected, second.id, None)
    last_detail = await read_cell(db, selected, first.id, None)
    assert first_detail['previous_cell_id'] is None
    assert first_detail['next_cell_id'] == str(first.id)
    assert last_detail['previous_cell_id'] == str(second.id)
    assert last_detail['next_cell_id'] is None
    with pytest.raises(ValueError, match='cursor_scope_mismatch'):
        await read_cells(db, selected, limit=1, cursor=page['next_cursor'], status='failed')
    filtered = await read_cells(db, selected, search='model', status='failed', factors={'temperature': 2})
    assert filtered['total'] == 1
    assert [cell['cell_label'] for cell in filtered['items']] == [labels[1]]
    replicates = await read_executions(db, selected, cell_id=second.id)
    assert [item['replicate_number'] for item in replicates['items']] == [2, 10]
    assert all(item['row_index'] is None for item in replicates['items'])
    first_replicate = await read_executions(db, selected, cell_id=second.id, limit=1)
    next_replicate = await read_executions(db, selected, cell_id=second.id, limit=1,
                                         cursor=first_replicate['next_cursor'])
    assert [item['replicate_number'] for item in first_replicate['items']] == [2]
    assert [item['replicate_number'] for item in next_replicate['items']] == [10]
    failed = await read_executions(db, selected, cell_id=second.id, status='failed')
    assert failed['total'] == 1
    assert first_replicate['total'] == next_replicate['total'] == 2
    assert [item['replicate_number'] for item in failed['items']] == [2]
    assert (await read_cells(db, selected, search='%'))['items'] == []


async def test_cached_primary_summary_fields_are_inert(results_db):
    db, selected, _cell, _user = results_db
    refresh = await db.get(ResultRefresh, selected.key)
    if refresh is None:
        refresh = ResultRefresh(scope_key=selected.key, protocol_id=selected.protocol_id,
                                protocol_revision_id=selected.protocol_revision_id,
                                design_revision_id=selected.design_revision_id)
        db.add(refresh)
    refresh.summary = {'primary_metric': 'duration_seconds', 'primary_metric_direction': 'minimize',
                       'best_cell': {'cell_label': 'Old winner'}}
    await db.flush()
    summary = await read_summary(db, selected)
    assert summary['primary_metric'] is None
    assert summary['primary_metric_direction'] is None
    assert summary['best_cell'] is None


async def test_unplanned_dataset_rows_remain_browsable_when_filtered(results_db):
    from dataclasses import replace

    db, selected, cell, _user = results_db
    cell.factor_values = {'model': 'A'}
    parents = [FactorialReplicateResult(cell_id=cell.id, replicate_number=number, replicate_label=f'rep{number}')
               for number in (10, 2)]
    db.add_all(parents)
    await db.flush()
    selected = replace(selected, row_plan={'driver_dataset_id': str(uuid.uuid4())})
    refresh = await db.get(ResultRefresh, selected.key)
    if refresh is None:
        refresh = ResultRefresh(scope_key=selected.key, protocol_id=selected.protocol_id,
                                protocol_revision_id=selected.protocol_revision_id,
                                design_revision_id=selected.design_revision_id)
        db.add(refresh)
    refresh.summary = {'source_row_count': 3}
    await db.flush()
    first = await read_executions(db, selected, cell_id=cell.id, status='not_started',
                                  search='model', factors={'model': 'A'}, limit=2)
    assert [(row['replicate_number'], row['row_index']) for row in first['items']] == [(2, 0), (2, 1)]
    second = await read_executions(db, selected, cell_id=cell.id, status='not_started',
                                   search='model', factors={'model': 'A'}, limit=2, cursor=first['next_cursor'])
    assert [(row['replicate_number'], row['row_index']) for row in second['items']] == [(2, 2), (10, 0)]
    assert all(row['status'] == 'not_started' for row in first['items'])
    assert len((await read_cells(db, selected, status='not_started'))['items']) == 1


@pytest.mark.parametrize('status', [None, 'not_started'])
async def test_large_unplanned_source_returns_contiguous_bounded_pages(results_db, status):
    from dataclasses import replace

    db, selected, cell, _user = results_db
    db.add(FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label='rep1'))
    await db.flush()
    selected = replace(selected, row_plan={'driver_dataset_id': str(uuid.uuid4())})
    refresh = await db.get(ResultRefresh, selected.key)
    if refresh is None:
        refresh = ResultRefresh(scope_key=selected.key, protocol_id=selected.protocol_id,
                                protocol_revision_id=selected.protocol_revision_id,
                                design_revision_id=selected.design_revision_id)
        db.add(refresh)
    refresh.summary = {'source_row_count': 1_000_000}
    await db.flush()
    first = await read_executions(db, selected, status=status)
    second = await read_executions(db, selected, status=status, cursor=first['next_cursor'])
    assert [row['row_index'] for row in first['items']] == list(range(20))
    assert [row['row_index'] for row in second['items']] == list(range(20, 40))
    assert first['total'] == second['total'] == 1_000_000
    assert second['next_cursor'] is not None


async def test_unstarted_row_pages_skip_occupied_rows_and_keep_gaps(results_db):
    from dataclasses import replace

    db, selected, cell, user = results_db
    dataset = RegisteredDataset(name='Row gaps', owner_id=user.id)
    parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label='rep1')
    db.add_all([dataset, parent])
    await db.flush()
    selected = replace(selected, row_plan={'driver_dataset_id': str(dataset.id)})
    for index, status in [(0, 'completed'), (1, None), (2, 'failed'), (4, 'completed')]:
        row = FactorialRowResult(replicate_result_id=parent.id, protocol_revision_id=selected.protocol_revision_id,
                                 dataset_id=dataset.id, raw_sha256='a' * 64, row_index=index)
        db.add(row)
        await db.flush()
        if status is not None:
            db.add(ProtocolRun(protocol_id=selected.protocol_id, owner_id=user.id,
                               protocol_revision_id=selected.protocol_revision_id,
                               design_revision_id=selected.design_revision_id, replicate_result_id=parent.id,
                               row_result_id=row.id, replicate_label=parent.replicate_label,
                               status=status, node_runs={}))
    await db.flush()
    refresh = await db.get(ResultRefresh, selected.key)
    if refresh is None:
        refresh = ResultRefresh(scope_key=selected.key, protocol_id=selected.protocol_id,
                                protocol_revision_id=selected.protocol_revision_id,
                                design_revision_id=selected.design_revision_id)
        db.add(refresh)
    refresh.summary = {'source_row_count': 7}
    await db.flush()
    first = await read_executions(db, selected, status='not_started', limit=2)
    second = await read_executions(db, selected, status='not_started', limit=2, cursor=first['next_cursor'])
    assert [row['row_index'] for row in first['items']] == [1, 3]
    assert [row['row_index'] for row in second['items']] == [5, 6]
    assert first['total'] == second['total'] == 4
    assert second['next_cursor'] is None


async def test_opaque_previews_and_eligibility(results_db):
    db, selected, cell, user = results_db
    parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label="replicate")
    db.add(parent)
    await db.flush()
    run = ProtocolRun(
        protocol_id=selected.protocol_id,
        owner_id=user.id,
        status="completed",
        protocol_revision_id=selected.protocol_revision_id,
        design_revision_id=selected.design_revision_id,
        replicate_result_id=parent.id,
        replicate_label=parent.replicate_label,
        factor_values={},
        node_runs={},
        attempt_result={
            "measurement": {
                "observations": [
                    {
                        "metric_id": "opaque",
                        "metric_name": "Report",
                        "status": "measured",
                        "value": "x" * 100000,
                        "producer": {"kind": "reported"},
                    },
                    {
                        "metric_id": "runtime",
                        "metric_name": "tokens",
                        "status": "measured",
                        "value": 100,
                        "producer": {"kind": "runtime", "producer_id": "asaree.runtime"},
                    },
                ]
            }
        },
    )
    db.add(run)
    await db.flush()
    preview = (await read_executions(db, selected))["items"][0]["result"]
    assert preview["metrics"] == {"runtime": 100}
    assert len(preview["observations"][0]["preview"]) == 512
    assert preview["observations"][0]["preview_truncated"]
    detail = await read_attempt_detail(db, selected, run.id)
    assert detail["attempt_result"]["measurement"]["observations"][0]["value_truncated"]
    chunks = []
    offset = 0
    while True:
        part = await read_evidence(db, selected, run.id, document="attempt_result", offset=offset)
        chunks.append(part["text"])
        if part["next_offset"] is None:
            break
        offset = part["next_offset"]
    assert json.loads("".join(chunks))["measurement"]["observations"][0]["value"] == "x" * 100000


async def test_version_scope_never_reads_a_different_publication(results_db):
    db, selected, cell, user = results_db
    parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label="parent")
    db.add(parent)
    await db.flush()
    other = ProtocolRevision(
        protocol_id=selected.protocol_id,
        revision=2,
        graph={},
        experiment_snapshot={"design_spec": {}},
        design_revision_id=selected.design_revision_id,
        published_at=datetime.now(UTC),
    )
    db.add(other)
    await db.flush()
    run = ProtocolRun(
        protocol_id=selected.protocol_id,
        owner_id=user.id,
        status="completed",
        protocol_revision_id=other.id,
        design_revision_id=selected.design_revision_id,
        replicate_result_id=parent.id,
        replicate_label=parent.replicate_label,
        factor_values={},
        node_runs={},
    )
    db.add(run)
    await db.flush()
    assert (await read_executions(db, selected))["items"][0]["run_id"] is None
    with pytest.raises(ValueError, match="attempt_not_found"):
        await read_attempt_detail(db, selected, run.id)
    with pytest.raises(ValueError, match="protocol_not_found"):
        await resolve_result_scope(db, experiment_id=uuid.uuid4(), protocol_id=selected.protocol_id)


async def test_additive_revision_changes_cannot_expand_a_published_cohort(results_db):
    db, selected, cell, user = results_db
    publication = await db.get(ProtocolRevision, selected.protocol_revision_id)
    publication.experiment_snapshot = {
        "design_spec": {"factors": [{"name": "model", "levels": ["a"]}], "replicates": 1}
    }
    cell.factor_values = {"model": "a"}
    db.add_all(
        [
            FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label="a-1"),
            FactorialReplicateResult(cell_id=cell.id, replicate_number=2, replicate_label="a-2"),
        ]
    )
    extra = FactorialCell(
        experiment_id=selected.experiment_id,
        design_revision_id=selected.design_revision_id,
        cell_label="model=b",
        factor_values={"model": "b"},
    )
    db.add(extra)
    await db.flush()
    db.add(FactorialReplicateResult(cell_id=extra.id, replicate_number=1, replicate_label="b-1"))
    await db.flush()
    pinned = await resolve_result_scope(db, experiment_id=selected.experiment_id, protocol_id=selected.protocol_id)
    page = await read_executions(db, pinned)
    assert len(page["items"]) == 1
    assert page["items"][0]["replicate_label"] == "a-1"
    refresh = await db.get(ResultRefresh, pinned.key)
    await rebuild_result_summary(db, pinned, refresh.generation)
    await db.refresh(refresh)
    overview = await read_summary(db, pinned)
    assert overview["cell_count"] == 1 and overview["replicate_count"] == 1


async def test_cell_stop_excludes_only_that_cell_from_existing_planners(results_db):
    db, selected, cell, user = results_db
    parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label="parent")
    db.add(parent)
    await db.flush()
    plan = await result_jobs.create_job(db, selected, user.id, kind="plan", request_key="plan", params={})
    plan.status = "queued"
    plan.progress = {"validated": True}
    db.add(ResultJobMember(job_id=plan.id, replicate_id=parent.id, row_index=-1, processed=False, delivered=False))
    await db.flush()
    await result_jobs.create_job(
        db, selected, user.id, kind="cancel", request_key="cell-stop", params={"cell_id": str(cell.id)}
    )
    await db.flush()
    await db.refresh(plan)
    assert plan.status == "queued"
    assert plan.params["excluded_cell_ids"] == [str(cell.id)]
    await result_jobs.plan_step(db, plan, selected)
    await db.flush()
    assert plan.progress["skipped"] == 1
    assert await db.scalar(select(func.count()).select_from(ProtocolRun)) == 0


async def test_batch_preview_seals_attempts_and_skips_changed_slots(results_db, monkeypatch):
    db, selected, cell, user = results_db
    parent = FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label="parent")
    db.add(parent)
    await db.flush()
    run = ProtocolRun(
        protocol_id=selected.protocol_id,
        owner_id=user.id,
        status="failed",
        protocol_revision_id=selected.protocol_revision_id,
        design_revision_id=selected.design_revision_id,
        replicate_result_id=parent.id,
        replicate_label=parent.replicate_label,
        factor_values={},
        node_runs={},
    )
    db.add(run)
    await db.flush()
    planner = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(result_jobs, "plan_cell_runs", planner)
    job = await result_jobs.create_job(db, selected, user.id, kind="plan", request_key="retry-key", params={})
    assert (
        await result_jobs.create_job(db, selected, user.id, kind="plan", request_key="retry-key", params={})
    ).id == job.id
    await result_jobs.prepare_step(db, job, selected)
    await db.flush()
    await result_jobs.prepare_step(db, job, selected)
    assert job.status == "awaiting_confirmation"
    assert job.progress["planned"] == 1
    planner.assert_awaited_once()
    assert planner.call_args.kwargs["replicate_ids"] == set()
    member = await db.scalar(select(ResultJobMember).where(ResultJobMember.job_id == job.id))
    assert member.expected_run_id == run.id
    newer = ProtocolRun(
        protocol_id=selected.protocol_id,
        owner_id=user.id,
        status="completed",
        protocol_revision_id=selected.protocol_revision_id,
        design_revision_id=selected.design_revision_id,
        replicate_result_id=parent.id,
        replicate_label=parent.replicate_label,
        factor_values={},
        node_runs={},
        created_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    db.add(newer)
    await db.flush()
    job.status = "queued"
    await result_jobs.plan_step(db, job, selected)
    await db.flush()
    await result_jobs.plan_step(db, job, selected)
    assert job.status == "completed"
    assert job.progress["skipped"] == 1
    planner.assert_awaited_once()  # Confirmation did not re-bill the changed attempt.


@pytest.mark.skipif(os.environ.get("ASAREE_RESULTS_RUN_SCALE") != "1", reason="Opt-in live PostgreSQL scale acceptance")
@pytest.mark.parametrize("count", [15786, 100000, 1000000])
async def test_scale_pages_and_cached_summary_stay_bounded(results_db, count):
    db, selected, cell, user = results_db
    await db.execute(
        text("""
        INSERT INTO factorial_replicate_results(id,cell_id,replicate_number,replicate_label)
        SELECT md5('parent-' || i)::uuid, :cell, i, 'parent-' || i FROM generate_series(1,:count) i
    """),
        {"cell": cell.id, "count": count},
    )
    await db.execute(
        text("""
        INSERT INTO protocol_runs(id,protocol_id,owner_id,status,is_test_run,node_runs,attempt_result,
            replicate_result_id,replicate_label,factor_values,protocol_revision_id,design_revision_id)
        SELECT md5('run-' || replicate_number)::uuid, :protocol, :owner, 'completed', false,
            jsonb_build_object('agent',jsonb_build_object('status','completed','output_text',repeat(md5(id::text),1024))),
            jsonb_build_object('measurement',jsonb_build_object('observations',jsonb_build_array(
                jsonb_build_object('metric_id','report','metric_name','Report','status','measured',
                    'value',repeat(md5(id::text),256),'producer',jsonb_build_object('kind','reported'))))),
            id,replicate_label,'{}'::jsonb,:publication,:design
        FROM factorial_replicate_results WHERE cell_id=:cell
    """),
        {
            "protocol": selected.protocol_id,
            "owner": user.id,
            "publication": selected.protocol_revision_id,
            "design": selected.design_revision_id,
            "cell": cell.id,
        },
    )
    await db.execute(text("ANALYZE factorial_replicate_results"))
    await db.execute(text("ANALYZE protocol_runs"))
    refresh = await db.get(ResultRefresh, selected.key)
    await rebuild_result_summary(db, selected, refresh.generation)
    await db.refresh(refresh)
    started = time.monotonic()
    page = await read_executions(db, selected)
    summary = await read_summary(db, selected)
    elapsed = time.monotonic() - started
    assert len(page["items"]) == 20
    assert page["next_cursor"]
    assert len(json.dumps(page, default=str).encode()) < 65536
    assert len(json.dumps(summary, default=str).encode()) < 16384
    assert summary["counts"]["completed"] == count
    assert elapsed < 2, f"Cached summary + page exceeded 2s: {elapsed:.3f}s at {count} runs"


async def test_cell_summaries_aggregate_replicates_in_sql(results_db):
    db, selected, cell, _ = results_db
    other = FactorialCell(
        experiment_id=cell.experiment_id,
        design_revision_id=cell.design_revision_id,
        cell_label="other",
        factor_values={},
    )
    db.add(other)
    await db.flush()
    db.add_all(
        [
            FactorialReplicateResult(cell_id=cell.id, replicate_number=1, replicate_label="condition",
                                     metric_values={"score": 1, "passed": True, "note": "text"}),
            FactorialReplicateResult(cell_id=cell.id, replicate_number=2, replicate_label="condition__rep2",
                                     metric_values={"score": 3.5, "passed": False}),
            FactorialReplicateResult(cell_id=cell.id, replicate_number=3, replicate_label="condition__rep3"),
            FactorialReplicateResult(cell_id=other.id, replicate_number=1, replicate_label="other"),
        ]
    )
    await db.flush()
    summaries = {
        row["cell_label"]: row
        for row in await summarize_cells(db, experiment_id=cell.experiment_id, revision_id=cell.design_revision_id)
    }
    assert summaries["condition"]["replicate_count"] == 3
    assert summaries["condition"]["scored_count"] == 2
    assert summaries["condition"]["metrics"] == {"score": {"sum": 4.5, "count": 2}, "passed": {"sum": 1.0, "count": 2}}
    assert summaries["other"] == {**summaries["other"], "replicate_count": 1, "scored_count": 0, "metrics": {}}
    # A revision id from another experiment never leaks its cells.
    assert await summarize_cells(db, experiment_id=uuid.uuid4(), revision_id=cell.design_revision_id) == []


async def test_streamed_export_matches_the_analysis_csv(results_db, monkeypatch):
    """The background export writes the same researcher-facing CSV as the
    synchronous one -- factor, usage and metric columns -- in bounded chunks.
    Two cells share replicate labels, which are only unique within a cell."""
    from types import SimpleNamespace

    from asaree.services import result_exports
    from asaree.services.csv_export import result_rows_to_csv
    from asaree.services.experiment_run_results import summarize_experiment_run_results

    db, selected, cell, user = results_db
    cell.factor_values = {"arm": "left"}
    other = FactorialCell(
        experiment_id=cell.experiment_id,
        design_revision_id=cell.design_revision_id,
        cell_label="other",
        factor_values={"arm": "right"},
    )
    db.add(other)
    await db.flush()
    for target in (cell, other):
        for number in (1, 2, 3):
            parent = FactorialReplicateResult(
                cell_id=target.id, replicate_number=number, replicate_label=f"rep{number}"
            )
            db.add(parent)
            await db.flush()
            run = ProtocolRun(
                protocol_id=selected.protocol_id,
                owner_id=user.id,
                status="completed",
                protocol_revision_id=selected.protocol_revision_id,
                design_revision_id=selected.design_revision_id,
                replicate_result_id=parent.id,
                replicate_label=parent.replicate_label,
                factor_values=target.factor_values,
                node_runs={"agent": {"status": "completed", "output_text": "ok"}},
                usage_summary={"settled": True, "input_tokens": 100 * number, "output_tokens": 10,
                               "total_tokens": 100 * number + 10, "cost_usd": 0.001 * number, "duration_seconds": 2.5},
                attempt_result={"measurement": {"observations": [{
                    "metric_id": "score", "metric_name": "Score", "status": "measured", "value": number / 10,
                    "producer": {"kind": "runtime", "producer_id": "asaree.runtime"}}], "artifacts": []}},
            )
            db.add(run)
            await db.flush()
            parent.run_id = run.id
    await db.flush()

    experiment = await db.get(ResearchExperiment, cell.experiment_id)
    results = await summarize_experiment_run_results(
        db, experiment_id=experiment.id, design_spec=experiment.design_spec, protocol_id=selected.protocol_id,
        design_revision_id=selected.design_revision_id, protocol_revision_id=selected.protocol_revision_id,
        bounded=False,
    )
    expected = result_rows_to_csv(
        results["replicates"], results.get("selected_design_spec", experiment.design_spec),
        consumption_mode=results["consumption_mode"],
    )
    monkeypatch.setattr(result_exports, "EXPORT_CHUNK_ROWS", 2)
    chunks: list[bytes] = []

    async def write(chunk: bytes) -> None:
        chunks.append(chunk)

    job = SimpleNamespace(experiment_id=experiment.id, params={"selection": {}})
    await result_exports._write_analysis_csv(db, job, selected, write)
    streamed = b"".join(chunks).decode()
    assert streamed == expected
    header = streamed.splitlines()[0].split(",")
    assert {"arm", "input_tokens", "total_tokens", "cost_usd", "duration_seconds"} <= set(header)
    assert len(streamed.splitlines()) == 7  # header + 6 replicates, none lost to the shared labels


async def test_preview_checks_a_thousand_replicates_per_step(results_db, monkeypatch):
    """A step used to check 100 replicates and the worker ran one step a second,
    so a 15,786-replicate preview took over two and a half minutes."""
    db, selected, cell, user = results_db
    db.add_all(
        FactorialReplicateResult(cell_id=cell.id, replicate_number=n, replicate_label=f"r{n}") for n in range(1, 2501)
    )
    await db.flush()
    monkeypatch.setattr(result_jobs, "plan_cell_runs", AsyncMock(return_value=([], 0)))
    job = await result_jobs.create_job(db, selected, user.id, kind="plan", request_key="big-preview", params={})
    steps = 0
    while job.status == "pending":
        await result_jobs.prepare_step(db, job, selected)
        await db.flush()
        steps += 1
    assert job.status == "awaiting_confirmation"
    assert job.progress["planned"] == job.progress["scanned"] == 2500
    assert steps == 4  # three windows of up to 1,000 (the first also validates), then finish


async def test_per_row_preview_fits_several_replicates_in_one_step(results_db, monkeypatch):
    """Per row, a step used to cover a single replicate however few source rows
    it had: one replicate a second."""
    import dataclasses

    db, selected, cell, user = results_db
    db.add_all(
        FactorialReplicateResult(cell_id=cell.id, replicate_number=n, replicate_label=f"r{n}") for n in range(1, 101)
    )
    await db.flush()
    row_scope = dataclasses.replace(selected, row_plan={"driver_dataset_id": str(uuid.uuid4())})
    assert row_scope.unit == "row"
    job = await result_jobs.create_job(db, row_scope, user.id, kind="plan", request_key="row-preview", params={})
    # 30 verified source rows; validation already done.
    job.progress = {"validated": True, "source": {"dataset_id": str(uuid.uuid4()), "sha256": "0" * 64,
                                                  "columns": ["question"], "count": 30}}
    await result_jobs.prepare_step(db, job, row_scope)
    assert job.progress["scanned"] == (1000 // 30) * 30  # 33 whole replicates x 30 rows in one step
    steps = 1
    while job.status == "pending":
        await result_jobs.prepare_step(db, job, row_scope)
        await db.flush()
        steps += 1
    assert job.progress["planned"] == job.progress["scanned"] == 100 * 30
    assert steps == 5  # 33 + 33 + 33 + 1 replicates, then finish


@pytest.mark.parametrize("versioned", [True, False])
@pytest.mark.parametrize("metrics", [[], [{"id": "auc", "name": "ROC AUC", "kind": "custom"}]])
def test_slot_query_never_binds_boolean_flags(versioned, metrics):
    """A bound True/False can't be folded in a generic plan: a reused connection
    then couldn't simplify `replicate_result_id = x OR (<legacy> AND ...)` and
    scanned every run of the design per slot (rebuild 0.4 s -> 64 s)."""
    selected = scope(versioned=versioned, design_spec={"metrics": metrics})
    statement = slot_query(selected, cutoff=datetime.now(UTC))
    params = statement.compile(dialect=postgresql.dialect()).params
    assert not [name for name, value in params.items() if isinstance(value, bool)]
    rollup = rollup_query(selected).compile(dialect=postgresql.dialect()).params
    assert not [name for name, value in rollup.items() if isinstance(value, bool)]
