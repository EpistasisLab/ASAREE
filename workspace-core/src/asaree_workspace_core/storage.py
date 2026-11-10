"""Filesystem half of the managed-artifact namespace/key contract.

No database or application imports: workers and MCP packages use the same
resolver. References identify locations; hashes in artifact records/manifests
identify immutable content. External paths are deliberately never adopted.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

NAMESPACES = {
    "datasets": ("dataset_storage_dir", "datasets"),
    "workspaces": ("dataset_workspace_dir", "workspaces"),
    "exports": ("result_export_dir", "result-exports"),
    "row-indexes": ("result_row_index_dir", "result-row-indexes"),
    "documents": ("okf_document_dir", "okf-documents"),
    "bundles": ("okf_bundle_upload_dir", "okf-bundles"),
    "backups": ("backup_dir", "backups"),
    "scratch": ("scratch_dir", "scratch"),
}
PREFIX = "managed://"


def storage_roots(values: Mapping[str, str] | None = None) -> dict[str, Path]:
    """Explicit values > environment > shared data root > ./data.

    Application settings pass their resolved values (including settings-file
    overrides). Standalone packages read the inherited process environment.
    """
    values = values or {}
    data = Path(values.get("data_root") or os.environ.get("ASAREE_DATA_ROOT") or "./data").expanduser().resolve()
    return {
        namespace: Path(values.get(field) or os.environ.get(f"ASAREE_{field.upper()}") or data / suffix)
        .expanduser().resolve()
        for namespace, (field, suffix) in NAMESPACES.items()
    }


def reference(namespace: str, key: str) -> str:
    if namespace not in NAMESPACES:
        raise ValueError(f"Unknown storage namespace: {namespace}")
    parts = PurePosixPath(key)
    if not key or parts.is_absolute() or ".." in parts.parts or "\\" in key or key != parts.as_posix():
        raise ValueError("Invalid managed storage key")
    return f"{PREFIX}{namespace}/{key}"


def resolve(value: str, roots: Mapping[str, Path] | None = None) -> Path:
    if not value.startswith(PREFIX):
        return Path(value).expanduser()  # supported legacy/external reference
    namespace, separator, key = value[len(PREFIX):].partition("/")
    if not separator:
        raise ValueError("Managed reference has no key")
    reference(namespace, key)
    root = (roots or storage_roots())[namespace].resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Managed reference escapes storage root")
    return path


def managed_reference(path: Path, roots: Mapping[str, Path] | None = None) -> str:
    """Encode a known managed path; leave external ownership intact."""
    path = path.expanduser().resolve()
    # Prefer the deepest override when roots overlap.
    ordered = sorted((roots or storage_roots()).items(), key=lambda item: len(item[1].parts), reverse=True)
    for namespace, root in ordered:
        if path != root and path.is_relative_to(root):
            return reference(namespace, path.relative_to(root).as_posix())
    return str(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes, *, overwrite: bool = True) -> None:
    """Publish complete bytes with a unique sibling staging file and fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".preparing", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.stat().st_size != len(data) or sha256(temporary) != hashlib.sha256(data).hexdigest():
            raise OSError("Prepared file failed integrity verification")
        if overwrite:
            temporary.replace(path)
        else:
            os.link(temporary, path)  # complete publication, refusing an existing identity
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def map_workspace_paths(document, transform):
    """Map only version-record locators; payloads and provenance stay opaque."""
    if not isinstance(document, dict):
        return document
    result = dict(document)
    if isinstance(document.get("slots"), dict):
        result["slots"] = {key: map_workspace_paths(slot, transform)
                           for key, slot in document["slots"].items()}
    elif isinstance(document.get("versions"), list):
        result["versions"] = [
            {key: transform(value) if key in {"train", "test", "manifest_path"}
             and isinstance(value, str) and value else value for key, value in version.items()}
            if isinstance(version, dict) else version for version in document["versions"]
        ]
    return result


def map_ambient_paths(ambient, transform):
    """Map ASAREE's documented ambient locators, preserving opaque metadata."""
    if not isinstance(ambient, dict):
        return ambient
    result = dict(ambient)
    for key in ("script_path", "data_path"):
        if isinstance(result.get(key), str) and result[key]:
            result[key] = transform(result[key])
    for key in ("script_paths", "row_inputs"):
        if isinstance(ambient.get(key), list):
            result[key] = [dict(item, path=transform(item["path"]))
                           if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]
                           else item for item in ambient[key]]
    if isinstance(ambient.get("data_slots"), dict):
        result["data_slots"] = {
            key: dict(item, data_path=transform(item["data_path"]))
            if isinstance(item, dict) and isinstance(item.get("data_path"), str) and item["data_path"]
            else item for key, item in ambient["data_slots"].items()
        }
    return result
