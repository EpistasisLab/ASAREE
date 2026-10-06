"""PostgreSQL coverage for immutable row-attempt measurements and projections."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.dataset_row_csv import project_row, read_row_source
from asaree.services.measurement_engine import parse_measurement_plan
from asaree.services.protocol_runs import create_protocol_run, record_measurement_evaluation
from asaree.services.reported_metrics import collect_reported_metrics
from asaree.services.runtime_metrics import finalize_attempt_measurement
from asaree.services.users import get_user_by_email

PLAN = {
    "metrics": [
        {"id": "agent", "name": "Agent output"},
        {"id": "script", "name": "Script output"},
        {"id": "missing-agent", "name": "Missing Agent"},
        {"id": "missing-script", "name": "Missing Script"},
    ],
    "producers": [
        {
            "id": "agent-report",
            "producer_id": "asaree.agent_output",
            "kind": "reported",
            "outputs": {"value": "agent"},
            "config": {"agent_node_id": "agent"},
        },
        {
            "id": "script-report",
            "producer_id": "asaree.python_script",
            "kind": "reported",
            "outputs": {"value": "script"},
            "config": {"agent_node_id": "agent", "script_node_id": "score"},
        },
        {
            "id": "missing-agent-report",
            "producer_id": "asaree.agent_output",
            "kind": "reported",
            "outputs": {"value": "missing-agent"},
            "config": {"agent_node_id": "missing-agent"},
        },
        {
            "id": "missing-script-report",
            "producer_id": "asaree.python_script",
            "kind": "reported",
            "outputs": {"value": "missing-script"},
            "config": {"agent_node_id": "agent", "script_node_id": "missing-score"},
        },
    ],
    "inputs": [],
}
GRAPH = {
    "nodes": [
        {"id": "agent", "type": "agent", "data": {"config": {}}},
        {"id": "missing-agent", "type": "agent", "data": {"config": {}}},
        {"id": "score", "type": "script", "data": {"config": {"name": "score", "code": "return 1"}}},
        {
            "id": "missing-score",
            "type": "script",
            "data": {"config": {"name": "missing-score", "code": "return 1"}},
        },
    ],
    "edges": [
        {"source": "score", "target": "agent", "targetHandle": "tool"},
        {"source": "missing-score", "target": "agent", "targetHandle": "tool"},
    ],
}


@pytest_asyncio.fixture
async def row_measurements(tmp_path) -> AsyncIterator[tuple[AsyncSession, dict]]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            sessions = async_sessionmaker(bind=connection, expire_on_commit=False)
            async with sessions() as db:
                user = await get_user_by_email(db, "test@test.com")
                assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
                experiment_id, dataset_id, protocol_id = (uuid.uuid4() for _ in range(3))
                design_id, protocol_revision_id = uuid.uuid4(), uuid.uuid4()
                cell_id, replicate_id = uuid.uuid4(), uuid.uuid4()
                raw = b"input,value\nfirst,one\nsecond,two\nthird,three\n"
                source_path = tmp_path / "observations.csv"
                source_path.write_bytes(raw)
                assert source_path.is_file()
                digest = hashlib.sha256(raw).hexdigest()
                source = read_row_source(dataset_id=str(dataset_id), raw_path=str(source_path), raw_sha256=digest)
                workspace = tmp_path / "workspace"
                workspace.mkdir()
                (workspace / "trace.json").write_text('{"trace":true}', encoding="utf-8")
                assert (workspace / "trace.json").is_file()

                experiment = ResearchExperiment(
                    id=experiment_id,
                    name=f"row-measurements-{uuid.uuid4()}",
                    owner_id=user.id,
                    measurement_plan=PLAN,
                    design_spec={"factors": [], "metrics": []},
                )
                dataset = RegisteredDataset(
                    id=dataset_id,
                    name=f"row-measurements-{uuid.uuid4()}",
                    owner_id=user.id,
                    raw_path=str(source_path),
                    raw_sha256=digest,
                )
                protocol = Protocol(
                    id=protocol_id,
                    name=f"row-measurements-{uuid.uuid4()}",
                    owner_id=user.id,
                    experiment_id=experiment_id,
                    graph=GRAPH,
                )
                db.add_all([experiment, dataset, protocol])
                await db.flush()
                design = ExperimentDesignRevision(id=design_id, experiment_id=experiment_id, revision=1)
                revision = ProtocolRevision(
                    id=protocol_revision_id,
                    protocol_id=protocol_id,
                    revision=1,
                    graph=GRAPH,
                    published_at=datetime.now(UTC),
                )
                db.add_all([design, revision])
                await db.flush()
                protocol.published_revision_id = protocol_revision_id
                cell = FactorialCell(
                    id=cell_id,
                    experiment_id=experiment_id,
                    design_revision_id=design_id,
                    cell_label="row-cell",
                    factor_values={},
                )
                db.add(cell)
                await db.flush()
                replicate = FactorialReplicateResult(
                    id=replicate_id,
                    cell_id=cell_id,
                    replicate_number=1,
                    replicate_label="row-replicate",
                    run_id=uuid.uuid4(),
                    workspace_id="parent-workspace",
                    metric_values={"parent": "preserved"},
                    artifacts={"parent": True},
                )
                db.add(replicate)
                await db.flush()
                rows = [
                    FactorialRowResult(
                        replicate_result_id=replicate_id,
                        protocol_revision_id=protocol_revision_id,
                        dataset_id=dataset_id,
                        raw_sha256=digest,
                        row_index=index,
                        run_id=uuid.uuid4(),
                        workspace_id=f"old-workspace-{index}",
                        metric_values={"old": index},
                        artifacts={"old": index},
                    )
                    for index in range(3)
                ]
                db.add_all(rows)
                await db.flush()
                yield db, {
                    "user_id": user.id,
                    "experiment": experiment,
                    "protocol": protocol,
                    "design_id": design_id,
                    "protocol_revision_id": protocol_revision_id,
                    "replicate": replicate,
                    "rows": rows,
                    "source": source,
                    "workspace": workspace,
                }
            if transaction.is_active:
                await transaction.rollback()
    finally:
        await engine.dispose()


def _row_snapshot(ctx: dict, index: int) -> dict:
    return project_row(ctx["source"], row_index=index, columns=["input", "value"])


async def _create_attempt(db: AsyncSession, ctx: dict, index: int) -> ProtocolRun:
    return await create_protocol_run(
        db,
        protocol_id=ctx["protocol"].id,
        owner_id=ctx["user_id"],
        replicate_label="row-replicate",
        factor_values={},
        replicate_result_id=ctx["replicate"].id,
        row_result_id=ctx["rows"][index].id,
        dataset_row=_row_snapshot(ctx, index),
        design_revision_id=ctx["design_id"],
        protocol_revision_id=ctx["protocol_revision_id"],
    )


@pytest.mark.asyncio
async def test_finalize_row_attempts_freeze_opaque_reports_on_distinct_subjects(row_measurements, monkeypatch):
    db, ctx = row_measurements
    calls_by_run: dict[uuid.UUID, list[SimpleNamespace]] = {}

    async def trace(run_id):
        return calls_by_run.get(run_id, [])

    monkeypatch.setattr("asaree.services.reported_metrics.get_run_steps", trace)
    parent = ctx["replicate"]
    parent_before = (parent.run_id, parent.workspace_id, parent.metric_values, parent.artifacts)
    attempts = [await _create_attempt(db, ctx, index) for index in (0, 1)]
    values = ["{\"row\":1}", "{\"row\":2}"]
    for index, attempt in enumerate(attempts):
        attempt.status = "completed"
        attempt.node_runs = {
            "agent": {"status": "completed", "output_text": values[index], "run_id": str(uuid.uuid4())},
            "missing-agent": {"status": "failed", "run_id": str(uuid.uuid4())},
        }
        agent_run = uuid.UUID(attempt.node_runs["agent"]["run_id"])
        calls_by_run[agent_run] = [
            SimpleNamespace(
                iteration=1,
                sequence=1,
                tool_call={
                    "server": "asaree-script",
                    "tool": "run_wired_script",
                    "arguments": {"script": "score"},
                    "result": {"row": index + 10},
                    "success": True,
                },
            ),
        ]
        if index == 1:
            calls_by_run[agent_run].append(SimpleNamespace(
                iteration=2,
                sequence=1,
                tool_call={
                    "server": "asaree-script",
                    "tool": "run_wired_script",
                    "arguments": {"script": "score"},
                    "result": None,
                    "success": False,
                    "error_type": "tool_reported",
                },
            ))
        assert await finalize_attempt_measurement(db, attempt.id) is True

    assert (parent.run_id, parent.workspace_id, parent.metric_values, parent.artifacts) == parent_before
    for index, attempt in enumerate(attempts):
        slot = ctx["rows"][index]
        document = attempt.attempt_result["measurement"]
        by_name = {item["metric_name"]: item for item in document["observations"]}
        assert document["replicate_id"] == str(slot.id)
        assert document["attempt_id"] == str(attempt.id)
        assert by_name["Agent output"]["value"] == values[index]
        assert by_name["Script output"]["value"] == ({"row": 10} if index == 0 else None)
        assert by_name["Script output"]["status"] == "measured"
        assert by_name["Script output"]["producer"]["evaluation"]["tool_call_success"] is (index == 0)
        if index == 1:
            assert by_name["Script output"]["producer"]["evaluation"]["tool_call_error_type"] == "tool_reported"
        assert by_name["Missing Agent"]["status"] == "unavailable"
        assert by_name["Missing Script"]["status"] == "unavailable"
        assert slot.artifacts["measurement"] == document
        assert slot.metric_values["Agent output"] == values[index]
        assert slot.metric_values["Script output"] == ({"row": 10} if index == 0 else None)
        assert slot.run_id == attempt.id
    first_subject = attempts[0].attempt_result["measurement"]["replicate_id"]
    second_subject = attempts[1].attempt_result["measurement"]["replicate_id"]
    assert first_subject != second_subject


@pytest.mark.asyncio
async def test_row_attempt_cancellation_failure_truncation_and_preview_projection(row_measurements, monkeypatch):
    db, ctx = row_measurements

    async def no_steps(_run_id):
        return []

    monkeypatch.setattr("asaree.services.reported_metrics.get_run_steps", no_steps)

    cancelled = await _create_attempt(db, ctx, 0)
    cancelled.status = "cancelled"
    cancelled.attempt_result["evaluation_state"] = "running"
    cancelled.node_runs = {"agent": {"status": "completed", "output_text": "cancelled output"}}
    assert await finalize_attempt_measurement(db, cancelled.id) is True
    cancelled_document = cancelled.attempt_result["measurement"]
    assert cancelled_document["observations"][0]["status"] == "measured"
    assert cancelled_document["observations"][3]["status"] == "unavailable"
    assert ctx["rows"][0].artifacts["measurement"] == cancelled_document
    assert ctx["rows"][0].metric_values is None

    failed = await _create_attempt(db, ctx, 1)
    failed.status = "failed"
    failed.error = "enqueue failed"
    assert await finalize_attempt_measurement(db, failed.id) is True
    assert ctx["rows"][1].artifacts["measurement"]["attempt_id"] == str(failed.id)
    assert ctx["rows"][1].metric_values is None

    truncated = await _create_attempt(db, ctx, 2)
    truncated.status = "completed"
    truncated.node_runs = {
        "agent": {"status": "completed", "output_text": "unfinished", "truncation": {"reason": "limit"}}
    }
    assert await finalize_attempt_measurement(db, truncated.id) is True
    assert truncated.attempt_result["measurement"]["observations"][0]["status"] == "measured"
    assert "metric_values" in truncated.attempt_result
    assert ctx["rows"][2].artifacts["measurement"]["attempt_id"] == str(truncated.id)
    assert ctx["rows"][2].metric_values is None

    preview = ProtocolRun(
        protocol_id=ctx["protocol"].id,
        owner_id=ctx["user_id"],
        status="completed",
        dataset_row=_row_snapshot(ctx, 0),
        attempt_result={"measurement_plan_snapshot": PLAN},
        node_runs={"agent": {"status": "completed", "output_text": "preview"}},
    )
    db.add(preview)
    await db.flush()
    assert await finalize_attempt_measurement(db, preview.id) is True
    assert preview.attempt_result["measurement"]["replicate_id"] == str(preview.id)
    assert all(row.run_id != preview.id for row in ctx["rows"])


@pytest.mark.asyncio
async def test_record_measurement_validates_subject_and_keeps_history_immutable(row_measurements):
    db, ctx = row_measurements
    attempt = await _create_attempt(db, ctx, 0)
    plan = parse_measurement_plan(PLAN)
    from asaree.services.reported_metrics import collect_reported_metrics

    attempt.node_runs = {"agent": {"status": "completed", "output_text": "recorded"}}
    evaluation = await collect_reported_metrics(attempt, plan, GRAPH)
    assert evaluation.replicate_id == str(ctx["rows"][0].id)
    assert await record_measurement_evaluation(db, attempt.id, evaluation) is not None
    frozen = dict(attempt.attempt_result)
    with pytest.raises(ValueError, match="immutable"):
        await record_measurement_evaluation(db, attempt.id, evaluation)
    assert attempt.attempt_result == frozen

    with pytest.raises(ValueError, match="attempt"):
        await record_measurement_evaluation(db, attempt.id, replace(evaluation, attempt_id=str(uuid.uuid4())))
    replacement = await _create_attempt(db, ctx, 1)
    wrong_subject = replace(
        evaluation,
        replicate_id=str(ctx["rows"][0].id),
        attempt_id=str(replacement.id),
        observations=tuple(replace(item, attempt_id=str(replacement.id)) for item in evaluation.observations),
    )
    with pytest.raises(ValueError, match="replicate"):
        await record_measurement_evaluation(db, replacement.id, wrong_subject)


@pytest.mark.asyncio
async def test_superseded_row_attempt_keeps_history_without_replacing_current_projection(row_measurements):
    db, ctx = row_measurements
    first = await _create_attempt(db, ctx, 0)
    first.node_runs = {"agent": {"status": "completed", "output_text": "older"}}
    evaluation = await collect_reported_metrics(first, parse_measurement_plan(PLAN), GRAPH)
    replacement = await _create_attempt(db, ctx, 0)

    await record_measurement_evaluation(db, first.id, evaluation)

    assert first.attempt_result["measurement"]["observations"][0]["value"] == "older"
    assert ctx["rows"][0].run_id == replacement.id
    assert ctx["rows"][0].metric_values is None
    assert ctx["rows"][0].artifacts is None


@pytest.mark.asyncio
async def test_whole_dataset_attempt_keeps_replicate_projection_compatibility(row_measurements, monkeypatch):
    db, ctx = row_measurements

    async def no_steps(_run_id):
        return []

    monkeypatch.setattr("asaree.services.reported_metrics.get_run_steps", no_steps)
    run = await create_protocol_run(
        db,
        protocol_id=ctx["protocol"].id,
        owner_id=ctx["user_id"],
        replicate_label="row-replicate",
        factor_values={},
        replicate_result_id=ctx["replicate"].id,
        design_revision_id=ctx["design_id"],
        protocol_revision_id=ctx["protocol_revision_id"],
    )
    run.status = "completed"
    run.node_runs = {"agent": {"status": "completed", "output_text": "whole dataset"}}

    assert await finalize_attempt_measurement(db, run.id) is True
    assert run.attempt_result["measurement"]["replicate_id"] == str(ctx["replicate"].id)
    assert ctx["replicate"].artifacts["measurement"]["attempt_id"] == str(run.id)
    assert ctx["replicate"].metric_values["Agent output"] == "whole dataset"
