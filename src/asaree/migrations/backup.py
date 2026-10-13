"""Dump each database before a deploy migrates it -- a troubleshooting snapshot.

    python -m asaree.migrations backup --dir backups

Runs as the deploy step before ``motoro.migrations deploy`` (compose's
``db-backup`` service). For core's database and ASAREE's, it compares the
schema's current revision with the head of the code being deployed and, only
when a migration is pending, writes ``pg_dump -Fc`` output to::

    {db}-{revision}-before-v{version}-{UTC timestamp}.dump

``revision`` is the schema the dump holds; ``version`` is the release whose
migrations were about to run (``asaree``'s version for the product database,
``motoro``'s for core's), so ``asaree-2c3d4e5f6071-before-v0.11.0-...`` reads
"the database at schema 2c3d4e5f6071, saved before v0.11.0 migrated it". The
release it came *from* isn't recorded anywhere, so the revision identifies it.
Restore with the same image's ``pg_restore``
(``pg_restore --clean --if-exists -d <db> <file>``). The image's client is newer
than a pg16 server, so a restore reports one ignorable error about
``transaction_timeout`` (a setting older servers don't have).

This is not a backup strategy: it exists only at deploy time. Dumps contain user
data, so they are written owner-only, and only the newest ``--keep`` automatic
dumps per database are retained. Files that don't match the name above (manual
dumps) are never touched. Any failure exits non-zero, which stops the deploy:
migrating without the snapshot it was promised is worse than not deploying.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    package: str
    current: str | None
    heads: tuple[str, ...]

    @property
    def pending(self) -> bool:
        # A database never migrated has nothing worth snapshotting yet.
        return self.current is not None and self.current not in self.heads


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)


def dump_name(name: str, version: str, revision: str, now: datetime) -> str:
    return f"{name}-{_safe(revision)}-before-v{_safe(version)}-{now.strftime('%Y%m%dT%H%M%SZ')}.dump"


def automatic_dumps(directory: Path, name: str) -> list[Path]:
    """This database's automatic dumps, oldest first (names sort by timestamp last)."""
    pattern = re.compile(rf"^{re.escape(name)}-[A-Za-z0-9._-]+-before-v[A-Za-z0-9._-]+-\d{{8}}T\d{{6}}Z\.dump$")
    dumps = [path for path in directory.iterdir() if pattern.match(path.name)]
    return sorted(dumps, key=lambda path: path.name.rsplit("-", 1)[1])


def prune(directory: Path, name: str, keep: int) -> list[Path]:
    dumps = automatic_dumps(directory, name)
    stale = dumps[: max(0, len(dumps) - keep)]
    for path in stale:
        path.unlink()
    return stale


def pg_dump(url: str, destination: Path) -> None:
    """``pg_dump -Fc`` into ``destination``, atomically and owner-only.

    Credentials go through libpq's environment rather than argv, and the dump is
    written beside its final name and renamed, so an interrupted run never
    leaves a truncated file that looks like a valid snapshot.
    """
    parsed = make_url(url)
    env = {
        **os.environ,
        "PGHOST": parsed.host or "localhost",
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": parsed.username or "",
        "PGPASSWORD": parsed.password or "",
        "PGDATABASE": parsed.database or "",
    }
    partial = destination.with_name(destination.name + ".partial")
    try:
        with open(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
            subprocess.run(["pg_dump", "--format=custom", "--no-owner"], env=env, stdout=out, check=True)
        give_to_directory_owner(partial)
        partial.rename(destination)
    finally:
        partial.unlink(missing_ok=True)


def give_to_directory_owner(path: Path) -> None:
    """Hand a dump written as root to whoever owns the backups directory.

    The compose service runs as root so it can always write the bind mount; a
    fixed non-root UID broke deploys on any Linux host whose user isn't 1000.
    The mounted directory's owner is the host user, so the dumps end up theirs.
    Docker Desktop on macOS already maps writes to the host user and may refuse
    or ignore chown on a bind mount, so a failure here is not an error.
    """
    if os.geteuid() != 0:
        return
    owner = path.parent.stat()
    with contextlib.suppress(OSError):
        os.chown(path, owner.st_uid, owner.st_gid)


async def targets(core_url: str, product_url: str) -> list[Target]:
    from alembic.script import ScriptDirectory
    from motoro import migrations as core

    from asaree import migrations as product

    return [
        Target(
            "motoro",
            core_url,
            "motoro",
            await core.current_revision(core_url),
            tuple(ScriptDirectory.from_config(core.make_config(core_url)).get_heads()),
        ),
        Target(
            "asaree",
            product_url,
            "asaree",
            await product.current_revision(product_url),
            tuple(ScriptDirectory.from_config(product.make_config(product_url)).get_heads()),
        ),
    ]


def backup(found: list[Target], directory: Path, keep: int, now: datetime | None = None) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)
    report = []
    for target in found:
        if not target.pending:
            state = "not migrated yet" if target.current is None else f"already at {target.current}"
            report.append(f"{target.name}: {state}, no backup needed")
            continue
        assert target.current is not None
        version = importlib.metadata.version(target.package)
        destination = directory / dump_name(target.name, version, target.current, now)
        pg_dump(target.url, destination)
        removed = prune(directory, target.name, keep)
        report.append(
            f"{target.name}: {target.current} -> {', '.join(target.heads)} pending; wrote {destination.name}"
            + (f", pruned {len(removed)} older" if removed else "")
        )
    return report


def main(args: Any) -> int:
    from asaree.config import get_settings

    settings = get_settings()
    core_url = args.core_url or settings.database_url
    product_url = args.url or settings.product_database_url
    found = asyncio.run(targets(core_url, product_url))
    for line in backup(found, Path(args.dir), args.keep):
        print(line)
    return 0
