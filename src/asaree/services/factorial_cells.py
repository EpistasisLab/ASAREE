"""Upserting and reading factorial cell results.

``upsert_cell`` merges into ``factor_values``/``metric_values``/``artifacts``
individually — a *dict update* into whatever's already stored, not a
replace of the column. That's what lets a cell's pre-scoring write (factors,
payload, SHA guards, all under ``artifacts``/``factor_values``) and its later
post-scoring write (metrics, importances) land on the same row without either
erasing the other, now that everything lives in three JSON columns instead of
many named ones — replacing the whole column on the second write would have
silently discarded the first write's keys.

Every function here is scoped to a single **design revision** — by default the
experiment's current one (see services.design_revisions). That default is the
whole point: a cell belongs to the design that generated it, and a query
filtered on ``experiment_id`` alone sees superseded designs' cells too. That
is precisely the bug revisions were introduced to fix — a design shrunk from 6
cells to 2 still reported "0/6 scored" and still ran 6 — so read cells through
here rather than querying ``FactorialCellResult`` directly. Pass
``revision_id`` explicitly only to look at history on purpose.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_cell_result import FactorialCellResult
from asaree.services.design_revisions import get_current_revision, get_or_create_current

_SCALAR_FIELDS = frozenset({"run_id", "workspace_id"})
_MERGE_FIELDS = frozenset({"factor_values", "metric_values", "artifacts"})
_SETTABLE_FIELDS = _SCALAR_FIELDS | _MERGE_FIELDS


async def _resolve_revision_id(
    db: AsyncSession, experiment_id: uuid.UUID, revision_id: uuid.UUID | None
) -> uuid.UUID | None:
    """The revision to read from: the caller's if given, else the current one.

    ``None`` back means the experiment has no design revision at all, i.e. no
    cells have ever been written — the readers below turn that into an empty
    result rather than creating a revision as a side effect of a read.
    """
    if revision_id is not None:
        return revision_id
    current = await get_current_revision(db, experiment_id)
    return current.id if current is not None else None


async def upsert_cell(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    cell_label: str,
    fields: dict[str, Any],
    revision_id: uuid.UUID | None = None,
) -> FactorialCellResult:
    """Create or merge into one cell of *experiment_id*'s current design.

    Unlike the readers, this creates the experiment's revision 1 on demand
    when it has none — a client that writes cells directly without ever
    calling generate-design (the SDK/notebook path) still needs somewhere to
    put them.
    """
    unknown = set(fields) - _SETTABLE_FIELDS
    if unknown:
        raise ValueError(f"not settable on a cell result: {sorted(unknown)}")

    if revision_id is None:
        revision_id = (await get_or_create_current(db, experiment_id)).id

    cell = await get_cell(db, experiment_id=experiment_id, cell_label=cell_label, revision_id=revision_id)
    if cell is None:
        cell = FactorialCellResult(
            experiment_id=experiment_id, design_revision_id=revision_id, cell_label=cell_label
        )
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


async def get_cell(
    db: AsyncSession, *, experiment_id: uuid.UUID, cell_label: str, revision_id: uuid.UUID | None = None
) -> FactorialCellResult | None:
    resolved = await _resolve_revision_id(db, experiment_id, revision_id)
    if resolved is None:
        return None
    return (
        await db.execute(
            select(FactorialCellResult).where(
                # Both, not just the revision: revision_id comes from the API's
                # own query string, and a revision belonging to someone else's
                # experiment must read as empty here, not as their cells.
                FactorialCellResult.experiment_id == experiment_id,
                FactorialCellResult.design_revision_id == resolved,
                FactorialCellResult.cell_label == cell_label,
            )
        )
    ).scalar_one_or_none()


async def list_cells(
    db: AsyncSession, *, experiment_id: uuid.UUID, revision_id: uuid.UUID | None = None
) -> Sequence[FactorialCellResult]:
    resolved = await _resolve_revision_id(db, experiment_id, revision_id)
    if resolved is None:
        return []
    return (
        (
            await db.execute(
                select(FactorialCellResult)
                # Scoped to both -- see get_cell's own note on why the
                # revision alone isn't enough of a filter.
                .where(FactorialCellResult.experiment_id == experiment_id)
                .where(FactorialCellResult.design_revision_id == resolved)
                .order_by(FactorialCellResult.cell_label)
            )
        )
        .scalars()
        .all()
    )
