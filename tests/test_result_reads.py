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
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
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
    cell_predicates,
    decode_cursor,
    encode_cursor,
    page_size,
    parent_predicates,
    primary_runtime_metric,
    read_attempt_detail,
    read_attempts,
    read_cells,
    read_conversation,
    read_evidence,
    read_execution_replicates,
    read_executions,
    read_summary,
    resolve_result_scope,
    runtime_metrics,
    slot_query,
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
    assert primary_runtime_metric(selected) == ("node-tokens", "minimize")
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


def test_catalog_defaults_determine_primary_direction_when_omitted():
    selected = scope(
        design_spec={
            "metrics": [{"id": "cost", "name": "Cost", "kind": "runtime", "catalogKey": "cost_usd", "primary": True}]
        }
    )
    assert primary_runtime_metric(selected) == ("cost_usd", "minimize")


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
    assert len((await read_cells(db, selected))["items"]) == 1


async def test_run_browser_natural_order_filters_and_cursor_scope(results_db):
    db, selected, first, user = results_db
    first.cell_label = 'Cell 10'
    first.factor_values = {'model': 'B', 'temperature': 10}
    second = FactorialCell(experiment_id=selected.experiment_id, design_revision_id=selected.design_revision_id,
                           cell_label='Cell 2', factor_values={'model': 'A', 'temperature': 2})
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
    assert [cell['cell_label'] for cell in page['items']] == ['Cell 2']
    next_page = await read_cells(db, selected, limit=1, cursor=page['next_cursor'])
    assert [cell['cell_label'] for cell in next_page['items']] == ['Cell 10']
    with pytest.raises(ValueError, match='cursor_scope_mismatch'):
        await read_cells(db, selected, limit=1, cursor=page['next_cursor'], status='failed')
    filtered = await read_cells(db, selected, search='model', status='failed', factors={'temperature': 2})
    assert [cell['cell_label'] for cell in filtered['items']] == ['Cell 2']
    assert filtered['items'][0]['matching_count'] == 1
    replicates = await read_executions(db, selected, cell_id=second.id)
    assert [item['replicate_number'] for item in replicates['items']] == [2, 10]
    assert all(item['row_index'] is None for item in replicates['items'])
    first_replicate = await read_executions(db, selected, cell_id=second.id, limit=1)
    next_replicate = await read_executions(db, selected, cell_id=second.id, limit=1,
                                         cursor=first_replicate['next_cursor'])
    assert [item['replicate_number'] for item in first_replicate['items']] == [2]
    assert [item['replicate_number'] for item in next_replicate['items']] == [10]
    failed = await read_executions(db, selected, cell_id=second.id, status='failed')
    assert [item['replicate_number'] for item in failed['items']] == [2]
    assert (await read_cells(db, selected, search='%'))['items'] == []


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
    groups = await read_execution_replicates(db, selected, cell_id=cell.id, status='not_started')
    assert [(group['replicate_number'], group['matching']) for group in groups['items']] == [(2, 3), (10, 3)]
    assert len((await read_cells(db, selected, status='not_started'))['items']) == 1


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
def test_slot_query_never_binds_boolean_flags(versioned):
    """A bound True/False can't be folded in a generic plan: a reused connection
    then couldn't simplify `replicate_result_id = x OR (<legacy> AND ...)` and
    scanned every run of the design per slot (rebuild 0.4 s -> 64 s)."""
    statement = slot_query(scope(versioned=versioned), cutoff=datetime.now(UTC))
    params = statement.compile(dialect=postgresql.dialect()).params
    assert not [name for name, value in params.items() if isinstance(value, bool)]
    rollup = rollup_query(scope(versioned=versioned)).compile(dialect=postgresql.dialect()).params
    assert not [name for name, value in rollup.items() if isinstance(value, bool)]
