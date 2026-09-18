"""Database session lifecycle tests that do not require Postgres."""

from __future__ import annotations

import asyncio

import pytest

from asaree.models import database


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def commit(self) -> None:
        raise AssertionError("a cancelled request must not commit")

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.parametrize("session_context", [database.get_session, database.get_db])
async def test_session_context_rolls_back_when_cancelled(
    monkeypatch: pytest.MonkeyPatch, session_context: object
) -> None:
    """Cancellation must await rollback before releasing an asyncpg connection."""
    session = _Session()
    monkeypatch.setattr(database, "_session_factory", lambda: session)

    if session_context is database.get_session:
        with pytest.raises(asyncio.CancelledError):
            async with database.get_session():
                raise asyncio.CancelledError
    else:
        context = database.get_db()
        await anext(context)
        with pytest.raises(asyncio.CancelledError):
            await context.athrow(asyncio.CancelledError)

    assert session.rollbacks == 1
