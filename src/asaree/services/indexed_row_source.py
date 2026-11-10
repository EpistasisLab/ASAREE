"""Transactional PostgreSQL indexes and bounded asynchronous original-row reads."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import tempfile
import uuid
from collections.abc import Generator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, BinaryIO, Literal, overload

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.dataset import RegisteredDataset
from asaree.models.dataset_row_index import DatasetIndexedRow, DatasetRowIndex
from asaree.services.dataset_row_csv import DatasetRowCsvError, RowSource

PARSER_VERSION = "csv-utf8-sig-strict-v1"
BATCH_SIZE = 1000
_VERIFICATION_LEASE: ContextVar[dict[tuple[str, str], tuple[int, ...]] | None] = ContextVar(
    "row_source_verification", default=None,
)


@contextmanager
def verification_scope() -> Iterator[None]:
    """Short-lived evidence for one job's windows in one worker tick only."""
    token = _VERIFICATION_LEASE.set({})
    try:
        yield
    finally:
        _VERIFICATION_LEASE.reset(token)


def source_fingerprint(path: Path) -> tuple[int, ...]:
    status = path.stat()
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def encode_strings(strings: Sequence[str]) -> bytes:
    # BYTEA, not JSONB/text: both headers and values may contain NUL.
    return json.dumps(list(strings), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_strings(data: bytes) -> tuple[str, ...]:
    values = json.loads(data)
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise DatasetRowCsvError("source_unavailable", "invalid indexed CSV string payload")
    return tuple(values)


class LoadedRows(Sequence[tuple[str, ...]]):
    """A bounded sparse row window. Indexing never performs hidden database I/O."""

    def __init__(self, count: int, values: dict[int, tuple[str, ...]] | None = None) -> None:
        self._length = count
        self.values = values or {}

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
        if index not in self.values:
            raise DatasetRowCsvError("source_unavailable", "load rows in an asynchronous batch before projection")
        return self.values[index]


@dataclass(frozen=True)
class IndexedRowSource(RowSource):
    owner_id: uuid.UUID
    index_id: uuid.UUID
    parser_version: str = PARSER_VERSION

    def descriptor(self) -> dict[str, Any]:
        # User strings stay out of JSONB job progress.
        return {"dataset_id": self.dataset_id, "sha256": self.raw_sha256,
                "parser_version": self.parser_version, "count": len(self.rows)}

    async def fetch_rows(self, db: AsyncSession, indices: Iterable[int]) -> IndexedRowSource:
        selected = list(islice(indices, BATCH_SIZE + 1))
        if len(selected) > BATCH_SIZE:
            raise ValueError(f"A row read may contain at most {BATCH_SIZE} positions")
        if any(isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(self.rows) for i in selected):
            raise DatasetRowCsvError("empty_source" if not self.rows else "row_out_of_range",
                                     "row index is outside the source")
        selected = list(dict.fromkeys(selected))
        cached = self.rows.values if isinstance(self.rows, LoadedRows) else {}
        missing = [i for i in selected if i not in cached]
        values = {i: cached[i] for i in selected if i in cached}
        if missing:
            query = select(DatasetIndexedRow.row_index, DatasetIndexedRow.data).join(
                DatasetRowIndex, DatasetRowIndex.id == DatasetIndexedRow.index_id
            ).join(RegisteredDataset, RegisteredDataset.id == DatasetRowIndex.dataset_id).where(
                DatasetRowIndex.id == self.index_id,
                RegisteredDataset.id == uuid.UUID(self.dataset_id),
                RegisteredDataset.owner_id == self.owner_id,
                RegisteredDataset.raw_sha256 == self.raw_sha256,
                DatasetRowIndex.raw_sha256 == self.raw_sha256,
                DatasetRowIndex.parser_version == self.parser_version,
                DatasetIndexedRow.row_index.in_(missing),
            )
            for index, data in await db.execute(query):
                row = decode_strings(data)
                if len(row) != len(self.columns):
                    raise DatasetRowCsvError("source_unavailable", "verified row index is inconsistent")
                values[index] = row
        if len(values) != len(selected):
            raise DatasetRowCsvError("source_unavailable", "verified row index is incomplete or inaccessible")
        return IndexedRowSource(self.dataset_id, self.raw_sha256, self.columns,
                                LoadedRows(len(self.rows), values), self.owner_id, self.index_id, self.parser_version)

    async def fetch_range(self, db: AsyncSession, start: int, stop: int) -> IndexedRowSource:
        if start < 0 or stop < start or stop > len(self.rows):
            raise DatasetRowCsvError("row_out_of_range", "row range is outside the source")
        return await self.fetch_rows(db, range(start, stop))


@overload
def verify_original(raw_path: str | None, raw_sha256: str | None, *, copy: Literal[True] = True) -> BinaryIO: ...


@overload
def verify_original(raw_path: str | None, raw_sha256: str | None, *, copy: Literal[False]) -> None: ...


@overload
def verify_original(raw_path: str | None, raw_sha256: str | None, *, copy: bool) -> BinaryIO | None: ...


def verify_original(raw_path: str | None, raw_sha256: str | None, *, copy: bool = True) -> BinaryIO | None:
    if raw_path is None or not isinstance(raw_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_sha256):
        raise DatasetRowCsvError("source_unavailable", "registered original CSV or SHA-256 is unavailable")
    verified = tempfile.TemporaryFile() if copy else None  # noqa: SIM115 - ownership transfers to caller
    try:
        path = Path(raw_path)
        before = source_fingerprint(path)
        cache_key = (str(path.resolve()), raw_sha256)
        evidence = _VERIFICATION_LEASE.get()
        # A new job/tick/process always hashes. Within a tick a replacement,
        # size, modification or change-time update invalidates this evidence.
        if not copy and evidence is not None and evidence.get(cache_key) == before:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                if verified is not None:
                    verified.write(chunk)
        if digest.hexdigest() != raw_sha256 or source_fingerprint(path) != before:
            raise DatasetRowCsvError("source_hash_mismatch", "original CSV does not match its registered SHA-256")
        if evidence is not None:
            evidence[cache_key] = before
        if verified is not None:
            verified.seek(0)
        return verified
    except BaseException as exc:
        if verified is not None:
            verified.close()
        if isinstance(exc, OSError):
            raise DatasetRowCsvError("source_unavailable", "registered original CSV cannot be read") from exc
        raise


def csv_records(verified: BinaryIO) -> Generator[tuple[str, ...], None, None]:
    """The v1 parser retains existing rules, including skipping empty records."""
    wrapper = io.TextIOWrapper(verified, encoding="utf-8-sig", newline="")
    try:
        records = (record for record in csv.reader(wrapper, strict=True) if record)
        header = next(records, None)
        if header is None or any(not column.strip() for column in header) or len(set(header)) != len(header):
            raise DatasetRowCsvError("invalid_csv", "CSV headers must be distinct and nonempty")
        yield tuple(header)
        for record in records:
            if len(record) != len(header):
                raise DatasetRowCsvError("invalid_csv", "CSV record has a different number of fields than its header")
            yield tuple(record)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise DatasetRowCsvError("invalid_csv", "CSV syntax or encoding is malformed") from exc
    finally:
        wrapper.detach()


def next_batch[T](records: Iterator[T], limit: int = BATCH_SIZE) -> list[T]:
    return list(islice(records, limit))


async def read_indexed_row_source(
    db: AsyncSession, *, dataset_id: uuid.UUID, owner_id: uuid.UUID,
    expected_sha256: str | None = None, parser_version: str = PARSER_VERSION,
) -> IndexedRowSource:
    if parser_version != PARSER_VERSION:
        raise DatasetRowCsvError("source_unavailable", "unsupported saved CSV parser version")
    dataset = await db.scalar(select(RegisteredDataset).where(
        RegisteredDataset.id == dataset_id, RegisteredDataset.owner_id == owner_id,
    ).with_for_update(read=True, key_share=True, of=RegisteredDataset))
    if dataset is None:
        raise DatasetRowCsvError("source_unavailable", "registered row source is unavailable")
    if expected_sha256 is not None and dataset.raw_sha256 != expected_sha256:
        raise DatasetRowCsvError("source_hash_mismatch", "saved source identity does not match registration")
    query = select(DatasetRowIndex).where(
        DatasetRowIndex.dataset_id == dataset_id, DatasetRowIndex.raw_sha256 == dataset.raw_sha256,
        DatasetRowIndex.parser_version == parser_version,
    ).execution_options(populate_existing=True)
    metadata = await db.scalar(query)
    # Copy only when parsing is needed. Ready-index verification scans bytes
    # once per short-lived job tick/fingerprint and never writes a second file.
    verified = await asyncio.to_thread(
        verify_original, dataset.raw_path, dataset.raw_sha256, copy=metadata is None,
    )
    try:
        if metadata is not None:
            return IndexedRowSource(str(dataset_id), metadata.raw_sha256, decode_strings(metadata.columns_data),
                                    LoadedRows(metadata.row_count), owner_id, metadata.id, parser_version)
        async with db.begin_nested():
            await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(
                f"csv-index:{dataset_id}:{dataset.raw_sha256}:{parser_version}", 0
            ))))
            metadata = await db.scalar(query)
            if metadata is None:
                if verified is None:
                    verified = await asyncio.to_thread(verify_original, dataset.raw_path, dataset.raw_sha256)
                assert verified is not None
                records = csv_records(verified)
                try:
                    columns = next(records)
                    metadata = DatasetRowIndex(dataset_id=dataset_id, raw_sha256=dataset.raw_sha256,
                                               parser_version=parser_version, columns_data=encode_strings(columns),
                                               row_count=0)
                    db.add(metadata)
                    await db.flush()
                    count = 0
                    while batch := await asyncio.to_thread(next_batch, records):
                        await db.execute(insert(DatasetIndexedRow), [
                            dict(index_id=metadata.id, row_index=count + i, data=encode_strings(row))
                            for i, row in enumerate(batch)
                        ])
                        count += len(batch)
                    metadata.row_count = count
                    await db.flush()
                finally:
                    records.close()
            columns = decode_strings(metadata.columns_data)
            return IndexedRowSource(str(dataset_id), metadata.raw_sha256, columns,
                                    LoadedRows(metadata.row_count), owner_id, metadata.id, parser_version)
    finally:
        if verified is not None:
            verified.close()
