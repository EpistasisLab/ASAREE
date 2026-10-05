from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from asaree.services.dataset_row_csv import (
    DatasetRowCsvError,
    materialize_row_csv,
    project_row,
    read_row_source,
)


def _register(path: Path, content: bytes) -> tuple[str, str]:
    path.write_bytes(content)
    return str(path), hashlib.sha256(content).hexdigest()


def test_reads_original_rows_and_projects_only_authorized_columns(tmp_path: Path) -> None:
    original = (
        'question,reference,note\n'
        '"What is\nUnicode?",,café\n'
        'duplicate,,雪\n'
        'duplicate,,雪\n'
    ).encode()
    raw_path, digest = _register(tmp_path / "source.csv", original)
    source = read_row_source(dataset_id="dataset-1", raw_path=raw_path, raw_sha256=digest, expected_sha256=digest)

    assert source.columns == ("question", "reference", "note")
    assert source.rows == (("What is\nUnicode?", "", "café"), ("duplicate", "", "雪"), ("duplicate", "", "雪"))
    first = project_row(source, row_index=1, columns=["question", "reference"])
    second = project_row(source, row_index=1, columns=["question", "note"])
    assert first["values"] == {"question": "duplicate", "reference": ""}
    assert second["values"] == {"question": "duplicate", "note": "雪"}
    assert first["row_index"] == second["row_index"] == 1


def test_hash_mutation_is_rejected(tmp_path: Path) -> None:
    raw_path, digest = _register(tmp_path / "source.csv", b"question\na\n")
    Path(raw_path).write_bytes(b"question\nb\n")

    with pytest.raises(DatasetRowCsvError) as exc_info:
        read_row_source(dataset_id="dataset-1", raw_path=raw_path, raw_sha256=digest)

    assert exc_info.value.code == "source_hash_mismatch"


def test_accepts_bom_and_ignores_blank_physical_records(tmp_path: Path) -> None:
    content = b'\xef\xbb\xbfquestion,reference\n\n"",\n\nhello,world\n'
    raw_path, digest = _register(tmp_path / "source.csv", content)
    source = read_row_source(dataset_id="dataset-1", raw_path=raw_path, raw_sha256=digest)

    assert source.columns == ("question", "reference")
    assert source.rows == (("", ""), ("hello", "world"))


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\n\n",
        b"question,question\na,b\n",
        b"question,   \na,b\n",
        b"question\n\xff\n",
        b"question,reference\na\n",
        b'question\n"unterminated\n',
    ],
)
def test_invalid_csv_sources_are_rejected(tmp_path: Path, content: bytes) -> None:
    raw_path, digest = _register(tmp_path / "source.csv", content)

    with pytest.raises(DatasetRowCsvError) as exc_info:
        read_row_source(dataset_id="dataset-1", raw_path=raw_path, raw_sha256=digest)

    assert exc_info.value.code == "invalid_csv"


def test_empty_data_source_is_valid_but_cannot_project_a_row(tmp_path: Path) -> None:
    raw_path, digest = _register(tmp_path / "source.csv", b"question\n\n")
    source = read_row_source(dataset_id="dataset-1", raw_path=raw_path, raw_sha256=digest)

    assert source.rows == ()
    with pytest.raises(DatasetRowCsvError) as exc_info:
        project_row(source, row_index=0, columns=["question"])
    assert exc_info.value.code == "empty_source"


def test_materialization_preserves_original_and_writes_exactly_one_selected_row(tmp_path: Path) -> None:
    original = b'question,reference,note\n"multi\nline",,caf\xc3\xa9\nother,x,y\n'
    raw_path, digest = _register(tmp_path / "source.csv", original)
    source = read_row_source(dataset_id="dataset-1", raw_path=raw_path, raw_sha256=digest)
    view = project_row(source, row_index=0, columns=["note", "question"])
    destination = tmp_path / "run" / "snapshot.csv"

    assert materialize_row_csv(view, destination=destination) == destination
    assert Path(raw_path).read_bytes() == original
    snapshot = read_row_source(
        dataset_id="dataset-1",
        raw_path=str(destination),
        raw_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
    )
    assert snapshot.columns == ("note", "question")
    assert snapshot.rows == (("café", "multi\nline"),)
