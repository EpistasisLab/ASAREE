"""Run-browser contracts through the public read and batch-preview interfaces."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from asaree.api.result_jobs import Command
from asaree.services.result_jobs import prepare_step
from asaree.services.result_reads import ResultScope, read_cells, read_executions


def scope():
    return ResultScope(uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3), uuid.UUID(int=4), None, {}, True, None)


@pytest.mark.parametrize('mode,expected', [
    ('pending', ['not_started']),
    ('retry', ['failed']),
    ('rerun', ['failed', 'cancelled', 'completed', 'limit_reached']),
])
async def test_preview_seals_only_the_requested_action(mode, expected):
    statuses = ['not_started', 'failed', 'cancelled', 'completed', 'limit_reached', 'pending', 'running', 'finalizing']
    rows = [dict(replicate_id=uuid.UUID(int=i + 10), slot_id=uuid.UUID(int=i + 10),
                 row_index=None, run_id=None if status == 'not_started' else uuid.UUID(int=i + 30), status=status)
            for i, status in enumerate(statuses)]
    inserted = []

    async def execute(statement):
        if statement.is_insert:
            inserted.extend(statement.compile().params.values())
            return None
        return SimpleNamespace(mappings=lambda: rows)

    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(
        all=lambda: [row['replicate_id'] for row in rows])),
                         execute=execute)
    job = SimpleNamespace(id=uuid.uuid4(), progress={'validated': True},
                          params={'selection': {'mode': mode}, 'cutoff': '2026-10-14T00:00:00+00:00'})
    await prepare_step(db, job, scope())
    selected_ids = {value for value in inserted if isinstance(value, uuid.UUID)}
    assert [row['status'] for row in rows if row['replicate_id'] in selected_ids] == expected


def test_command_rejects_unknown_actions_and_unbounded_search():
    with pytest.raises(ValidationError):
        Command(kind='plan', request_key='test', mode='resume')
    with pytest.raises(ValidationError):
        Command(kind='plan', request_key='test', search='x' * 201)


@pytest.mark.parametrize('reader', [read_cells, read_executions])
async def test_search_and_status_are_applied_on_server_before_pagination(reader):
    statements = []

    class Empty:
        def mappings(self): return self
        def all(self): return []
        def __iter__(self): return iter([])

    async def capture(statement):
        statements.append(str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True})))
        return Empty()

    db = SimpleNamespace(execute=capture, get=AsyncMock(return_value=None))
    page = await reader(db, scope(), search='model 2', status='failed',
                        sort='label' if reader == read_cells else 'number')
    assert page['items'] == []
    sql = '\n'.join(statements)
    assert 'ILIKE' in sql
    assert "'failed'" in sql
    assert 'LIMIT 21' in sql
    assert 'ORDER BY' in sql
