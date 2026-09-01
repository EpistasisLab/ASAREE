"""Persistence for factorial cells and their replicate results.

A ``FactorialCell`` owns one factor combination, while each
``FactorialReplicateResult`` is one independently runnable observation.
All result-oriented operations use replicate terminology; cell-oriented
operations return the parent factor combination.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.services.design_revisions import get_current_revision, get_or_create_current

_REPLICATE_SUFFIX = re.compile(r"__rep(?P<number>\d+)$")
_NON_FACTOR_KEYS = frozenset({"replicate", "seed", "rep", "trial", "iteration"})
_SCALAR_FIELDS = frozenset({"run_id", "workspace_id"})
_MERGE_FIELDS = frozenset({"factor_values", "metric_values", "artifacts"})
_SETTABLE_FIELDS = _SCALAR_FIELDS | _MERGE_FIELDS


def split_replicate_label(replicate_label: str) -> tuple[str, int]:
    """Return the owning cell label and 1-based replicate number."""
    match = _REPLICATE_SUFFIX.search(replicate_label)
    if match is None:
        return replicate_label, 1
    return replicate_label[: match.start()], int(match.group("number"))


async def _resolve_revision_id(
    db: AsyncSession, experiment_id: uuid.UUID, revision_id: uuid.UUID | None
) -> uuid.UUID | None:
    if revision_id is not None:
        return revision_id
    current = await get_current_revision(db, experiment_id)
    return current.id if current is not None else None


async def get_factorial_cell(
    db: AsyncSession, *, experiment_id: uuid.UUID, cell_label: str, revision_id: uuid.UUID | None = None
) -> FactorialCell | None:
    resolved = await _resolve_revision_id(db, experiment_id, revision_id)
    if resolved is None:
        return None
    return (
        await db.execute(
            select(FactorialCell)
            .options(selectinload(FactorialCell.replicates))
            .where(
                FactorialCell.experiment_id == experiment_id,
                FactorialCell.design_revision_id == resolved,
                FactorialCell.cell_label == cell_label,
            )
        )
    ).scalar_one_or_none()


async def list_factorial_cells(
    db: AsyncSession, *, experiment_id: uuid.UUID, revision_id: uuid.UUID | None = None
) -> Sequence[FactorialCell]:
    resolved = await _resolve_revision_id(db, experiment_id, revision_id)
    if resolved is None:
        return []
    return (
        (
            await db.execute(
                select(FactorialCell)
                .options(selectinload(FactorialCell.replicates))
                .where(
                    FactorialCell.experiment_id == experiment_id,
                    FactorialCell.design_revision_id == resolved,
                )
                .order_by(FactorialCell.cell_label)
            )
        )
        .scalars()
        .all()
    )


async def get_replicate(
    db: AsyncSession, *, experiment_id: uuid.UUID, replicate_label: str, revision_id: uuid.UUID | None = None
) -> FactorialReplicateResult | None:
    resolved = await _resolve_revision_id(db, experiment_id, revision_id)
    if resolved is None:
        return None
    return (
        await db.execute(
            select(FactorialReplicateResult)
            .join(FactorialReplicateResult.cell)
            .options(selectinload(FactorialReplicateResult.cell))
            .where(
                FactorialCell.experiment_id == experiment_id,
                FactorialCell.design_revision_id == resolved,
                FactorialReplicateResult.replicate_label == replicate_label,
            )
        )
    ).scalar_one_or_none()


async def list_replicates(
    db: AsyncSession, *, experiment_id: uuid.UUID, revision_id: uuid.UUID | None = None
) -> Sequence[FactorialReplicateResult]:
    resolved = await _resolve_revision_id(db, experiment_id, revision_id)
    if resolved is None:
        return []
    return (
        (
            await db.execute(
                select(FactorialReplicateResult)
                .join(FactorialReplicateResult.cell)
                .options(selectinload(FactorialReplicateResult.cell))
                .where(
                    FactorialCell.experiment_id == experiment_id,
                    FactorialCell.design_revision_id == resolved,
                )
                .order_by(FactorialCell.cell_label, FactorialReplicateResult.replicate_number)
            )
        )
        .scalars()
        .all()
    )


async def upsert_replicate(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    replicate_label: str,
    fields: dict[str, Any],
    revision_id: uuid.UUID | None = None,
) -> FactorialReplicateResult:
    unknown = set(fields) - _SETTABLE_FIELDS
    if unknown:
        raise ValueError(f"not settable on a replicate result: {sorted(unknown)}")
    if revision_id is None:
        revision_id = (await get_or_create_current(db, experiment_id)).id

    cell_label, replicate_number = split_replicate_label(replicate_label)
    supplied_factors = {
        key: value
        for key, value in (fields.get("factor_values") or {}).items()
        if key.lower() not in _NON_FACTOR_KEYS
    }
    cell = None
    if supplied_factors:
        cell = (
            await db.execute(
                select(FactorialCell)
                .options(selectinload(FactorialCell.replicates))
                .where(
                    FactorialCell.experiment_id == experiment_id,
                    FactorialCell.design_revision_id == revision_id,
                    FactorialCell.factor_values == supplied_factors,
                )
            )
        ).scalar_one_or_none()
    if cell is None:
        cell = await get_factorial_cell(
            db, experiment_id=experiment_id, cell_label=cell_label, revision_id=revision_id
        )
    if cell is None:
        cell = FactorialCell(
            experiment_id=experiment_id,
            design_revision_id=revision_id,
            cell_label=cell_label,
        )
        db.add(cell)
        await db.flush()

    if "factor_values" in fields:
        merged_factors = dict(cell.factor_values or {})
        merged_factors.update(supplied_factors)
        cell.factor_values = merged_factors

    replicate = await get_replicate(
        db, experiment_id=experiment_id, replicate_label=replicate_label, revision_id=revision_id
    )
    if replicate is None:
        replicate = FactorialReplicateResult(
            cell=cell,
            replicate_number=replicate_number,
            replicate_label=replicate_label,
        )
        db.add(replicate)

    for key, value in fields.items():
        if key == "factor_values":
            continue
        if key in {"metric_values", "artifacts"}:
            merged = dict(getattr(replicate, key) or {})
            merged.update(value or {})
            setattr(replicate, key, merged)
        else:
            setattr(replicate, key, value)

    await db.flush()
    return replicate


__all__ = [
    "get_factorial_cell",
    "get_replicate",
    "list_factorial_cells",
    "list_replicates",
    "split_replicate_label",
    "upsert_replicate",
]
