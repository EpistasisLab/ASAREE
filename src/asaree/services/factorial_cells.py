"""Upserting and reading factorial cell results.

``upsert_cell`` merges into ``factor_values``/``metric_values``/``artifacts``
individually — a *dict update* into whatever's already stored, not a
replace of the column. That's what lets a cell's pre-scoring write (factors,
payload, SHA guards, all under ``artifacts``/``factor_values``) and its later
post-scoring write (metrics, importances) land on the same row without either
erasing the other, now that everything lives in three JSON columns instead of
many named ones — replacing the whole column on the second write would have
silently discarded the first write's keys.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_cell_result import FactorialCellResult

_SCALAR_FIELDS = frozenset({"run_id", "workspace_id"})
_MERGE_FIELDS = frozenset({"factor_values", "metric_values", "artifacts"})
_SETTABLE_FIELDS = _SCALAR_FIELDS | _MERGE_FIELDS


async def upsert_cell(
    db: AsyncSession, *, experiment_id: uuid.UUID, cell_label: str, fields: dict[str, Any]
) -> FactorialCellResult:
    unknown = set(fields) - _SETTABLE_FIELDS
    if unknown:
        raise ValueError(f"not settable on a cell result: {sorted(unknown)}")

    cell = await get_cell(db, experiment_id=experiment_id, cell_label=cell_label)
    if cell is None:
        cell = FactorialCellResult(experiment_id=experiment_id, cell_label=cell_label)
        db.add(cell)

    for key, value in fields.items():
        if key in _MERGE_FIELDS:
            merged = dict(getattr(cell, key) or {})
            merged.update(value or {})
            setattr(cell, key, merged)
        else:
            setattr(cell, key, value)

    await db.flush()
    await db.refresh(cell)
    return cell


async def get_cell(db: AsyncSession, *, experiment_id: uuid.UUID, cell_label: str) -> FactorialCellResult | None:
    return (
        await db.execute(
            select(FactorialCellResult).where(
                FactorialCellResult.experiment_id == experiment_id, FactorialCellResult.cell_label == cell_label
            )
        )
    ).scalar_one_or_none()


async def list_cells(db: AsyncSession, *, experiment_id: uuid.UUID) -> Sequence[FactorialCellResult]:
    return (
        (
            await db.execute(
                select(FactorialCellResult)
                .where(FactorialCellResult.experiment_id == experiment_id)
                .order_by(FactorialCellResult.cell_label)
            )
        )
        .scalars()
        .all()
    )
