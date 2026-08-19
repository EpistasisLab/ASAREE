"""ASAREE's own schema, and its own migration chain.

Separate from Motoro's chain in every respect: own database
(``settings.product_database_url``, the ``asaree`` database — a different
database from core's ``motoro``, though both currently live on the same
Postgres server), own version table (the plain Alembic default,
``alembic_version`` — no collision risk since it's not sharing a database with
another chain), own ``Base.metadata``. No ``include_object`` filter is needed
here the way core's chain needs one: ASAREE owns its whole metadata outright,
nothing else writes to this database.

Ordering when standing up an environment: core's chain first
(``python -m motoro.migrations upgrade``), then this one. Product tables
routinely carry an opaque UUID referencing a core row; the reverse is
forbidden, and core's chain has no idea ASAREE exists.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parent


def _resolve_url(url: str | None) -> str:
    from asaree.config import get_settings

    return url or get_settings().product_database_url


def make_config(url: str | None = None) -> Config:
    """Build an Alembic config pointed at ASAREE's chain.

    *url* defaults to ``AsareeSettings.product_database_url``.
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", _resolve_url(url))
    return cfg


async def _ensure_database_exists(url: str) -> None:
    """Create the target database if it doesn't exist yet.

    Postgres never does this on its own, and nothing in this project's setup
    does either — unlike ``motoro``, which core's own docker-compose
    creates via ``POSTGRES_DB`` on first boot, ``asaree`` has no equivalent
    anywhere. Whoever hits this first has always had to create it by hand,
    silently, which is exactly the gap this closes.

    ``CREATE DATABASE`` cannot run inside a transaction, so this connects
    with ``AUTOCOMMIT`` isolation, and to the server's own "postgres"
    maintenance database rather than the target — which, by definition,
    might not exist yet.
    """
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    target = make_url(url)
    dbname = target.database
    assert dbname, f"database URL has no database name: {url!r}"
    server_url = target.set(database="postgres")

    engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": dbname})
            if not exists:
                # Can't parametrize a DDL identifier; quoted to tolerate any casing/specials.
                await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        await engine.dispose()


def upgrade(url: str | None = None, revision: str = "head") -> None:
    """Bring ASAREE's schema up to *revision*. Safe to call repeatedly.

    Creates the target database first if it's missing — see
    :func:`_ensure_database_exists`.
    """
    from alembic import command

    resolved_url = _resolve_url(url)
    asyncio.run(_ensure_database_exists(resolved_url))
    command.upgrade(make_config(resolved_url), revision)


def downgrade(url: str | None = None, revision: str = "-1") -> None:
    """Step ASAREE's schema back to *revision*. Mainly a testing affordance."""
    from alembic import command

    command.downgrade(make_config(url), revision)


def stamp(url: str | None = None, revision: str = "head") -> None:
    """Mark the schema as being at *revision* without running anything."""
    from alembic import command

    command.stamp(make_config(url), revision)


async def upgrade_async(url: str | None = None, revision: str = "head") -> None:
    """:func:`upgrade`, callable from async code (runs in a worker thread)."""
    await asyncio.to_thread(upgrade, url, revision)


async def current_revision(url: str | None = None) -> str | None:
    """The revision ASAREE's schema is stamped at, or ``None`` if unmigrated."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_resolve_url(url))
    try:
        async with engine.connect() as conn:
            if await conn.scalar(text("SELECT to_regclass('alembic_version')")) is None:
                return None
            version_num = await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
            return str(version_num) if version_num is not None else None
    finally:
        await engine.dispose()


__all__ = [
    "MIGRATIONS_DIR",
    "current_revision",
    "downgrade",
    "make_config",
    "stamp",
    "upgrade",
    "upgrade_async",
]
