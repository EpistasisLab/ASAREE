"""Persistence for factorial cells and their replicate results.

A ``FactorialCell`` owns one factor combination, while each
``FactorialReplicateResult`` is one independently runnable observation.
All result-oriented operations use replicate terminology; cell-oriented
operations return the parent factor combination.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Float, and_, case, cast, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

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
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    revision_id: uuid.UUID | None = None,
    replicate_ids: set[uuid.UUID] | None = None,
    include_evidence: bool = True,
) -> Sequence[FactorialReplicateResult]:
    resolved = await _resolve_revision_id(db, experiment_id, revision_id)
    if resolved is None:
        return []
    query = (
        select(FactorialReplicateResult)
        .join(FactorialReplicateResult.cell)
        .options(
            selectinload(FactorialReplicateResult.cell),
        )
        .where(FactorialCell.experiment_id == experiment_id, FactorialCell.design_revision_id == resolved)
    )
    if replicate_ids is not None:
        query = query.where(FactorialReplicateResult.id.in_(replicate_ids))
    if not include_evidence:
        query = query.options(
            load_only(
                FactorialReplicateResult.id,
                FactorialReplicateResult.cell_id,
                FactorialReplicateResult.replicate_label,
                FactorialReplicateResult.replicate_number,
                FactorialReplicateResult.run_id,
            )
        )
    return (
        (await db.execute(query.order_by(FactorialCell.cell_label, FactorialReplicateResult.replicate_number)))
        .scalars()
        .all()
    )


async def summarize_cells(
    db: AsyncSession, *, experiment_id: uuid.UUID, revision_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """One row per cell with replicate counts and per-metric sum/count.

    Aggregated in SQL so the response grows with the number of factor
    combinations, not replicates. Sum/count (not a mean) lets callers pool
    several cells into one replicate-weighted mean, e.g. a heatmap square.
    Booleans count as 0/1, matching how replicate metrics are averaged.
    """
    resolved = await _resolve_revision_id(db, experiment_id, revision_id)
    if resolved is None:
        return []
    cell, replicate = FactorialCell, FactorialReplicateResult
    in_revision = (cell.experiment_id == experiment_id, cell.design_revision_id == resolved)
    scored = and_(replicate.metric_values.is_not(None), func.jsonb_typeof(replicate.metric_values) != "null")
    counts = (
        await db.execute(
            select(
                cell.id,
                cell.cell_label,
                cell.factor_values,
                func.count(replicate.id).label("replicate_count"),
                func.count(replicate.id).filter(scored).label("scored_count"),
                func.max(replicate.updated_at).label("updated_at"),
            )
            .join(replicate, replicate.cell_id == cell.id)
            .where(*in_revision)
            .group_by(cell.id)
            .order_by(cell.cell_label)
        )
    ).all()
    entry = func.jsonb_each(replicate.metric_values).table_valued("key", "value").render_derived("kv")
    value_type = func.jsonb_typeof(entry.c.value)
    numeric = case(
        (value_type == "boolean", case((entry.c.value == cast(literal("true"), JSONB), 1.0), else_=0.0)),
        else_=cast(entry.c.value, Float),
    )
    metrics = (
        await db.execute(
            select(replicate.cell_id, entry.c.key, func.sum(numeric), func.count())
            .select_from(replicate)
            .join(cell, replicate.cell_id == cell.id)
            .join(entry, literal(True))
            .where(*in_revision, func.jsonb_typeof(replicate.metric_values) == "object")
            .where(value_type.in_(("number", "boolean")))
            .group_by(replicate.cell_id, entry.c.key)
        )
    ).all()
    by_cell: dict[uuid.UUID, dict[str, dict[str, float]]] = {}
    for cell_id, key, total, count in metrics:
        by_cell.setdefault(cell_id, {})[key] = {"sum": total, "count": count}
    return [
        {
            "cell_id": row.id,
            "cell_label": row.cell_label,
            "factor_values": row.factor_values or {},
            "replicate_count": row.replicate_count,
            "scored_count": row.scored_count,
            "updated_at": row.updated_at,
            "metrics": by_cell.get(row.id, {}),
        }
        for row in counts
    ]


def _factor_key(factor_values: dict[str, Any]) -> str:
    return json.dumps(factor_values, sort_keys=True, default=str)


async def materialize_replicates(
    db: AsyncSession,
    *,
    experiment_id: uuid.UUID,
    revision_id: uuid.UUID,
    planned: Sequence[tuple[str, dict[str, Any]]],
) -> list[FactorialReplicateResult]:
    """:func:`upsert_replicate` for a whole design at once, in planned order.

    Same rules per ``(replicate_label, fields)`` pair -- the cell is found by its
    factor values, then by label, else created; factor values merge into it;
    metric values and artifacts merge into the replicate; a published revision
    accepts neither new cells, new replicates nor changed factors -- but the
    revision's cells and replicates are read once and every new row is written
    in one flush. Calling upsert_replicate per row made design generation
    quadratic: each call reloaded every replicate already in its cell, so a
    15,786-replicate design took minutes.
    """
    from asaree.models.protocol_revision import ProtocolRevision

    for _, fields in planned:
        unknown = set(fields) - _SETTABLE_FIELDS
        if unknown:
            raise ValueError(f"not settable on a replicate result: {sorted(unknown)}")
    published = (
        await db.scalar(select(ProtocolRevision.id).where(ProtocolRevision.design_revision_id == revision_id).limit(1))
        is not None
    )
    cells = (
        await db.execute(
            select(FactorialCell).where(
                FactorialCell.experiment_id == experiment_id, FactorialCell.design_revision_id == revision_id
            )
        )
    ).scalars()
    by_label = {cell.cell_label: cell for cell in cells}
    by_factors = {_factor_key(cell.factor_values): cell for cell in by_label.values() if cell.factor_values}
    replicates = {
        replicate.replicate_label: replicate
        for replicate in (
            await db.execute(
                select(FactorialReplicateResult)
                .join(FactorialReplicateResult.cell)
                .where(FactorialCell.experiment_id == experiment_id, FactorialCell.design_revision_id == revision_id)
            )
        ).scalars()
    }
    new_cells: set[uuid.UUID] = set()
    for replicate_label, fields in planned:
        cell_label, replicate_number = split_replicate_label(replicate_label)
        supplied = {
            key: value
            for key, value in (fields.get("factor_values") or {}).items()
            if key.lower() not in _NON_FACTOR_KEYS
        }
        cell = (by_factors.get(_factor_key(supplied)) if supplied else None) or by_label.get(cell_label)
        if cell is None:
            if published:
                raise ValueError("Generate a draft design before adding cells to a published experiment version.")
            # Client-side id: new replicates reference it before the one flush.
            cell = FactorialCell(
                id=uuid.uuid4(), experiment_id=experiment_id, design_revision_id=revision_id, cell_label=cell_label
            )
            db.add(cell)
            new_cells.add(cell.id)
            by_label[cell_label] = cell
        if "factor_values" in fields:
            merged = {**(cell.factor_values or {}), **supplied}
            if merged != (cell.factor_values or {}):
                if published and cell.id not in new_cells:
                    raise ValueError("Published experiment cell factors are immutable. Generate a draft design first.")
                cell.factor_values = merged
                by_factors[_factor_key(merged)] = cell
        replicate = replicates.get(replicate_label)
        if replicate is None:
            if published:
                raise ValueError("Generate a draft design before adding replicates to a published experiment version.")
            replicate = FactorialReplicateResult(
                cell_id=cell.id, replicate_number=replicate_number, replicate_label=replicate_label
            )
            db.add(replicate)
            replicates[replicate_label] = replicate
        for key, value in fields.items():
            if key == "factor_values":
                continue
            if key in {"metric_values", "artifacts"}:
                setattr(replicate, key, {**(getattr(replicate, key) or {}), **(value or {})})
            else:
                setattr(replicate, key, value)
    await db.flush()
    # Reload once with each replicate's cell, which response serialization reads.
    loaded = {
        replicate.replicate_label: replicate
        for replicate in await list_replicates(db, experiment_id=experiment_id, revision_id=revision_id)
    }
    return [loaded[label] for label, _ in planned]


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
        key: value for key, value in (fields.get("factor_values") or {}).items() if key.lower() not in _NON_FACTOR_KEYS
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
        cell = await get_factorial_cell(db, experiment_id=experiment_id, cell_label=cell_label, revision_id=revision_id)
    if cell is None:
        from asaree.models.protocol_revision import ProtocolRevision

        if await db.scalar(
            select(ProtocolRevision.id).where(ProtocolRevision.design_revision_id == revision_id).limit(1)
        ):
            raise ValueError("Generate a draft design before adding cells to a published experiment version.")
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
        from asaree.models.protocol_revision import ProtocolRevision

        if merged_factors != (cell.factor_values or {}) and await db.scalar(
            select(ProtocolRevision.id).where(ProtocolRevision.design_revision_id == revision_id).limit(1)
        ):
            raise ValueError("Published experiment cell factors are immutable. Generate a draft design first.")
        cell.factor_values = merged_factors

    replicate = await get_replicate(
        db, experiment_id=experiment_id, replicate_label=replicate_label, revision_id=revision_id
    )
    if replicate is None:
        from asaree.models.protocol_revision import ProtocolRevision

        if await db.scalar(
            select(ProtocolRevision.id).where(ProtocolRevision.design_revision_id == revision_id).limit(1)
        ):
            raise ValueError("Generate a draft design before adding replicates to a published experiment version.")
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
