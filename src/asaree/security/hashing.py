"""Content hashing for files ASAREE stores — the same guarantee agentic-core's
semantic memory and the workspace mechanism already rely on: a hash proves
what a file actually contains, not just that a path exists.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
