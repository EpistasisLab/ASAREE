"""Verified original rows with bounded memory and random access for batch planning."""

from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import re
import sqlite3
import tempfile
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import BinaryIO, overload
from urllib.parse import quote

from asaree.services.dataset_row_csv import DatasetRowCsvError, RowSource


class IndexedRows(Sequence[tuple[str, ...]]):
    def __init__(self, path: Path, count: int):
        self.path = path
        self._length = count

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> tuple[str, ...]: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[tuple[str, ...]]: ...

    def __getitem__(self, index: int | slice) -> tuple[str, ...] | Sequence[tuple[str, ...]]:
        if isinstance(index, slice):
            return tuple(self[item] for item in range(*index.indices(self._length)))
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError(index)
        with closing(sqlite3.connect(f"file:{quote(str(self.path))}?mode=ro", uri=True)) as connection:
            record = connection.execute("SELECT data FROM rows WHERE row_index=?", (index,)).fetchone()
        if record is None:
            raise DatasetRowCsvError("source_unavailable", "verified row index is incomplete")
        return tuple(json.loads(record[0]))


def read_indexed_row_source(
    *,
    dataset_id: str,
    raw_path: str | None,
    raw_sha256: str | None,
    cache_root: Path,
) -> RowSource:
    if raw_path is None or not isinstance(raw_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_sha256):
        raise DatasetRowCsvError("source_unavailable", "registered original CSV or SHA-256 is unavailable")
    cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_path = cache_root / f"{raw_sha256}.sqlite"
    # Serialize cache creation across workers; cached rows are immutable after
    # the atomic rename. Every job still verifies the current registered bytes.
    with (cache_root / f"{raw_sha256}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            digest = hashlib.sha256()
            with Path(raw_path).open("rb") as source, tempfile.TemporaryFile() as verified:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    if not cache_path.exists():
                        verified.write(chunk)
                if digest.hexdigest() != raw_sha256:
                    raise DatasetRowCsvError(
                        "source_hash_mismatch", "original CSV does not match its registered SHA-256"
                    )
                if not cache_path.exists():
                    verified.seek(0)
                    _build_index(verified, cache_path, raw_sha256)
            with closing(sqlite3.connect(f"file:{quote(str(cache_path))}?mode=ro", uri=True)) as connection:
                metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            if metadata["sha256"] != raw_sha256:
                raise DatasetRowCsvError("source_hash_mismatch", "verified index identity does not match original CSV")
            return RowSource(
                dataset_id,
                raw_sha256,
                tuple(json.loads(metadata["columns"])),
                IndexedRows(cache_path, int(metadata["count"])),
            )
        except (OSError, sqlite3.Error, KeyError) as exc:
            raise DatasetRowCsvError("source_unavailable", "verified original row index cannot be read") from exc


def _build_index(verified: BinaryIO, destination: Path, sha256: str) -> None:
    import io

    # The verified temporary copy makes CSV parsing and hashing refer to exactly
    # the same bytes even if the external original changes during preparation.
    wrapper = io.TextIOWrapper(verified, encoding="utf-8-sig", newline="")
    temporary = destination.with_suffix(".building")
    try:
        temporary.unlink(missing_ok=True)
        with closing(sqlite3.connect(temporary)) as connection, connection:
            connection.execute("CREATE TABLE rows(row_index INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            records = (record for record in csv.reader(wrapper, strict=True) if record)
            header = next(records, None)
            if header is None or any(not column.strip() for column in header) or len(set(header)) != len(header):
                raise DatasetRowCsvError("invalid_csv", "CSV headers must be distinct and nonempty")
            batch = []
            count = 0
            for record in records:
                if len(record) != len(header):
                    raise DatasetRowCsvError(
                        "invalid_csv", "CSV record has a different number of fields than its header"
                    )
                batch.append((count, json.dumps(record, ensure_ascii=False)))
                count += 1
                if len(batch) == 1000:
                    connection.executemany("INSERT INTO rows VALUES (?,?)", batch)
                    batch = []
            if batch:
                connection.executemany("INSERT INTO rows VALUES (?,?)", batch)
            connection.executemany(
                "INSERT INTO metadata VALUES (?,?)",
                [
                    ("sha256", sha256),
                    ("columns", json.dumps(header)),
                    ("count", str(count)),
                ],
            )
        temporary.chmod(0o600)
        temporary.replace(destination)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise DatasetRowCsvError("invalid_csv", "CSV syntax or encoding is malformed") from exc
    finally:
        wrapper.detach()
        temporary.unlink(missing_ok=True)
