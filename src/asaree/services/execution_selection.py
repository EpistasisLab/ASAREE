"""Shared predicates for browsing and sealing a batch's execution selection."""
from typing import Any

from sqlalchemy import Text, and_, cast, func, or_


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


def effective_status(row: dict[str, Any]) -> str:
    result = row.get('result') or {}
    if result.get('truncated') or row['status'] == 'limit_reached':
        return 'limit_reached'
    if row['status'] == 'completed' and result.get('evaluation_state') == 'failed':
        return 'scoring_failed'
    return str(row['status'])


def action_eligible(status: str, mode: str | None, truncated: bool = False) -> bool:
    if mode == 'pending':
        return status == 'not_started'
    if mode == 'retry':
        return status == 'failed' and not truncated
    if mode == 'rerun':
        return status in {'failed', 'cancelled', 'completed', 'limit_reached'}
    return False


def natural_label(value: Any) -> Any:
    """PostgreSQL numeric collation without requiring a deployment-level collation."""
    # Pad a numeric suffix (Cell 2 / Cell 10); factor-value sorts have their own key.
    suffix = func.substring(value, r'[0-9]+$')
    return cast(func.regexp_replace(func.lower(value), r'[0-9]+$', ''), Text) + cast(
        func.coalesce(func.lpad(suffix, 30, '0'), ''), Text)
