"""PostgreSQL concurrency coverage for latest row-attempt projections."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.factorial_row_results import claim_row_attempt, project_row_attempt
from asaree.services.measurement_engine import (
    MeasurementEvaluation,
    MetricObservation,
    ProducerProvenance,
)
from asaree.services.protocol_runs import create_protocol_run, record_measurement_evaluation, set_status
from asaree.services.users import get_user_by_email


@pytest_asyncio.fixture
async def row_slot(tmp_path: Path) -> AsyncIterator[dict]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            user = await get_user_by_email(db, "test@test.com")
            assert user is not None, "run scripts/seed_row_test_users.py before row database tests"
            experiment_id, dataset_id, protocol_id = (uuid.uuid4() for _ in range(3))
            design_id, revision_id = uuid.uuid4(), uuid.uuid4()
            cell_id, replicate_id, row_id = (uuid.uuid4() for _ in range(3))
            raw = b"input,expected\nquestion,response\n"
            csv_path = tmp_path / "stale-writes.csv"
            csv_path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            workspace = tmp_path / "attempt-workspace"
            workspace.mkdir()
            (workspace / "trace.json").write_text('{"attempt":"A"}', encoding="utf-8")
            experiment = ResearchExperiment(
                id=experiment_id,
                name=f"stale-row-{experiment_id}",
                owner_id=user.id,
                design_spec={"factors": [], "metrics": []},
            )
            dataset = RegisteredDataset(
                id=dataset_id,
                name=f"stale-row-{dataset_id}",
                owner_id=user.id,
                raw_path=str(csv_path),
                raw_sha256=digest,
            )
            protocol = Protocol(
                id=protocol_id,
                name=f"stale-row-{protocol_id}",
                owner_id=user.id,
                experiment_id=experiment_id,
                graph={"nodes": [], "edges": []},
            )
            db.add_all([experiment, dataset, protocol])
            await db.flush()
            design = ExperimentDesignRevision(
                id=design_id,
                experiment_id=experiment_id,
                revision=1,
                design_spec={"factors": [], "metrics": []},
            )
            revision = ProtocolRevision(
                id=revision_id,
                protocol_id=protocol_id,
                revision=1,
                graph={"nodes": [], "edges": []},
                published_at=datetime.now(UTC),
            )
            cell = FactorialCell(
                id=cell_id,
                experiment_id=experiment_id,
                design_revision_id=design_id,
                cell_label="row-cell",
                factor_values={},
            )
            db.add_all([design, revision, cell])
            await db.flush()
            protocol.published_revision_id = revision_id
            replicate = FactorialReplicateResult(
                id=replicate_id,
                cell_id=cell_id,
                replicate_number=1,
                replicate_label="row-replicate",
                run_id=None,
                metric_values=None,
                artifacts=None,
            )
            row = FactorialRowResult(
                id=row_id,
                replicate_result_id=replicate_id,
                protocol_revision_id=revision_id,
                dataset_id=dataset_id,
                raw_sha256=digest,
                row_index=0,
                artifacts={"previous": True},
            )
            db.add_all([replicate, row])
            await db.flush()
            await db.commit()
            yield {
                "sessions": sessions,
                "user_id": user.id,
                "protocol_id": protocol_id,
                "design_id": design_id,
                "revision_id": revision_id,
                "replicate_id": replicate_id,
                "row_id": row_id,
                "dataset_id": dataset_id,
                "digest": digest,
                "row_snapshot": {
                    "dataset_id": str(dataset_id),
                    "raw_sha256": digest,
                    "row_index": 0,
                    "columns": ["input", "expected"],
                    "values": {"input": "question", "expected": "response"},
                },
                "workspace": str(workspace),
            }
    finally:
        await engine.dispose()


async def _create_run(db, ctx: dict, *, row_id: uuid.UUID) -> ProtocolRun:
    return await create_protocol_run(
        db,
        protocol_id=ctx["protocol_id"],
        owner_id=ctx["user_id"],
        replicate_label="row-replicate",
        factor_values={},
        replicate_result_id=ctx["replicate_id"],
        row_result_id=row_id,
        dataset_row=ctx["row_snapshot"],
        design_revision_id=ctx["design_id"],
        protocol_revision_id=ctx["revision_id"],
    )


@pytest.mark.asyncio
async def test_stale_row_projection_cannot_overwrite_replacement_attempt(row_slot):
    sessions = row_slot["sessions"]
    async with sessions.begin() as db:
        run_a = await _create_run(db, row_slot, row_id=row_slot["row_id"])
        run_a_id = run_a.id

    work_finished = asyncio.Event()
    allow_stale_write = asyncio.Event()

    async def stale_worker() -> None:
        # Represents A finishing its work, then pausing before projection.
        work_finished.set()
        await allow_stale_write.wait()
        async with sessions.begin() as stale_db:
            assert await project_row_attempt(
                stale_db,
                row_result_id=row_slot["row_id"],
                run_id=run_a_id,
                fields={
                    "workspace_id": "attempt-A",
                    "metric_values": {"score": 1},
                    "artifacts": {"output_text": "stale A", "late": True},
                },
            ) is False

    task = asyncio.create_task(stale_worker())
    await work_finished.wait()
    async with sessions.begin() as db:
        await set_status(db, run_a_id, status="failed", error="A failed")
        run_b = await claim_row_attempt(
            db,
            row_result_id=row_slot["row_id"],
            expected_run_id=run_a_id,
            create_kwargs={
                "protocol_id": row_slot["protocol_id"],
                "owner_id": row_slot["user_id"],
                "replicate_label": "row-replicate",
                "factor_values": {},
                "replicate_result_id": row_slot["replicate_id"],
                "design_revision_id": row_slot["design_id"],
                "protocol_revision_id": row_slot["revision_id"],
                "dataset_row": row_slot["row_snapshot"],
            },
        )
        assert run_b is not None
        run_b_id = run_b.id
        await set_status(db, run_b_id, status="completed")
        assert await project_row_attempt(
            db,
            row_result_id=row_slot["row_id"],
            run_id=run_b_id,
            fields={
                "workspace_id": "attempt-B",
                "metric_values": {"score": 2},
                "artifacts": {"output_text": "current B"},
            },
        ) is True

    allow_stale_write.set()
    await task
    async with sessions() as db:
        slot = await db.get(FactorialRowResult, row_slot["row_id"])
        old = await db.get(ProtocolRun, run_a_id)
        assert slot is not None and old is not None
        assert slot.run_id == run_b_id
        assert slot.workspace_id == "attempt-B"
        assert slot.metric_values == {"score": 2}
        assert slot.artifacts["output_text"] == "current B"
        assert old.error == "A failed"


@pytest.mark.asyncio
async def test_deleted_row_slot_late_projection_never_targets_parent(row_slot):
    sessions = row_slot["sessions"]
    async with sessions.begin() as db:
        run = await _create_run(db, row_slot, row_id=row_slot["row_id"])
        run_id = run.id
        parent_before = await db.get(FactorialReplicateResult, row_slot["replicate_id"])
        assert parent_before is not None
        before = (parent_before.workspace_id, parent_before.metric_values, parent_before.artifacts)
        await db.execute(delete(FactorialRowResult).where(FactorialRowResult.id == row_slot["row_id"]))
        assert await project_row_attempt(
            db,
            row_result_id=row_slot["row_id"],
            run_id=run_id,
            fields={"workspace_id": "late", "metric_values": {"score": 7}, "artifacts": {"late": True}},
        ) is False
        parent_after = await db.get(FactorialReplicateResult, row_slot["replicate_id"])
        assert parent_after is not None
        assert (parent_after.workspace_id, parent_after.metric_values, parent_after.artifacts) == before


@pytest.mark.asyncio
async def test_duplicate_row_finalization_preserves_measured_null_and_result(row_slot):
    sessions = row_slot["sessions"]
    async with sessions.begin() as db:
        run = await _create_run(db, row_slot, row_id=row_slot["row_id"])
        run_id = run.id
        run.status = "completed"
        provenance = ProducerProvenance(
            binding_id="report",
            producer_id="asaree.agent_output",
            kind="reported",
            version="1",
        )

        def evaluation(value: str | None) -> MeasurementEvaluation:
            return MeasurementEvaluation(
                replicate_id=str(row_slot["row_id"]),
                attempt_id=str(run_id),
                observations=(
                    MetricObservation(
                        metric_id="answer",
                        metric_name="Answer",
                        value_type=None,
                        status="measured",
                        value=value,
                        error=None,
                        attempt_id=str(run_id),
                        producer=provenance,
                        input_provenance={},
                    ),
                ),
                artifacts=(),
            )

        await record_measurement_evaluation(db, run_id, evaluation(None))
        persisted = await db.get(ProtocolRun, run_id)
        assert persisted is not None
        original = dict(persisted.attempt_result)
        with pytest.raises(ValueError, match="immutable_attempt_result"):
            await record_measurement_evaluation(db, run_id, evaluation("replacement"))
        persisted = await db.get(ProtocolRun, run_id)
        assert persisted is not None
        assert persisted.attempt_result == original
        with pytest.raises(ValueError, match="immutable_attempt_result"):
            from asaree.services.protocol_runs import update_attempt_result

            await update_attempt_result(db, run_id, fields={"metric_values": {"Answer": "replacement"}})
        slot = await db.get(FactorialRowResult, row_slot["row_id"])
        assert slot is not None
        assert slot.metric_values == {"Answer": None}
