"""Search and status filtering happen before bounded execution pages are returned."""
import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from asaree.services.result_reads import ResultScope, read_cells, read_executions


def scope():
    return ResultScope(uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3), uuid.UUID(int=4), None, {}, True, None)


@pytest.mark.parametrize('reader', [read_cells, read_executions])
async def test_search_and_status_are_applied_on_server_before_pagination(reader):
    statements = []
    parameters = []

    class Empty:
        def mappings(self): return self
        def all(self): return []
        def __iter__(self): return iter([])

    async def capture(statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        statements.append(str(compiled))
        parameters.extend(compiled.params.values())
        return Empty()

    db = SimpleNamespace(execute=capture, get=AsyncMock(return_value=None))
    page = await reader(db, scope(), search='model 2', status='failed', factors={'model': 'A'})
    assert page['items'] == []
    sql = '\n'.join(statements)
    assert 'ILIKE' in sql
    assert 'failed' in parameters
    assert {'model': 'A'} in parameters
    assert 21 in parameters
    assert 'LIMIT' in sql
    assert 'ORDER BY' in sql
    # PostgreSQL requires grouping before a CAST result can be subscripted.
    assert not re.search(r'CAST\([^)]* AS TEXT\[\]\)\[', sql)
