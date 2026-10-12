"""Read contracts, SQL scoping and optional isolated PostgreSQL integration checks.

Set ASAREE_RESULTS_TEST_DATABASE_URL to a disposable PostgreSQL database. All
migrations and fixtures live in a unique schema inside a rolled-back transaction.
This suite never reads application credential files or touches application tables.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.result_read_model import ResultRefresh
from asaree.models.user import User
from asaree.services.result_reads import (
    ResultScope,
    decode_cursor,
    encode_cursor,
    page_size,
    read_attempt_detail,
    read_attempts,
    read_cells,
    read_executions,
    read_summary,
    resolve_result_scope,
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
    for status in ("completed", "failed"):
        run = ProtocolRun(
            protocol_id=selected.protocol_id,
            owner_id=user.id,
            status=status,
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
    assert len(detail["node_runs"]["agent"]["output_text"]) == 100000
    refresh = await db.get(ResultRefresh, selected.key)
    await rebuild_result_summary(db, selected, refresh.generation)
    await db.refresh(refresh)
    summary = await read_summary(db, selected)
    assert summary["total"] == 1
    assert summary["counts"]["failed"] == 1
    assert summary["counts"]["completed"] == 0
    assert len((await read_cells(db, selected))["items"]) == 1


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
                        "producer": {"kind": "runtime"},
                    },
                ]
            }
        },
    )
    db.add(run)
    await db.flush()
    preview = (await read_executions(db, selected))["items"][0]["result"]
    assert preview["metrics"] == {"tokens": 100}
    assert len(preview["observations"][0]["preview"]) == 512
    assert preview["observations"][0]["preview_truncated"]
    assert (await read_attempt_detail(db, selected, run.id))["attempt_result"]["measurement"]["observations"][0][
        "value"
    ] == "x" * 100000
