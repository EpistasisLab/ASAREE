"""Scoped persistence queries for stable dataset row slots and their attempts."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services.design_revisions import get_current_revision
from asaree.services.protocol_runs import TERMINAL_PROTOCOL_RUN_STATUSES

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROW_PROJECTION_FIELDS = frozenset({"workspace_id", "metric_values", "artifacts"})


async def project_row_attempt(
    db: AsyncSession, *, row_result_id: uuid.UUID, run_id: uuid.UUID, fields: dict
) -> bool:
    """Atomically update a row slot only while ``run_id`` still owns it.

    The update is scoped through the owning cell and both pinned revisions.
    The caller cannot use an arbitrary row UUID to project into another
    experiment, and an earlier current-attempt read is never authorization.
    """
    if not isinstance(fields, dict) or not fields or fields.keys() - _ROW_PROJECTION_FIELDS:
        raise ValueError("invalid_row_projection_fields")
    if not isinstance(row_result_id, uuid.UUID) or not isinstance(run_id, uuid.UUID):
        raise ValueError("invalid_row_scope")

    valid_scope = exists(
        select(FactorialRowResult.id)
        .join(
            FactorialReplicateResult,
            FactorialReplicateResult.id == FactorialRowResult.replicate_result_id,
        )
        .join(FactorialCell, FactorialCell.id == FactorialReplicateResult.cell_id)
        .join(ProtocolRevision, ProtocolRevision.id == FactorialRowResult.protocol_revision_id)
        .join(Protocol, Protocol.id == ProtocolRevision.protocol_id)
        .join(ProtocolRun, ProtocolRun.id == run_id)
        .where(
            FactorialRowResult.id == row_result_id,
            FactorialRowResult.run_id == run_id,
            ProtocolRun.row_result_id == FactorialRowResult.id,
            ProtocolRun.replicate_result_id == FactorialReplicateResult.id,
            ProtocolRun.design_revision_id == FactorialCell.design_revision_id,
            ProtocolRun.protocol_revision_id == FactorialRowResult.protocol_revision_id,
            ProtocolRun.protocol_id == Protocol.id,
            Protocol.experiment_id == FactorialCell.experiment_id,
        )
    )
    result = await db.execute(
        update(FactorialRowResult)
        .where(FactorialRowResult.id == row_result_id, valid_scope)
        .values(**fields)
        .execution_options(synchronize_session="fetch")
    )
    return result.rowcount == 1


async def _revision_id(
    db: AsyncSession, experiment_id: uuid.UUID, design_revision_id: uuid.UUID | None
) -> uuid.UUID | None:
    if design_revision_id is None:
        current = await get_current_revision(db, experiment_id)
        return current.id if current is not None else None
    valid = await db.scalar(
        select(ExperimentDesignRevision.id).where(
            ExperimentDesignRevision.id == design_revision_id,
            ExperimentDesignRevision.experiment_id == experiment_id,
        )
    )
    return valid


def _slot_scope(experiment_id: uuid.UUID, revision_id: uuid.UUID, protocol_revision_id: uuid.UUID):
    return (
        FactorialRowResult.replicate_result_id == FactorialReplicateResult.id,
        FactorialReplicateResult.cell_id == FactorialCell.id,
        ProtocolRevision.id == FactorialRowResult.protocol_revision_id,
        Protocol.experiment_id == experiment_id,
        FactorialCell.experiment_id == experiment_id,
        FactorialCell.design_revision_id == revision_id,
        FactorialRowResult.protocol_revision_id == protocol_revision_id,
    )


async def get_row_result(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    row_result_id: uuid.UUID,
    design_revision_id: uuid.UUID | None = None,
    protocol_revision_id: uuid.UUID,
) -> FactorialRowResult | None:
    revision_id = await _revision_id(db, experiment_id, design_revision_id)
    if revision_id is None:
        return None
    return (
        await db.execute(
            select(FactorialRowResult)
            .join(FactorialReplicateResult, FactorialRowResult.replicate_result_id == FactorialReplicateResult.id)
            .join(FactorialCell, FactorialReplicateResult.cell_id == FactorialCell.id)
            .join(ProtocolRevision, ProtocolRevision.id == FactorialRowResult.protocol_revision_id)
            .join(Protocol, Protocol.id == ProtocolRevision.protocol_id)
            .where(
                *_slot_scope(experiment_id, revision_id, protocol_revision_id),
                FactorialRowResult.id == row_result_id,
            )
        )
    ).scalar_one_or_none()


async def list_row_results(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    design_revision_id: uuid.UUID | None = None,
    protocol_revision_id: uuid.UUID,
) -> list[FactorialRowResult]:
    revision_id = await _revision_id(db, experiment_id, design_revision_id)
    if revision_id is None:
        return []
    return list(
        (
            await db.execute(
                select(FactorialRowResult)
                .join(FactorialReplicateResult, FactorialRowResult.replicate_result_id == FactorialReplicateResult.id)
                .join(FactorialCell, FactorialReplicateResult.cell_id == FactorialCell.id)
                .join(ProtocolRevision, ProtocolRevision.id == FactorialRowResult.protocol_revision_id)
                .join(Protocol, Protocol.id == ProtocolRevision.protocol_id)
                .where(*_slot_scope(experiment_id, revision_id, protocol_revision_id))
                .order_by(
                    FactorialCell.cell_label,
                    FactorialReplicateResult.replicate_number,
                    FactorialRowResult.row_index,
                    FactorialRowResult.id,
                )
            )
        )
        .scalars()
        .all()
    )


async def ensure_row_result(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    design_revision_id: uuid.UUID,
    protocol_revision_id: uuid.UUID,
    replicate_result_id: uuid.UUID,
    dataset_id: uuid.UUID,
    raw_sha256: str,
    row_index: int,
) -> FactorialRowResult:
    def invalid() -> ValueError:
        return ValueError("invalid_row_scope")

    if (
        not all(
            isinstance(value, uuid.UUID)
            for value in (experiment_id, design_revision_id, protocol_revision_id, replicate_result_id, dataset_id)
        )
        or not isinstance(raw_sha256, str)
        or _SHA256.fullmatch(raw_sha256) is None
        or isinstance(row_index, bool)
        or not isinstance(row_index, int)
        or row_index < 0
    ):
        raise invalid()

    current = await get_current_revision(db, experiment_id)
    parent = await db.scalar(
        select(FactorialReplicateResult.id)
        .join(FactorialCell, FactorialReplicateResult.cell_id == FactorialCell.id)
        .where(
            FactorialReplicateResult.id == replicate_result_id,
            FactorialCell.experiment_id == experiment_id,
            FactorialCell.design_revision_id == design_revision_id,
        )
    )
    published = await db.scalar(
        select(ProtocolRevision.id)
        .join(Protocol, ProtocolRevision.protocol_id == Protocol.id)
        .where(
            ProtocolRevision.id == protocol_revision_id,
            Protocol.experiment_id == experiment_id,
            Protocol.published_revision_id == protocol_revision_id,
        )
    )
    owned_dataset = await db.scalar(
        select(RegisteredDataset.id)
        .join(ResearchExperiment, ResearchExperiment.id == experiment_id)
        .where(RegisteredDataset.id == dataset_id, RegisteredDataset.owner_id == ResearchExperiment.owner_id)
    )
    if (
        current is None
        or current.id != design_revision_id
        or parent is None
        or published is None
        or owned_dataset is None
    ):
        raise invalid()

    existing = (
        await db.execute(
            select(FactorialRowResult).where(
                FactorialRowResult.replicate_result_id == replicate_result_id,
                FactorialRowResult.protocol_revision_id == protocol_revision_id,
                FactorialRowResult.dataset_id == dataset_id,
                FactorialRowResult.raw_sha256 == raw_sha256,
                FactorialRowResult.row_index == row_index,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    result = FactorialRowResult(
        replicate_result_id=replicate_result_id,
        protocol_revision_id=protocol_revision_id,
        dataset_id=dataset_id,
        raw_sha256=raw_sha256,
        row_index=row_index,
    )
    # A concurrent planner can allocate the same identity after our lookup.
    # Isolate the insert in a savepoint so a uniqueness race does not poison
    # unrelated work in the caller's transaction.
    try:
        async with db.begin_nested():
            db.add(result)
            await db.flush()
        return result
    except IntegrityError:
        existing = (
            await db.execute(
                select(FactorialRowResult).where(
                    FactorialRowResult.replicate_result_id == replicate_result_id,
                    FactorialRowResult.protocol_revision_id == protocol_revision_id,
                    FactorialRowResult.dataset_id == dataset_id,
                    FactorialRowResult.raw_sha256 == raw_sha256,
                    FactorialRowResult.row_index == row_index,
                )
            )
        ).scalar_one()
        return existing


async def claim_row_attempt(
    db: AsyncSession,
    *,
    row_result_id: uuid.UUID,
    expected_run_id: uuid.UUID | None,
    create_kwargs: dict,
) -> ProtocolRun | None:
    """Atomically claim the latest-attempt slot for a row execution."""
    slot = await db.scalar(
        select(FactorialRowResult)
        .where(FactorialRowResult.id == row_result_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if slot is None or slot.run_id != expected_run_id:
        return None
    if expected_run_id is not None:
        previous = await db.get(ProtocolRun, expected_run_id, populate_existing=True)
        if previous is None or previous.status not in TERMINAL_PROTOCOL_RUN_STATUSES:
            return None
        if previous.status not in {"failed", "cancelled"}:
            return None
    from asaree.services.protocol_runs import create_protocol_run

    # The locked slot defines the identity, even when callers include it in kwargs.
    run = await create_protocol_run(db, **{**create_kwargs, "row_result_id": row_result_id})
    # PostgreSQL's ``now()`` is fixed at transaction start. A retry commonly
    # happens in the same transaction as its failed predecessor, so give it
    # its actual creation time and retain the documented created_at/id history
    # ordering instead of letting a random UUID decide which attempt is latest.
    run.created_at = datetime.now(UTC)
    await db.flush()
    return run


async def list_row_attempts(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    row_result_id: uuid.UUID,
    design_revision_id: uuid.UUID | None = None,
    protocol_revision_id: uuid.UUID,
) -> list[ProtocolRun]:
    revision_id = await _revision_id(db, experiment_id, design_revision_id)
    if revision_id is None:
        return []
    return list(
        (
            await db.execute(
                select(ProtocolRun)
                .join(FactorialRowResult, ProtocolRun.row_result_id == FactorialRowResult.id)
                .join(FactorialReplicateResult, FactorialRowResult.replicate_result_id == FactorialReplicateResult.id)
                .join(FactorialCell, FactorialReplicateResult.cell_id == FactorialCell.id)
                .join(ProtocolRevision, ProtocolRevision.id == FactorialRowResult.protocol_revision_id)
                .join(Protocol, Protocol.id == ProtocolRevision.protocol_id)
                .where(
                    *_slot_scope(experiment_id, revision_id, protocol_revision_id),
                    ProtocolRun.row_result_id == row_result_id,
                    ProtocolRun.protocol_revision_id == protocol_revision_id,
                )
                .order_by(ProtocolRun.created_at, ProtocolRun.id)
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "claim_row_attempt",
    "ensure_row_result",
    "get_row_result",
    "list_row_attempts",
    "list_row_results",
    "project_row_attempt",
]
