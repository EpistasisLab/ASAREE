"""Upserting and reading factorial cell results.

``upsert_cell`` merges rather than replaces: only the keys actually present in
*fields* are written. This is what lets a cell's pre-scoring write (factors,
payload, SHA guards) and its later post-scoring write (test_metrics,
importances) target the same row without the second call blanking out the
first — exactly the durability property the original notebook's two-phase
``runs.update`` calls relied on.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_cell_result import FactorialCellResult

_SETTABLE_FIELDS = frozenset(
    {
        "run_id",
        "workspace_id",
        "tier",
        "effort",
        "critic",
        "replicate",
        "primary_metric",
        "payload",
        "raw_payload",
        "payload_sanitize_notes",
        "process_metrics",
        "expected_payload_sha256",
        "model_script_sha256",
        "test_metrics",
        "permutation_importance_top15",
        "model_decisions",
        "package_versions",
        "test_class_distribution",
        "n_test",
        "code_sha256",
        "payload_sha256",
        "data_sha256",
    }
)


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
