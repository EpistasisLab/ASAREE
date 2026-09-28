"""Multipart directory uploads shared by folder-backed resources."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

MultipartFiles = list[tuple[str, tuple[str, BinaryIO]]]


@contextmanager
def multipart_directory(directory: str) -> Iterator[MultipartFiles]:
    """Open every file below *directory* and preserve its picked-folder path."""
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {directory}")
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    with ExitStack() as stack:
        files: MultipartFiles = []
        for path in paths:
            handle: BinaryIO = stack.enter_context(path.open("rb"))
            files.append(("files", (f"{root.name}/{path.relative_to(root).as_posix()}", handle)))
        yield files
