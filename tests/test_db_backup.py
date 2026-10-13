"""Pre-deploy database dumps (``python -m asaree.migrations backup``)."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from asaree.migrations import backup
from asaree.migrations.backup import Target, automatic_dumps, dump_name, prune

NOW = datetime(2026, 10, 13, 19, 0, 5, tzinfo=UTC)


def target(name: str = "asaree", current: str | None = "aaa", heads: tuple[str, ...] = ("bbb",)) -> Target:
    return Target(name, f"postgresql+asyncpg://u:p@localhost:5432/{name}", name, current, heads)


def test_dump_name_records_release_and_schema_revision():
    assert dump_name("asaree", "0.11.0", "2c3d4e5f6071", NOW) == (
        "asaree-2c3d4e5f6071-before-v0.11.0-20261013T190005Z.dump"
    )
    # Dev builds carry local-version characters that don't belong in a file name.
    assert dump_name("asaree", "0.10.1.post1.dev47+gbc4012f", "r", NOW) == (
        "asaree-r-before-v0.10.1.post1.dev47_gbc4012f-20261013T190005Z.dump"
    )


@pytest.mark.parametrize(
    ("current", "heads", "pending"),
    [("aaa", ("bbb",), True), ("bbb", ("bbb",), False), (None, ("bbb",), False)],
)
def test_only_a_migrated_database_with_pending_revisions_is_dumped(current, heads, pending):
    assert target(current=current, heads=heads).pending is pending


def test_prune_keeps_newest_automatic_dumps_and_never_touches_manual_ones(tmp_path: Path):
    days = [datetime(2026, 10, day, tzinfo=UTC) for day in range(1, 8)]
    for day in days:
        (tmp_path / dump_name("asaree", "0.10.0", "r", day)).touch()
    (tmp_path / dump_name("motoro", "0.11.0", "r", days[0])).touch()
    manual = tmp_path / "asaree_20260819_153118.dump"
    manual.touch()

    removed = prune(tmp_path, "asaree", keep=3)

    assert [path.name for path in removed] == [dump_name("asaree", "0.10.0", "r", day) for day in days[:4]]
    assert [path.name for path in automatic_dumps(tmp_path, "asaree")] == [
        dump_name("asaree", "0.10.0", "r", day) for day in days[4:]
    ]
    assert manual.exists() and automatic_dumps(tmp_path, "motoro")


def test_backup_dumps_only_pending_databases(tmp_path: Path, monkeypatch):
    dumped = []
    monkeypatch.setattr(backup, "pg_dump", lambda url, destination: (dumped.append(url), destination.touch()))
    versions = {"asaree": "0.11.0", "motoro": "0.12.0"}
    monkeypatch.setattr(backup.importlib.metadata, "version", lambda package: versions[package])

    report = backup.backup(
        [target("motoro", current="6a4d", heads=("7d3b",)), target("asaree", current="bbb", heads=("bbb",))],
        tmp_path,
        keep=5,
        now=NOW,
    )

    assert dumped == ["postgresql+asyncpg://u:p@localhost:5432/motoro"]
    assert [path.name for path in tmp_path.iterdir()] == ["motoro-6a4d-before-v0.12.0-20261013T190005Z.dump"]
    assert report[1] == "asaree: already at bbb, no backup needed"


def test_a_failed_dump_leaves_no_file_behind(tmp_path: Path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "pg_dump")

    monkeypatch.setattr(backup.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        backup.pg_dump("postgresql+asyncpg://u:p@localhost:5432/asaree", tmp_path / "x.dump")
    assert list(tmp_path.iterdir()) == []


DB_URL = os.environ.get("ASAREE_MIGRATIONS_TEST_DATABASE_URL", "")


@pytest.mark.skipif(not DB_URL or shutil.which("pg_dump") is None, reason="needs a test server and pg_dump")
def test_real_dump_restores_the_pre_migration_schema(tmp_path: Path):
    """End to end against a scratch database one revision behind head."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from asaree import migrations

    base, _, _ = DB_URL.rpartition("/")
    scratch = f"{base}/asaree_t_backup"

    async def admin(sql: str) -> None:
        engine = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(sql))
        await engine.dispose()

    asyncio.run(admin('DROP DATABASE IF EXISTS "asaree_t_backup"'))
    asyncio.run(admin('CREATE DATABASE "asaree_t_backup"'))
    try:
        migrations.upgrade(scratch, "head")
        migrations.downgrade(scratch, "-1")
        current = asyncio.run(migrations.current_revision(scratch))
        found = asyncio.run(backup.targets(DB_URL, scratch))[1]
        assert found.pending and found.current == current

        report = backup.backup([found], tmp_path, keep=5)

        (dump,) = tmp_path.iterdir()
        assert dump.name.startswith(f"asaree-{current}-before-v"), report
        assert stat.S_IMODE(dump.stat().st_mode) == 0o600
        listing = subprocess.run(["pg_restore", "--list", str(dump)], capture_output=True, text=True, check=True)
        assert "alembic_version" in listing.stdout
    finally:
        asyncio.run(admin('DROP DATABASE IF EXISTS "asaree_t_backup"'))
