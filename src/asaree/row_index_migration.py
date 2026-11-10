"""Offline inventory, rebuild, verification and reversible retirement of SQLite indexes.

SQLite is read only here for compatibility; production readers use PostgreSQL.
Stop all API/workers before use and retain coordinated database/file backups.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

from asaree_workspace_core.storage import atomic_write, sha256
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.database import get_session
from asaree.models.dataset import RegisteredDataset
from asaree.models.dataset_row_index import DatasetRowIndex
from asaree.services.dataset_row_retention import job_sources
from asaree.services.indexed_row_source import (
    BATCH_SIZE,
    PARSER_VERSION,
    csv_records,
    next_batch,
    read_indexed_row_source,
    verify_original,
)
from asaree.services.result_jobs import restored_source

JsonDict = dict[str, Any]


def inventory_files(directories: list[Path]) -> list[JsonDict]:
    files = []
    seen_directories: set[Path] = set()
    for directory in directories:
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"Legacy index directory is unavailable or a symlink: {directory}")
        directory = directory.resolve()
        if directory in seen_directories:
            continue
        seen_directories.add(directory)
        for path in sorted(directory.iterdir()):
            entry: JsonDict = {"path": str(path.resolve()), "status": "retained_unknown"}
            if path.is_symlink():
                entry["error"] = "Symlink requires explicit operator review"
            elif path.is_file() and re.fullmatch(r"[0-9a-f]{64}\.sqlite", path.name):
                entry.update(status="indexed", source_sha256=path.stem, file_sha256=sha256(path))
                try:
                    with legacy_connection(path) as connection:
                        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
                        if metadata["sha256"] != path.stem:
                            raise ValueError("SQLite source hash disagrees with its filename")
                        columns = json.loads(metadata["columns"])
                        if not isinstance(columns, list) or any(not isinstance(c, str) for c in columns):
                            raise ValueError("Invalid SQLite header metadata")
                        entry.update(columns=columns, count=int(metadata["count"]))
                except (sqlite3.Error, ValueError, KeyError) as exc:
                    entry.update(status="inconsistent", error=str(exc))
            files.append(entry)
    return files


def legacy_connection(path: Path) -> closing[sqlite3.Connection]:
    # Read-only and query-only, including during operator rebuild/retirement.
    connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return closing(connection)


def legacy_row(data: Any) -> tuple[str, ...]:
    if not isinstance(data, str):
        raise ValueError("Invalid SQLite row payload type")
    values = json.loads(data)
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError("Invalid SQLite row payload; expected an array of strings")
    return tuple(values)


async def verify_source(
    db: AsyncSession, dataset: RegisteredDataset, files: list[JsonDict], *, postgres: bool,
) -> JsonDict:
    result: JsonDict = {"dataset_id": str(dataset.id), "owner_id": str(dataset.owner_id),
              "sha256": dataset.raw_sha256, "parser_version": PARSER_VERSION, "status": "unavailable"}
    try:
        source = (await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=dataset.owner_id)
                  if postgres else None)
        # Always verify again for operator comparison: no stat lease, and use
        # one snapshot for original CSV/SQLite/PostgreSQL row comparisons.
        with verify_original(dataset.raw_path, dataset.raw_sha256) as verified:
            records = csv_records(verified)
            try:
                columns = next(records)
                if source is not None and columns != source.columns:
                    raise ValueError("PostgreSQL columns disagree with original")
                from contextlib import ExitStack

                with ExitStack() as stack:
                    cursors = []
                    for entry in files:
                        if entry["status"] != "indexed":
                            raise ValueError("SQLite index metadata is inconsistent")
                        if columns != tuple(entry["columns"]):
                            raise ValueError("SQLite columns disagree with original")
                        connection = stack.enter_context(legacy_connection(Path(entry["path"])))
                        cursors.append(connection.execute("SELECT row_index,data FROM rows ORDER BY row_index"))
                    count = 0
                    while batch := await asyncio.to_thread(next_batch, records):
                        if source is not None:
                            window = await source.fetch_range(db, count, count + len(batch))
                            if any(window.rows[count + i] != row for i, row in enumerate(batch)):
                                raise ValueError("PostgreSQL row values disagree with original")
                        for cursor in cursors:
                            legacy = cursor.fetchmany(BATCH_SIZE)
                            if len(legacy) != len(batch) or any(
                                index != count + i or legacy_row(data) != batch[i]
                                for i, (index, data) in enumerate(legacy)
                            ):
                                raise ValueError("SQLite row positions/values disagree with original")
                        count += len(batch)
                    if any(cursor.fetchone() is not None for cursor in cursors):
                        raise ValueError("SQLite has unexpected extra rows")
                    if any(entry["count"] != count for entry in files) or (
                        source is not None and len(source.rows) != count
                    ):
                        raise ValueError("Index row count disagrees with original")
                result.update(status="verified" if postgres else "original_verified", count=count)
            finally:
                records.close()
    except (OSError, ValueError, sqlite3.Error) as exc:
        result["error"] = str(exc)
    return result


async def run_migration(action: str, directories: list[Path]) -> JsonDict:
    report: JsonDict = {"version": 1, "action": action, "files": inventory_files(directories),
              "sources": [], "jobs": [], "problems": [], "diagnostics": []}
    async with get_session() as db:
        dependencies: dict[str, list[uuid.UUID]] = {}
        async for job, source_id in job_sources(db):
            dependencies.setdefault(source_id, []).append(job.id)
        async for job, source_id in job_sources(db, include_terminal=True):
            if job.status not in ("pending", "queued", "awaiting_confirmation"):
                report["diagnostics"].append({"job_id": str(job.id), "source_id": source_id,
                                               "status": job.status, "retention": "historical evidence only"})
        existing = set(str(value) for value in await db.scalars(select(DatasetRowIndex.dataset_id)))
        indexed_hashes = {entry["source_sha256"] for entry in report["files"] if entry.get("source_sha256")}
        registrations = list(await db.scalars(select(RegisteredDataset)))
        known_ids = {str(dataset.id) for dataset in registrations}
        for missing in dependencies.keys() - known_ids:
            report["problems"].append(f"Saved jobs refer to unavailable registration {missing}")
        for dataset in registrations:
            if (dataset.raw_sha256 not in indexed_hashes and str(dataset.id) not in dependencies
                    and str(dataset.id) not in existing):
                continue
            files = [entry for entry in report["files"] if entry.get("source_sha256") == dataset.raw_sha256]
            # Each registration builds its own index even for identical hashes.
            # A failed source rolls back its build, while other sources remain reportable.
            async with db.begin_nested() as transaction:
                if action == "verify" or action == "retire":
                    present = await db.scalar(select(DatasetRowIndex.id).where(
                        DatasetRowIndex.dataset_id == dataset.id,
                        DatasetRowIndex.raw_sha256 == dataset.raw_sha256,
                        DatasetRowIndex.parser_version == PARSER_VERSION,
                    ))
                    if present is None:
                        report["sources"].append({
                            "dataset_id": str(dataset.id), "sha256": dataset.raw_sha256,
                            "status": "unavailable", "error": "PostgreSQL index absent; rebuild first",
                        })
                        continue
                result = await verify_source(db, dataset, files, postgres=action != "inventory")
                report["sources"].append(result)
                if result["status"] not in ("verified", "original_verified"):
                    await transaction.rollback()
                    continue
                if action != "inventory":
                    from asaree.models.result_job import ResultJob
                    from asaree.services.result_jobs import job_scope

                    for job_id in dependencies.get(str(dataset.id), []):
                        loaded_job = await db.get(ResultJob, job_id)
                        if loaded_job is None:
                            report["problems"].append(f"Job {job_id} disappeared during offline migration")
                            continue
                        job = loaded_job
                        if not job.progress.get("source"):
                            # Pre-source jobs still appear as protected dependencies.
                            report["jobs"].append({"id": str(job.id), "status": "protected_unprepared"})
                            continue
                        before = dict(job.progress)
                        progress = dict(before)
                        try:
                            await restored_source(db, progress, await job_scope(db, job), job.owner_id)
                        except ValueError as exc:
                            report["problems"].append(f"Saved job {job.id}: {exc}")
                            continue
                        report["jobs"].append({"id": str(job.id), "status": "verified",
                                               "before_progress": before, "after_progress": progress})
                        if action == "rebuild":
                            job.progress = progress
        for entry in report["files"]:
            if entry["status"] == "indexed" and not any(
                dataset.raw_sha256 == entry["source_sha256"] for dataset in registrations
            ):
                entry["status"] = "unassociated"
                entry["error"] = "No registered original; retained for operator review"
    # Database transaction has committed before retirement. A fresh full
    # verification precedes every rename; locks/partials/unknowns stay intact.
    if action == "retire":
        try:
            retire_verified(report)
        except (OSError, ValueError) as exc:
            report["problems"].append(str(exc))
    return report


def retire_verified(report: JsonDict) -> None:
    if report["problems"] or any(source["status"] != "verified" for source in report["sources"]):
        raise ValueError("Retirement blocked by unavailable/inconsistent sources or saved jobs")
    if any(entry["status"] in ("inconsistent", "unassociated") for entry in report["files"]):
        raise ValueError("Retirement blocked by inconsistent or unassociated legacy indexes")
    prepared = []
    seen_paths: set[Path] = set()
    for entry in report["files"]:
        if entry["status"] != "indexed":
            continue
        if not any(source["sha256"] == entry["source_sha256"] for source in report["sources"]):
            raise ValueError("Retirement requires a verified registered source")
        path = Path(entry["path"])
        if path.resolve() in seen_paths:
            raise ValueError("Duplicate legacy index path in retirement report")
        seen_paths.add(path.resolve())
        if path.is_symlink() or sha256(path) != entry["file_sha256"]:
            raise ValueError("Legacy index changed after verification")
        destination = path.with_suffix(".sqlite.retired")
        if destination.exists():
            raise ValueError(f"Retirement destination already exists: {destination}")
        prepared.append((entry, path, destination))
    for entry, path, destination in prepared:
        path.rename(destination)
        entry.update(status="retired", retired_path=str(destination))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "rebuild", "verify", "retire"))
    parser.add_argument("--legacy-dir", type=Path, action="append", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    # Plans include source headers and job metadata; never print them to logs.
    report = asyncio.run(run_migration(args.action, args.legacy_dir))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(args.report, json.dumps(report, ensure_ascii=True, indent=2).encode())
    os.chmod(args.report, 0o600)
    errors = bool(report["problems"]) or any(
        source["status"] not in ("verified", "original_verified") for source in report["sources"]
    ) or any(entry["status"] in ("inconsistent", "unassociated") for entry in report["files"])
    print(f"{len(report['sources'])} sources, {len(report['jobs'])} jobs; report: {args.report}")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
