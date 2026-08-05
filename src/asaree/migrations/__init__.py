"""ASAREE's own schema, and its own migration chain.

Separate from agentic-core's chain in every respect: own database
(``settings.product_database_url``, the ``asaree`` database — a different
database from core's ``agentic_core``, though both currently live on the same
Postgres server), own version table (the plain Alembic default,
``alembic_version`` — no collision risk since it's not sharing a database with
another chain), own ``Base.metadata``. No ``include_object`` filter is needed
here the way core's chain needs one: ASAREE owns its whole metadata outright,
nothing else writes to this database.

Ordering when standing up an environment: core's chain first
(``python -m agentic_core.migrations upgrade``), then this one. Product tables
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


def make_config(url: str | None = None) -> Config:
    """Build an Alembic config pointed at ASAREE's chain.

    *url* defaults to ``AsareeSettings.product_database_url``.
    """
    from alembic.config import Config

    from asaree.config import get_settings

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url or get_settings().product_database_url)
    return cfg


def upgrade(url: str | None = None, revision: str = "head") -> None:
    """Bring ASAREE's schema up to *revision*. Safe to call repeatedly."""
    from alembic import command

    command.upgrade(make_config(url), revision)


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

    from asaree.config import get_settings

    engine = create_async_engine(url or get_settings().product_database_url)
    try:
        async with engine.connect() as conn:
            if await conn.scalar(text("SELECT to_regclass('alembic_version')")) is None:
                return None
            return await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
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
