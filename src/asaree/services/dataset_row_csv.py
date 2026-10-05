"""Read verified original Dataset CSV rows and write selected-row snapshots."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class DatasetRowCsvError(ValueError):
    """A source CSV or requested row snapshot is invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"dataset_row.{code}: {message}")


@dataclass(frozen=True)
class RowSource:
    dataset_id: str
    raw_sha256: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def read_row_source(
    *,
    dataset_id: str,
    raw_path: str | None,
    raw_sha256: str | None,
    expected_sha256: str | None = None,
) -> RowSource:
    """Read and parse only the registered original file after verifying its bytes."""
    if raw_path is None or not isinstance(raw_sha256, str) or not _SHA256_RE.fullmatch(raw_sha256):
        raise DatasetRowCsvError("source_unavailable", "registered original CSV or SHA-256 is unavailable")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256)
    ):
        raise DatasetRowCsvError("source_hash_mismatch", "expected SHA-256 is invalid")

    try:
        original_bytes = Path(raw_path).read_bytes()
    except OSError as exc:
        raise DatasetRowCsvError("source_unavailable", "registered original CSV cannot be read") from exc

    actual_sha256 = hashlib.sha256(original_bytes).hexdigest()
    if actual_sha256 != raw_sha256 or (expected_sha256 is not None and actual_sha256 != expected_sha256):
        raise DatasetRowCsvError("source_hash_mismatch", "original CSV does not match its registered SHA-256")

    try:
        text = original_bytes.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", strict=True)
        records = (record for record in reader if record)
        header = next(records, None)
        if header is None:
            raise DatasetRowCsvError("invalid_csv", "CSV header is missing")
        if any(not column.strip() for column in header) or len(set(header)) != len(header):
            raise DatasetRowCsvError("invalid_csv", "CSV headers must be distinct and nonempty")
        rows: list[tuple[str, ...]] = []
        for record in records:
            if len(record) != len(header):
                raise DatasetRowCsvError("invalid_csv", "CSV record has a different number of fields than its header")
            rows.append(tuple(record))
    except UnicodeDecodeError as exc:
        raise DatasetRowCsvError("invalid_csv", "CSV is not valid UTF-8") from exc
    except csv.Error as exc:
        raise DatasetRowCsvError("invalid_csv", "CSV syntax is malformed") from exc

    return RowSource(dataset_id, actual_sha256, tuple(header), tuple(rows))


def project_row(source: RowSource, *, row_index: int, columns: Sequence[str]) -> dict[str, object]:
    """Return an ordered, exact-string projection of one zero-based data record."""
    if isinstance(row_index, bool) or not isinstance(row_index, int):
        raise DatasetRowCsvError("row_out_of_range", "row index must be an integer")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise DatasetRowCsvError("invalid_columns", "columns must be a nonempty sequence")
    selected = list(columns)
    if (
        not selected
        or any(not isinstance(column, str) or not column.strip() for column in selected)
        or len(set(selected)) != len(selected)
        or any(column not in source.columns for column in selected)
    ):
        raise DatasetRowCsvError("invalid_columns", "columns must be distinct existing nonempty headers")
    if row_index < 0 or row_index >= len(source.rows):
        code = "empty_source" if not source.rows else "row_out_of_range"
        raise DatasetRowCsvError(code, "row index is outside the source")
    row = source.rows[row_index]
    indices = [source.columns.index(column) for column in selected]
    return {
        "dataset_id": source.dataset_id,
        "raw_sha256": source.raw_sha256,
        "row_index": row_index,
        "columns": selected,
        "values": {column: row[index] for column, index in zip(selected, indices, strict=True)},
    }


def materialize_row_csv(view: dict, *, destination: Path) -> Path:
    """Write a one-row selected-column CSV snapshot to an explicit run path."""
    columns = view.get("columns")
    values = view.get("values")
    if (
        not isinstance(columns, list)
        or not columns
        or any(not isinstance(column, str) for column in columns)
        or len(set(columns)) != len(columns)
        or not isinstance(values, dict)
        or any(column not in values or not isinstance(values[column], str) for column in columns)
    ):
        raise DatasetRowCsvError("invalid_columns", "row view must contain distinct columns and string values")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter=",", lineterminator="\n")
            writer.writerow(columns)
            writer.writerow([values[column] for column in columns])
    except OSError as exc:
        raise DatasetRowCsvError("source_unavailable", "row snapshot destination cannot be written") from exc
    return destination
