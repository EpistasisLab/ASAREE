"""The migration chain must be safe to re-run, not just to run once.

Migrations get re-run more often than "each revision executes exactly once"
suggests: a restored backup, a lost or hand-edited ``alembic_version`` row, a
deploy retried after a partial failure (see ``migrations/guards.py``). A revision
that creates something without ``IF NOT EXISTS`` -- or a function/trigger without
replacing it -- passes every normal upgrade and then wedges the next re-run.

Skipped unless ``ASAREE_MIGRATIONS_TEST_DATABASE_URL`` points at a PostgreSQL
server (any database on it). The tests create and drop their own scratch
database there and never touch the one named in the URL.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

DB_URL = os.environ.get("ASAREE_MIGRATIONS_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(not DB_URL, reason="ASAREE_MIGRATIONS_TEST_DATABASE_URL is not set")

SCRATCH = "asaree_t_migration_rerun"

# Everything a revision can create in the product schema, rendered canonically.
SCHEMA_SQL = {
    "columns": """
        SELECT table_name || '.' || column_name || ':' || data_type || ':' || is_nullable
               || ':' || COALESCE(column_default, '')
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name <> 'alembic_version' ORDER BY 1""",
    "indexes": "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1",
    "constraints": """
        SELECT conrelid::regclass || ' ' || conname || ' ' || pg_get_constraintdef(oid)
        FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1""",
    "functions": """
        SELECT pg_get_functiondef(p.oid) FROM pg_proc p
        WHERE p.pronamespace = 'public'::regnamespace AND p.prokind = 'f' ORDER BY 1""",
    "triggers": """
        SELECT pg_get_triggerdef(oid) FROM pg_trigger
        WHERE NOT tgisinternal AND tgrelid::regclass::text NOT LIKE 'pg_%' ORDER BY 1""",
    "types": """
        SELECT t.typname || ':' || string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
        FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE t.typnamespace = 'public'::regnamespace GROUP BY t.typname ORDER BY 1""",
}


# Revisions that predate this test and cannot yet be re-run. They are already
# applied everywhere, so they only bite on a retried or re-stamped deploy. Listed
# rather than skipped: each must still fail, so fixing one forces its removal
# here, and every revision not listed -- including every new one -- is enforced.
LEGACY_UPGRADE_RERUN = {
    "d5a3b90c71e4",
    "e2f7c4a91b60",
    "a7b8c9d0e1f2",
    "f1a2b3c4d5e6",
    "9e4a7b2c1d30",
    "0a1b2c3d4e5f",
    "1b2c3d4e5f60",
    "2c3d4e5f6071",
}
LEGACY_DOWNGRADE_RERUN = {
    "d5a3b90c71e4",
    "e2f7c4a91b60",
    "a7b8c9d0e1f2",
    "b5e6f7a8b9c0",
    "f1a2b3c4d5e6",
    "9e4a7b2c1d30",
    "0a1b2c3d4e5f",
    "1b2c3d4e5f60",
    "2c3d4e5f6071",
}


def _check(failures: dict[str, str], legacy: set[str]) -> None:
    unexpected = [message for revision, message in failures.items() if revision not in legacy]
    fixed = sorted(legacy - set(failures))
    assert not unexpected, "\n".join(unexpected)
    assert not fixed, f"now re-runnable -- remove from the legacy list: {fixed}"


def _url_for(db_name: str) -> str:
    base, _, _ = DB_URL.rpartition("/")
    return f"{base}/{db_name}"


async def _admin(sql: str) -> None:
    """CREATE/DROP DATABASE cannot run inside a transaction."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_url_for("postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


async def _schema(url: str) -> dict[str, list[str]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return {kind: [row[0] for row in await conn.execute(text(sql))] for kind, sql in SCHEMA_SQL.items()}
    finally:
        await engine.dispose()


def _chain() -> list[tuple[str, str]]:
    """``(revision, parent)`` pairs from the first revision to head."""
    from alembic.script import ScriptDirectory

    from asaree.migrations import make_config

    script = ScriptDirectory.from_config(make_config(_url_for(SCRATCH)))
    assert len(script.get_heads()) == 1, "the chain must have a single head"
    return [(rev.revision, rev.down_revision or "base") for rev in reversed(list(script.walk_revisions()))]


async def _run(command: Any, url: str, revision: str) -> None:
    # The alembic commands start their own event loop, so keep them off this one.
    await asyncio.to_thread(command, url, revision)


def _diff(before: dict[str, list[str]], after: dict[str, list[str]]) -> str:
    lines = []
    for kind in SCHEMA_SQL:
        gone, new = set(before[kind]) - set(after[kind]), set(after[kind]) - set(before[kind])
        lines += [f"{kind} missing: {item}" for item in sorted(gone)] + [
            f"{kind} extra: {item}" for item in sorted(new)
        ]
    return "\n".join(lines[:40])


@pytest.fixture
async def scratch() -> Any:
    await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH}"')
    await _admin(f'CREATE DATABASE "{SCRATCH}"')
    yield _url_for(SCRATCH)
    await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH}"')


async def test_each_upgrade_survives_a_lost_stamp(scratch: str) -> None:
    """Apply each revision, then apply it again over its own result.

    Walks the chain once, so every revision is re-run against exactly the schema
    it produced. The re-run must succeed and leave that schema unchanged: one
    that errors wedges a deploy, and one that silently changes the schema means
    the stamp no longer describes the database.
    """
    from asaree.migrations import stamp, upgrade

    failures: dict[str, str] = {}
    for revision, parent in _chain():
        await _run(upgrade, scratch, revision)
        expected = await _schema(scratch)
        try:
            await _run(stamp, scratch, parent)
            await _run(upgrade, scratch, revision)
        except Exception as exc:  # noqa: BLE001 -- collect every offending revision, not just the first
            failures[revision] = f"{revision}: re-run failed: {str(exc).splitlines()[0][:200]}"
            await _run(stamp, scratch, revision)  # each revision is atomic, so the schema is still `expected`
            continue
        if (actual := await _schema(scratch)) != expected:
            failures[revision] = f"{revision}: re-run changed the schema\n{_diff(expected, actual)}"
    _check(failures, LEGACY_UPGRADE_RERUN)


async def test_each_downgrade_survives_a_lost_stamp(scratch: str) -> None:
    """Undo each revision, then undo it again as if its stamp had been lost."""
    from asaree.migrations import downgrade, stamp, upgrade

    await _run(upgrade, scratch, "head")
    head = await _schema(scratch)
    failures: dict[str, str] = {}
    for revision, parent in reversed(_chain()):
        await _run(downgrade, scratch, parent)
        expected = await _schema(scratch)
        try:
            await _run(stamp, scratch, revision)
            await _run(downgrade, scratch, parent)
        except Exception as exc:  # noqa: BLE001 -- collect every offending revision, not just the first
            failures[revision] = f"{revision}: downgrade re-run failed: {str(exc).splitlines()[0][:200]}"
            await _run(stamp, scratch, parent)
            continue
        if (actual := await _schema(scratch)) != expected:
            failures[revision] = f"{revision}: downgrade re-run changed the schema\n{_diff(expected, actual)}"
    _check(failures, LEGACY_DOWNGRADE_RERUN)

    await _run(upgrade, scratch, "head")
    assert await _schema(scratch) == head, _diff(head, await _schema(scratch))
