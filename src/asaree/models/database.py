"""Async engine and session for ASAREE's own database.

Mirrors Motoro's own ``models/database.py`` shape, but reads
``settings.product_database_url`` — ASAREE's database, not core's. ASAREE is
the product, so unlike core it's free to hand a session straight to a FastAPI
route via ``Depends(get_db)``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from asaree.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Get or create the async database engine (lazy singleton)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.product_database_url,
            echo=settings.debug,
            pool_pre_ping=True,
            pool_recycle=settings.db_pool_recycle_seconds,
        )
    return _engine


async def dispose_engine() -> None:
    """Close the engine's connection pool and drop the cached singleton."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async session for the current request."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Same commit/rollback contract as :func:`get_db`, for callers outside FastAPI's
    dependency injection — e.g. a standalone MCP server process that shares this
    database but isn't a FastAPI route."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
