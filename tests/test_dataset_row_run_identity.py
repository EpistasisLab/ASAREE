"""Database-backed identity tests for row-slot ProtocolRun attempts."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import asaree.api.protocols as protocol_api
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
from asaree.services.protocol_runs import create_protocol_run
from asaree.services.users import create_user, get_user_by_email, set_password


@pytest_asyncio.fixture
async def row_graph(tmp_path) -> AsyncIterator[tuple[AsyncSession, dict]]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    async with engine.begin() as connection:
        sessions = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with sessions() as db:
            user_id, experiment_id, dataset_id, protocol_id = (uuid.uuid4() for _ in range(4))
            design_id, protocol_revision_id = uuid.uuid4(), uuid.uuid4()
            cell_id, replicate_id = uuid.uuid4(), uuid.uuid4()
            raw = b"subject,value\na,1\nb,2\n"
            raw_path = tmp_path / "source.csv"
            raw_path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            source = read_row_source(dataset_id=str(dataset_id), raw_path=str(raw_path), raw_sha256=digest)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            (workspace / "trace.json").write_text('{"run":"fixture"}', encoding="utf-8")
            user = await get_user_by_email(db, "test@test.com")
            if user is None:
                user = await create_user(db, email="test@test.com", password="Test1234", display_name="Row test")
            else:
                await set_password(db, user, new_password="Test1234")
                user.display_name = "Row test"
                user.is_active = True
            user_id = user.id
            experiment = ResearchExperiment(id=experiment_id, name=f"row-{experiment_id}", owner_id=user_id)
            dataset = RegisteredDataset(
                id=dataset_id, name=f"rows-{dataset_id}", owner_id=user_id, raw_path=str(raw_path), raw_sha256=digest
            )
            protocol = Protocol(
                id=protocol_id, name=f"protocol-{protocol_id}", owner_id=user_id, experiment_id=experiment_id, graph={}
            )
            db.add_all([experiment, dataset, protocol])
            await db.flush()
            design = ExperimentDesignRevision(id=design_id, experiment_id=experiment_id, revision=1)
            revision = ProtocolRevision(
                id=protocol_revision_id, protocol_id=protocol_id, revision=1, graph={}, published_at=datetime.now(UTC)
            )
            db.add_all([design, revision])
            await db.flush()
            protocol.published_revision_id = protocol_revision_id
            cell = FactorialCell(
                id=cell_id,
                experiment_id=experiment_id,
                design_revision_id=design_id,
                cell_label="cell-1",
                factor_values={"condition": "same"},
            )
            db.add(cell)
            await db.flush()
            replicate = FactorialReplicateResult(
                id=replicate_id,
                cell_id=cell_id,
                replicate_number=1,
                replicate_label="replicate-1",
                run_id=uuid.uuid4(),
                workspace_id="parent-workspace",
                metric_values={"old": 1},
                artifacts={"old": True},
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
                    workspace_id=f"row-workspace-{index}",
                    metric_values={"old": index},
                    artifacts={"old": index},
                )
                for index in range(2)
            ]
            db.add_all(rows)
            await db.flush()
            context = {
                "owner_id": user_id,
                "experiment_id": experiment_id,
                "dataset_id": dataset_id,
                "protocol_id": protocol_id,
                "design_id": design_id,
                "protocol_revision_id": protocol_revision_id,
                "replicate_id": replicate_id,
                "replicate": replicate,
                "rows": rows,
                "digest": digest,
                "workspace": workspace,
                "source": source,
            }
            try:
                yield db, context
            finally:
                await db.rollback()
    await engine.dispose()


def _snapshot(ctx: dict, row_index: int) -> dict:
    return project_row(ctx["source"], row_index=row_index, columns=["subject", "value"])


async def _create(db: AsyncSession, ctx: dict, index: int) -> ProtocolRun:
    return await create_protocol_run(
        db,
        protocol_id=ctx["protocol_id"],
        owner_id=ctx["owner_id"],
        replicate_label="replicate-1",
        factor_values={"condition": "same"},
        replicate_result_id=ctx["replicate_id"],
        row_result_id=ctx["rows"][index].id,
        dataset_row=_snapshot(ctx, index),
        design_revision_id=ctx["design_id"],
        protocol_revision_id=ctx["protocol_revision_id"],
    )


async def test_row_slots_have_independent_attempt_identity_and_projection(row_graph) -> None:
    db, ctx = row_graph
    assert (ctx["workspace"] / "trace.json").is_file()
    parent_before = (
        ctx["replicate"].run_id,
        ctx["replicate"].workspace_id,
        ctx["replicate"].metric_values,
        ctx["replicate"].artifacts,
    )
    old_row_run_id = ctx["rows"][0].run_id
    first = await _create(db, ctx, 0)
    second = await _create(db, ctx, 1)
    assert first.id != second.id
    assert first.replicate_label == second.replicate_label == "replicate-1"
    assert first.row_result_id != second.row_result_id
    assert first.attempt_result["row_provenance"]["row_result_id"] == str(ctx["rows"][0].id)
    assert first.attempt_result["row_provenance"]["dataset_row"] == _snapshot(ctx, 0)
    assert ctx["rows"][0].run_id == first.id and ctx["rows"][1].run_id == second.id
    assert (
        ctx["rows"][0].workspace_id is None
        and ctx["rows"][0].metric_values is None
        and ctx["rows"][0].artifacts is None
    )
    assert parent_before == (
        ctx["replicate"].run_id,
        ctx["replicate"].workspace_id,
        ctx["replicate"].metric_values,
        ctx["replicate"].artifacts,
    )
    assert old_row_run_id != first.id
    serialized = protocol_api._protocol_run_response(first)
    assert serialized.row_result_id == first.row_result_id
    assert serialized.dataset_row == _snapshot(ctx, 0)

    old_attempt_snapshot = dict(first.attempt_result)
    replacement = await _create(db, ctx, 0)
    assert ctx["rows"][0].run_id == replacement.id
    assert first.attempt_result == old_attempt_snapshot
    assert await db.scalar(select(ProtocolRun.id).where(ProtocolRun.id == first.id)) == first.id
    assert parent_before == (
        ctx["replicate"].run_id,
        ctx["replicate"].workspace_id,
        ctx["replicate"].metric_values,
        ctx["replicate"].artifacts,
    )


async def test_mismatched_row_scope_snapshot_and_factors_rejected_before_run_insert(row_graph) -> None:
    db, ctx = row_graph
    for kwargs in (
        {"dataset_row": {**_snapshot(ctx, 0), "row_index": 1}},
        {"factor_values": {"condition": "other"}},
        {"design_revision_id": uuid.uuid4()},
        {"protocol_revision_id": uuid.uuid4()},
        {"replicate_result_id": uuid.uuid4()},
    ):
        args = {
            "protocol_id": ctx["protocol_id"],
            "owner_id": ctx["owner_id"],
            "replicate_label": "replicate-1",
            "factor_values": {"condition": "same"},
            "replicate_result_id": ctx["replicate_id"],
            "row_result_id": ctx["rows"][0].id,
            "dataset_row": _snapshot(ctx, 0),
            "design_revision_id": ctx["design_id"],
            "protocol_revision_id": ctx["protocol_revision_id"],
        }
        args.update(kwargs)
        with pytest.raises(ValueError):
            await create_protocol_run(db, **args)
        assert await db.scalar(select(ProtocolRun.id).where(ProtocolRun.row_result_id == ctx["rows"][0].id)) is None

    with pytest.raises(ValueError, match="invalid_dataset_row_snapshot"):
        await create_protocol_run(
            db,
            protocol_id=ctx["protocol_id"],
            owner_id=ctx["owner_id"],
            dataset_row={**_snapshot(ctx, 0), "extra": True},
        )


async def test_snapshot_only_and_legacy_runs_have_no_factorial_projection(row_graph) -> None:
    db, ctx = row_graph
    parent_before = (
        ctx["replicate"].run_id,
        ctx["replicate"].workspace_id,
        ctx["replicate"].metric_values,
        ctx["replicate"].artifacts,
    )
    preview = await create_protocol_run(
        db,
        protocol_id=ctx["protocol_id"],
        owner_id=ctx["owner_id"],
        target_node_id="node-preview",
        dataset_row=_snapshot(ctx, 0),
    )
    legacy = await create_protocol_run(db, protocol_id=ctx["protocol_id"], owner_id=ctx["owner_id"])
    assert preview.row_result_id is None and preview.replicate_result_id is None
    assert preview.attempt_result["row_provenance"]["row_result_id"] is None
    assert legacy.row_result_id is None and legacy.dataset_row is None and legacy.replicate_result_id is None
    assert parent_before == (
        ctx["replicate"].run_id,
        ctx["replicate"].workspace_id,
        ctx["replicate"].metric_values,
        ctx["replicate"].artifacts,
    )
