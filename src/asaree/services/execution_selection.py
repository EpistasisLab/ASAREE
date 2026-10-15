"""Shared predicates for browsing and sealing a batch's execution selection."""
from typing import Any

from sqlalchemy import Text, and_, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by


def cell_filters(cell: Any, search: str = '', factors: dict[str, Any] | None = None) -> list[Any]:
    predicates = []
    if search.strip():
        # Treat user input as literal text, including percent and underscore.
        value = '%' + search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        predicates.append(or_(cell.cell_label.ilike(value, escape='\\'),
                              cast(cell.factor_values, Text).ilike(value, escape='\\')))
    if factors:
        predicates.append(cell.factor_values.contains(factors))
    return predicates


def slot_filters(slots: Any, status: str | None = None) -> list[Any]:
    if status == 'scoring_failed':
        return [and_(slots.c.status == 'completed', slots.c.result['evaluation_state'].astext == 'failed',
                     func.coalesce(slots.c.result['truncated'].astext, 'false') != 'true')]
    if status == 'limit_reached':
        return [or_(slots.c.status == 'limit_reached', slots.c.result['truncated'].astext == 'true')]
    if status == 'completed':
        return [and_(slots.c.status == status, func.coalesce(slots.c.result['truncated'].astext, 'false') != 'true',
                     func.coalesce(slots.c.result['evaluation_state'].astext, '') != 'failed')]
    if status == 'failed':
        return [and_(slots.c.status == 'failed', func.coalesce(slots.c.result['truncated'].astext, 'false') != 'true')]
    return [slots.c.status == status] if status else []


def natural_label(value: Any) -> Any:
    """PostgreSQL numeric collation without requiring a deployment-level collation."""
    parts = func.regexp_matches(func.lower(value), r'([0-9]+|[^0-9]+)', 'g').table_valued(
        'part', with_ordinality='position').render_derived()
    part = cast(parts.c.part, ARRAY(Text))[1]
    digits = func.coalesce(func.nullif(func.ltrim(part, '0'), ''), '0')
    # Length before digits handles numbers of any size, including embedded factors.
    number = cast(func.lpad(cast(func.length(digits), Text), 10, '0'), Text) + literal(':') + cast(digits, Text)
    normalized = case((part.op('~')(r'^[0-9]+$'), number), else_=part)
    return select(func.coalesce(func.string_agg(
        normalized, aggregate_order_by(literal(''), parts.c.position)), '')).select_from(
            parts).correlate_except(parts).scalar_subquery()
