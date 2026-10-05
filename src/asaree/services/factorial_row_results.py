"""Scoped persistence queries for stable dataset row slots and their attempts."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
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

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
    db.add(result)
    await db.flush()
    return result


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


__all__ = ["ensure_row_result", "get_row_result", "list_row_attempts", "list_row_results"]
