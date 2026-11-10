"""Application configuration for the existing artifact namespace/key contract."""
from __future__ import annotations

import json
from pathlib import Path

from asaree_workspace_core.storage import NAMESPACES, managed_reference, resolve, storage_roots

from asaree.config import get_settings


def roots() -> dict[str, Path]:
    settings = get_settings()
    return storage_roots({field: getattr(settings, field, "") for field, _ in NAMESPACES.values()})


def reference_for(path: Path) -> str:
    return managed_reference(path, roots())


def resolve_reference(value: str) -> Path:
    return resolve(value, roots() if value.startswith("managed://") else None)


def workspace_references(paths: set[Path]) -> bool:
    """Conservative protection for retained legacy and current workspace seeds.

    Unreadable state is unknown evidence, never permission to delete inputs.
    Directory names cannot establish orphan status.
    """
    def contains(value: object) -> bool:
        if isinstance(value, dict):
            return any(contains(item) for item in value.values())
        if isinstance(value, list):
            return any(contains(item) for item in value)
        if isinstance(value, str) and (value.startswith("managed://") or value.startswith("/")):
            return resolve_reference(value).resolve() in paths
        return False

    root = roots()["workspaces"]
    for state in root.rglob("state.json"):
        try:
            if contains(json.loads(state.read_text())):
                return True
        except (OSError, ValueError):
            return True
    return False
