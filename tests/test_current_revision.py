"""Revision inspection must work before a fresh database has been created."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg import InvalidCatalogNameError, InvalidPasswordError

from asaree.migrations import current_revision

URL = "postgresql+asyncpg://u:p@localhost:5432/asaree"


@pytest.fixture
def engine(monkeypatch):
    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr("asaree.migrations._resolve_url", lambda url: url)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", lambda url: engine)
    return engine


@pytest.mark.parametrize("error", [InvalidCatalogNameError, InvalidPasswordError, ConnectionRefusedError])
async def test_missing_database_is_unmigrated_but_other_connection_errors_propagate(engine, error):
    engine.connect.return_value.__aenter__ = AsyncMock(side_effect=error("connection failed"))

    if error is InvalidCatalogNameError:
        assert await current_revision(URL) is None
    else:
        with pytest.raises(error):
            await current_revision(URL)

    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    ("values", "expected"),
    [([None], None), (["alembic_version", None], None), (["alembic_version", "abc123"], "abc123")],
)
async def test_existing_database_revision(engine, values, expected):
    connection = AsyncMock()
    connection.scalar.side_effect = values
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)

    assert await current_revision(URL) == expected
    engine.dispose.assert_awaited_once()
