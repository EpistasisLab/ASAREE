import hashlib

import pytest

from asaree.services.dataset_row_csv import DatasetRowCsvError, project_row, read_row_source
from asaree.services.indexed_row_source import read_indexed_row_source


def test_indexed_rows_preserve_original_indices_strings_and_multiline_csv(tmp_path):
    source = tmp_path / "original.csv"
    raw = b'\xef\xbb\xbfquestion,answer\n"two\nlines",001\n,0\nlast,yes\n'
    source.write_bytes(raw)
    arguments = dict(dataset_id="dataset", raw_path=str(source), raw_sha256=hashlib.sha256(raw).hexdigest())
    indexed = read_indexed_row_source(**arguments, cache_root=tmp_path / "index")
    original = read_row_source(**arguments)
    assert indexed.columns == original.columns
    assert len(indexed.rows) == len(original.rows)
    for index in range(len(original.rows)):
        assert project_row(indexed, row_index=index, columns=["answer", "question"]) == project_row(
            original, row_index=index, columns=["answer", "question"]
        )
    reused = read_indexed_row_source(**arguments, cache_root=tmp_path / "index")
    assert reused.rows[2] == ("last", "yes")


def test_cached_index_does_not_bypass_original_hash_verification(tmp_path):
    source = tmp_path / "original.csv"
    raw = b"question,answer\nfirst,1\n"
    source.write_bytes(raw)
    arguments = dict(dataset_id="dataset", raw_path=str(source), raw_sha256=hashlib.sha256(raw).hexdigest())
    read_indexed_row_source(**arguments, cache_root=tmp_path / "index")
    source.write_bytes(b"question,answer\nchanged,2\n")
    with pytest.raises(DatasetRowCsvError, match="source_hash_mismatch"):
        read_indexed_row_source(**arguments, cache_root=tmp_path / "index")


def test_large_source_uses_disk_index_and_preserves_random_access(tmp_path, monkeypatch):
    source = tmp_path / "large.csv"
    raw = b"question,answer\n" + b"prompt,001\n" * 15786
    source.write_bytes(raw)
    monkeypatch.setattr(type(source), "read_bytes", lambda _: pytest.fail("must not materialize the original file"))
    indexed = read_indexed_row_source(
        dataset_id="dataset",
        raw_path=str(source),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        cache_root=tmp_path / "index",
    )
    assert len(indexed.rows) == 15786
    assert indexed.rows[15785] == ("prompt", "001")
