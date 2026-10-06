"""PostgreSQL tests for experiment and revision scoped row result queries."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

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
from asaree.services.factorial_row_results import (
    ensure_row_result,
    get_row_result,
    list_row_attempts,
    list_row_results,
)
from asaree.services.users import create_user, get_user_by_email, set_password


@pytest_asyncio.fixture
async def row_graph() -> AsyncIterator[tuple[AsyncSession, dict[str, uuid.UUID]]]:
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids = {
        key: uuid.uuid4()
        for key in (
            "user",
            "dataset",
            "experiment",
            "other_experiment",
            "design",
            "old_design",
            "other_design",
            "protocol",
            "other_protocol",
            "publication",
            "other_publication",
            "replicate_a",
            "replicate_b",
            "other_replicate",
            "slot_a",
            "slot_b",
            "other_slot",
            "historical_replicate",
            "historical_slot",
            "other_user",
            "foreign_dataset",
        )
    }
    async with sessions() as db:
        user = await get_user_by_email(db, "test@test.com")
        if user is None:
            user = await create_user(db, email="test@test.com", password="Test1234", display_name="Row test")
        else:
            await set_password(db, user, new_password="Test1234")
            user.display_name = "Row test"
            user.is_active = True
        ids["user"] = user.id
        db.add_all(
            [
                RegisteredDataset(id=ids["dataset"], name=f"rows-{ids['dataset']}", owner_id=ids["user"]),
                ResearchExperiment(id=ids["experiment"], name=f"exp-{ids['experiment']}", owner_id=ids["user"]),
                ResearchExperiment(
                    id=ids["other_experiment"], name=f"exp-{ids['other_experiment']}", owner_id=ids["user"]
                ),
            ]
        )
        other_user = await get_user_by_email(db, "other@test.com")
        if other_user is None:
            other_user = await create_user(
                db, email="other@test.com", password="Test1234", display_name="Other row test"
            )
        else:
            await set_password(db, other_user, new_password="Test1234")
            other_user.display_name = "Other row test"
            other_user.is_active = True
        ids["other_user"] = other_user.id
        db.add(
            RegisteredDataset(
                id=ids["foreign_dataset"], name=f"foreign-{ids['foreign_dataset']}", owner_id=ids["other_user"]
            )
        )
        await db.flush()
        db.add_all(
            [
                ExperimentDesignRevision(
                    id=ids["old_design"], experiment_id=ids["experiment"], revision=1, superseded_at=datetime.now(UTC)
                ),
                ExperimentDesignRevision(id=ids["design"], experiment_id=ids["experiment"], revision=2),
                ExperimentDesignRevision(id=ids["other_design"], experiment_id=ids["other_experiment"], revision=1),
                Protocol(
                    id=ids["protocol"],
                    name=f"protocol-{ids['protocol']}",
                    owner_id=ids["user"],
                    experiment_id=ids["experiment"],
                    graph={},
                ),
                Protocol(
                    id=ids["other_protocol"],
                    name=f"protocol-{ids['other_protocol']}",
                    owner_id=ids["user"],
                    experiment_id=ids["other_experiment"],
                    graph={},
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                ProtocolRevision(
                    id=ids["publication"],
                    protocol_id=ids["protocol"],
                    revision=1,
                    graph={},
                    published_at=datetime.now(UTC),
                ),
                ProtocolRevision(
                    id=ids["other_publication"],
                    protocol_id=ids["other_protocol"],
                    revision=1,
                    graph={},
                    published_at=datetime.now(UTC),
                ),
            ]
        )
        await db.flush()
        protocol = await db.get(Protocol, ids["protocol"])
        other_protocol = await db.get(Protocol, ids["other_protocol"])
        protocol.published_revision_id = ids["publication"]
        other_protocol.published_revision_id = ids["other_publication"]
        await db.flush()
        cells = [
            FactorialCell(
                id=uuid.uuid4(),
                experiment_id=ids["experiment"],
                design_revision_id=ids["design"],
                cell_label=label,
                factor_values={},
            )
            for label in ("b", "a")
        ]
        old_cell = FactorialCell(
            id=uuid.uuid4(),
            experiment_id=ids["experiment"],
            design_revision_id=ids["old_design"],
            cell_label="historic",
            factor_values={},
        )
        other_cell = FactorialCell(
            id=uuid.uuid4(),
            experiment_id=ids["other_experiment"],
            design_revision_id=ids["other_design"],
            cell_label="a",
            factor_values={},
        )
        db.add_all([*cells, old_cell, other_cell])
        await db.flush()
        reps = [
            FactorialReplicateResult(
                id=ids["replicate_a"], cell_id=cells[0].id, replicate_number=1, replicate_label="same-label"
            ),
            FactorialReplicateResult(
                id=ids["replicate_b"], cell_id=cells[1].id, replicate_number=1, replicate_label="same-label"
            ),
            FactorialReplicateResult(
                id=ids["other_replicate"], cell_id=other_cell.id, replicate_number=1, replicate_label="same-label"
            ),
            FactorialReplicateResult(
                id=ids["historical_replicate"], cell_id=old_cell.id, replicate_number=1, replicate_label="same-label"
            ),
        ]
        db.add_all(reps)
        await db.flush()
        slots = [
            FactorialRowResult(
                id=ids["slot_a"],
                replicate_result_id=ids["replicate_a"],
                protocol_revision_id=ids["publication"],
                dataset_id=ids["dataset"],
                raw_sha256="a" * 64,
                row_index=2,
            ),
            FactorialRowResult(
                id=ids["slot_b"],
                replicate_result_id=ids["replicate_b"],
                protocol_revision_id=ids["publication"],
                dataset_id=ids["dataset"],
                raw_sha256="a" * 64,
                row_index=0,
            ),
            FactorialRowResult(
                id=ids["other_slot"],
                replicate_result_id=ids["other_replicate"],
                protocol_revision_id=ids["other_publication"],
                dataset_id=ids["dataset"],
                raw_sha256="a" * 64,
                row_index=0,
            ),
            FactorialRowResult(
                id=ids["historical_slot"],
                replicate_result_id=ids["historical_replicate"],
                protocol_revision_id=ids["publication"],
                dataset_id=ids["dataset"],
                raw_sha256="a" * 64,
                row_index=0,
            ),
        ]
        db.add_all(slots)
        await db.flush()
        yield db, ids
        await db.rollback()
    await engine.dispose()


async def test_scoped_lookup_order_and_forged_mixed_scopes(row_graph) -> None:
    db, ids = row_graph
    rows = await list_row_results(db, experiment_id=ids["experiment"], protocol_revision_id=ids["publication"])
    assert [row.id for row in rows] == [ids["slot_b"], ids["slot_a"]]
    assert (
        await get_row_result(
            db,
            experiment_id=ids["experiment"],
            row_result_id=ids["other_slot"],
            protocol_revision_id=ids["other_publication"],
        )
        is None
    )
    assert (
        await list_row_results(
            db,
            experiment_id=ids["experiment"],
            design_revision_id=ids["other_design"],
            protocol_revision_id=ids["publication"],
        )
        == []
    )
    assert (
        await list_row_results(db, experiment_id=ids["experiment"], protocol_revision_id=ids["other_publication"]) == []
    )
    assert (
        await get_row_result(
            db,
            experiment_id=ids["experiment"],
            row_result_id=ids["slot_a"],
            design_revision_id=ids["old_design"],
            protocol_revision_id=ids["publication"],
        )
        is None
    )
    historical = await list_row_results(
        db,
        experiment_id=ids["experiment"],
        design_revision_id=ids["old_design"],
        protocol_revision_id=ids["publication"],
    )
    assert [row.id for row in historical] == [ids["historical_slot"]]


async def test_history_is_grouped_by_slot_identity_not_replicate_label(row_graph) -> None:
    db, ids = row_graph
    earlier = datetime(2025, 1, 1, tzinfo=UTC)
    later = datetime(2025, 1, 2, tzinfo=UTC)
    first_attempt = ProtocolRun(
        id=uuid.UUID(int=10),
        protocol_id=ids["protocol"],
        owner_id=ids["user"],
        node_runs={},
        row_result_id=ids["slot_a"],
        protocol_revision_id=ids["publication"],
        created_at=later,
    )
    second_attempt = ProtocolRun(
        id=uuid.UUID(int=11),
        protocol_id=ids["protocol"],
        owner_id=ids["user"],
        node_runs={},
        row_result_id=ids["slot_a"],
        protocol_revision_id=ids["publication"],
        created_at=earlier,
    )
    other_slot_attempt = ProtocolRun(
        protocol_id=ids["protocol"],
        owner_id=ids["user"],
        node_runs={},
        row_result_id=ids["slot_b"],
        protocol_revision_id=ids["publication"],
        created_at=earlier,
    )
    db.add_all([first_attempt, second_attempt, other_slot_attempt])
    await db.flush()
    attempts = await list_row_attempts(
        db, experiment_id=ids["experiment"], row_result_id=ids["slot_a"], protocol_revision_id=ids["publication"]
    )
    assert [attempt.id for attempt in attempts] == [second_attempt.id, first_attempt.id]
    assert all(attempt.row_result_id == ids["slot_a"] for attempt in attempts)
    other_attempts = await list_row_attempts(
        db,
        experiment_id=ids["experiment"],
        row_result_id=ids["slot_b"],
        protocol_revision_id=ids["publication"],
    )
    assert [attempt.id for attempt in other_attempts] == [other_slot_attempt.id]


async def test_ensure_is_idempotent_and_checks_parent_dataset_and_publication(row_graph) -> None:
    db, ids = row_graph
    kwargs = dict(
        experiment_id=ids["experiment"],
        design_revision_id=ids["design"],
        protocol_revision_id=ids["publication"],
        replicate_result_id=ids["replicate_a"],
        dataset_id=ids["dataset"],
        raw_sha256="b" * 64,
        row_index=4,
    )
    first = await ensure_row_result(db, **kwargs)
    second = await ensure_row_result(db, **kwargs)
    assert first.id == second.id
    invalid_scopes = [
        {**kwargs, "design_revision_id": ids["old_design"]},
        {**kwargs, "replicate_result_id": ids["other_replicate"]},
        {**kwargs, "protocol_revision_id": ids["other_publication"]},
        {**kwargs, "protocol_revision_id": uuid.uuid4()},
        {**kwargs, "dataset_id": ids["foreign_dataset"]},
    ]
    for bad in invalid_scopes:
        with pytest.raises(ValueError, match="invalid_row_scope"):
            await ensure_row_result(db, **bad)
    for bad_value in ("A" * 64, "bad", "a" * 63):
        with pytest.raises(ValueError, match="invalid_row_scope"):
            await ensure_row_result(db, **{**kwargs, "raw_sha256": bad_value})
    for bad_value in (-1, True, 1.5):
        with pytest.raises(ValueError, match="invalid_row_scope"):
            await ensure_row_result(db, **{**kwargs, "row_index": bad_value})
