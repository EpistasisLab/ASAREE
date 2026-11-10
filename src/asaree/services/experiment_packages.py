"""Portable logical publication exports, never workspace backups or resume images.

Inventories live in JSONL files, not lists. Database evidence is sliced in SQL;
tar member objects are discarded after writing. Only schema-owned locators are
interpreted. Prompts, scripts, dataset values and result payloads remain opaque.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import importlib.metadata
import json
import os
import re
import stat
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from motoro.models.database import system_session
from motoro.models.mcp_server import MCPServerConfig
from motoro.models.skill import Skill, SkillFile
from motoro.services.skill_service import render_skill_md
from sqlalchemy import Text, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from asaree.models.database import get_session
from asaree.models.dataset import RegisteredDataset
from asaree.models.dataset_split import DatasetArtifact, DatasetSplit
from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_artifact import ExperimentArtifact
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.result_job import ResultJob
from asaree.services import managed_storage, okf_bundles, okf_documents

BLOCK = 1024 * 1024
SQL_BLOCK = 16384
SECRET_FIELDS = frozenset({
    "api_key", "api_token", "access_token", "refresh_token", "password", "secret", "client_secret",
    "authorization", "headers", "env", "environment", "stdio_env", "credentials", "connection_string",
    "api_base", "base_url", "endpoint", "url", "raw_path", "train_path", "test_path",
})


def encoded(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")) + "\n").encode()


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or not path.parts or path.is_absolute() or ".." in path.parts
        or "\\" in value or path.as_posix() != value):
        raise ValueError("Unsafe package member path")
    return value


def credential_file(value: str) -> bool:
    """File identities, not content heuristics: never scan or rewrite user bytes."""
    return any(
        part.lower() in {".env", "credentials.json", "credentials", ".netrc", ".npmrc", "id_rsa", "id_ed25519"}
        or part.lower().startswith(".env.") or part.lower().endswith((".pem", ".key", ".p12", ".pfx"))
        for part in PurePosixPath(value).parts
    )


def open_resource(source: Path) -> Any:
    """Open through directory descriptors: reject symlink races at every segment."""
    source = source.absolute()
    parts = source.parts
    descriptor = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        result = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        return os.fdopen(result, "rb")
    finally:
        os.close(descriptor)


def safe_config(config: Any) -> Any:
    if not isinstance(config, dict):
        return deepcopy(config)
    result = {
        key: safe_config(value) if key in {"model_config", "model_config_data", "connection"}
        else [safe_config(model) for model in value] if key == "fallback_models" and isinstance(value, list)
        else deepcopy(value)
        for key, value in config.items() if key.lower() not in SECRET_FIELDS
    }
    for path_key, id_key in (("bundle_path", "bundle_id"), ("document_path", "document_id")):
        if path_key in result:
            try:
                identity = str(uuid.UUID(str(result.get(id_key))))
                result[path_key] = f"resources/knowledge/{identity}"
            except (ValueError, TypeError, AttributeError):
                result[path_key] = None
    return result


def safe_sampling_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(snapshot)
    for key in ("input", "config", "explicit_input", "discovery_context"):
        if key in result:
            result[key] = safe_config(result[key])
    result["fallbacks"] = [safe_sampling_snapshot(value) for value in result.get("fallbacks") or []]
    return result


def portable_definition(graph: dict[str, Any], settings: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove typed credential fields, including whole-config factor levels.

    A literal argument named `password` or a dataset column named `api_key` is
    experimental content; only configuration fields have credential semantics.
    """
    graph, settings = deepcopy(graph), deepcopy(settings)
    if isinstance(graph.get("sampling_snapshot"), dict):
        snapshots = {}
        for snapshot in graph["sampling_snapshot"].values():
            clean = safe_sampling_snapshot(snapshot)
            # Archive keys describe credential-free content. These are logical
            # snapshots, not requests to the runtime's credential-bearing resolver.
            key = hashlib.sha256(encoded(clean.get("explicit_input", clean.get("input")))).hexdigest()
            snapshots[key] = clean
        graph["sampling_snapshot"] = snapshots
    config_factors: set[str] = set()
    fallback_factors: set[str] = set()
    secret_factors: set[str] = set()
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        data["config"] = safe_config(data.get("config") or {})
        for path, name in (data.get("factor_bindings") or {}).items():
            if path.split(".")[-1].lower() in SECRET_FIELDS:
                secret_factors.add(name)
            elif path.split(".")[-1] == "fallback_models":
                fallback_factors.add(name)
            elif path in {"config", "model_config_data", "config.model_config_data", "config.model_config"}:
                config_factors.add(name)
    for factor in (settings.get("design_spec") or {}).get("factors") or []:
        if factor.get("name") in secret_factors:
            factor["levels"] = [None for _ in factor.get("levels") or []]
        elif factor.get("name") in config_factors:
            factor["levels"] = [safe_config(level) for level in factor.get("levels") or []]
        elif factor.get("name") in fallback_factors:
            factor["levels"] = [[safe_config(model) for model in level] for level in factor.get("levels") or []]
    return graph, settings


def safe_factor_values(values: Any, graph: dict[str, Any]) -> Any:
    """Generated cells and attempts obey the same typed credential boundary."""
    if not isinstance(values, dict):
        return values
    result = deepcopy(values)
    for node in graph.get("nodes") or []:
        for path, name in (node.get("data") or {}).get("factor_bindings", {}).items():
            if name not in result:
                continue
            if path.split(".")[-1].lower() in SECRET_FIELDS:
                result[name] = None
            elif path.split(".")[-1] == "fallback_models" and isinstance(result[name], list):
                result[name] = [safe_config(model) for model in result[name]]
            elif path in {"config", "model_config_data", "config.model_config_data", "config.model_config"}:
                result[name] = safe_config(result[name])
    return result


def node_variants(node: dict[str, Any], design: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    """Union individual bound alternatives in O(sum levels), never combinations.

    Resource identities live in one field or whole config. Other factors only
    modify unrelated settings; combining them adds no dependency to the union.
    The full declaration and frozen sampling combinations remain in the package.
    """
    from asaree.services.protocol_execution import apply_factor_bindings

    factors = {f["name"]: f.get("levels") or [] for f in (design or {}).get("factors") or []}
    names = list(dict.fromkeys((node.get("data") or {}).get("factor_bindings", {}).values()))
    yield node
    for name in names:
        if not factors.get(name):
            continue
        for value in factors[name]:
            graph = apply_factor_bindings({"nodes": [node], "edges": []}, {name: value})
            yield graph["nodes"][0]


class PackageWriter:
    """Disk-backed inventory and constant-buffer tar writer.

    TarFile normally retains every TarInfo even in stream mode. Clearing its
    members is deliberate: this writer never seeks, appends, or calls getmember.
    """

    def __init__(self, destination: Path, staging: Path):
        self.staging = staging
        self.output = destination.open("wb")
        destination.chmod(0o600)
        self.compressor = gzip.GzipFile(filename="", mode="wb", fileobj=self.output, mtime=0)
        self.tar = tarfile.open(fileobj=self.compressor, mode="w|", format=tarfile.PAX_FORMAT)  # noqa: SIM115
        self.inventory = (staging / "files.jsonl").open("wb")
        self.dependencies = (staging / "dependencies.jsonl").open("wb")
        self.missing = 0
        self.count = 0
        self.stopping = threading.Event()
        self.checkpoint: Any = None

    def check(self) -> None:
        if self.stopping.is_set():
            raise ValueError("Package capture stopped")

    def dependency(
        self, identity: str, kind: str, provenance: str, *, status: str = "included", **details: Any,
    ) -> None:
        self.dependencies.write(encoded({"id": identity, "kind": kind, "provenance": provenance,
                                         "status": status, **details}))
        if status in {"missing", "unresolved", "changed", "forbidden"}:
            self.missing += 1

    def seen(self, kind: str, identity: str) -> bool:
        # Markers bound dedup memory independently of total resources/attempts.
        marker = self.staging / "seen" / hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()
        marker.parent.mkdir(exist_ok=True)
        try:
            marker.touch(exist_ok=False)
            return False
        except FileExistsError:
            return True

    def add(self, path: Path, name: str, *, provenance: str, identity: str, inventory: bool = True) -> dict[str, Any]:
        relative_path(name)
        if self.seen("archive_member", name):
            raise ValueError("Duplicate package member")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(BLOCK), b""):
                self.check()
                digest.update(block)
            size = stream.tell()
            stream.seek(0)
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = size, 0o600, 0
            check = self.check

            class Reader:
                def read(self, size: int = -1, /) -> bytes:
                    check()
                    return stream.read(size)

            self.tar.addfile(info, Reader())
            self.tar.members.clear()  # type: ignore[attr-defined]  # Runtime cache omitted by typeshed.
        record = {"path": name, "sha256": digest.hexdigest(), "bytes": size,
                  "source_id": identity, "provenance": provenance}
        if inventory:
            self.inventory.write(encoded(record))
            self.count += 1
        return record

    def json(self, name: str, value: Any, *, provenance: str = "publication_pinned", identity: str = "") -> None:
        path = self.staging / "document"
        # iterencode avoids a second whole-document string allocation.
        with path.open("w", encoding="utf-8") as stream:
            for block in json.JSONEncoder(sort_keys=True, ensure_ascii=False, default=str).iterencode(value):
                self.check()
                stream.write(block)
            stream.write("\n")
        self.add(path, name, provenance=provenance, identity=identity)

    def file(self, source: Path, name: str, *, identity: str, provenance: str,
             expected: str | None = None) -> dict[str, Any]:
        """Copy once to private staging; verify opened identity and expected bytes."""
        target = self.staging / "content"
        digest = hashlib.sha256()
        with open_resource(source) as input_file, target.open("wb") as output:
            before = os.fstat(input_file.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Not a regular resource file")
            for block in iter(lambda: input_file.read(BLOCK), b""):
                self.check()
                output.write(block)
                digest.update(block)
            after = os.fstat(input_file.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ) or (expected is not None and digest.hexdigest() != expected):
            raise ValueError("Resource changed or hash mismatch")
        return self.add(target, name, provenance=provenance, identity=identity)

    def finish(self, manifest: dict[str, Any]) -> dict[str, Any]:
        self.dependencies.close()
        dependency_index = self.add(self.staging / "dependencies.jsonl", "dependencies.jsonl",
                                    provenance="export_inventory", identity="dependencies")
        self.inventory.close()
        file_index = self.add(self.staging / "files.jsonl", "files.jsonl", provenance="export_inventory",
                              identity="files", inventory=False)
        manifest.update(files=file_index, dependencies=dependency_index, file_count=self.count,
                        completeness={"status": "incomplete" if self.missing else "complete",
                                      "unresolved_count": self.missing})
        path = self.staging / "manifest.json"
        path.write_bytes(encoded(manifest))
        self.add(path, "manifest.json", provenance="export_inventory", identity="manifest", inventory=False)
        self.close()
        return manifest

    def close(self) -> None:
        self.inventory.close()
        self.dependencies.close()
        self.tar.close()
        self.compressor.close()
        if not self.output.closed:
            self.output.flush()
            os.fsync(self.output.fileno())
            self.output.close()


async def archive_io(writer: PackageWriter, operation: Any, *args: Any, **kwargs: Any) -> Any:
    """One joined writer task; HTTP cancellation cannot orphan a copying thread."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.5)
            if writer.checkpoint is not None:
                await writer.checkpoint()
        return await task
    except BaseException:
        writer.stopping.set()
        with suppress(BaseException):
            await asyncio.shield(task)
        raise


def archive_hash(writer: PackageWriter, path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK), b""):
            writer.check()
            digest.update(block)
    return digest.hexdigest()


async def _datasets(db: AsyncSession, writer: PackageWriter, publication: ProtocolRevision, owner: uuid.UUID) -> None:
    pins = publication.dataset_inputs
    if pins is None:
        writer.dependency(str(publication.id), "dataset_inputs", "legacy.unknown", status="unresolved",
                          reason="Publication did not record input identities; current selections are not substituted.")
        return None
    await archive_io(writer, writer.json, "inputs/bindings.json", pins, identity=str(publication.id))
    for pin in pins.get("inputs") or []:
        identity = pin["dataset_id"]
        registration = await db.scalar(select(RegisteredDataset).where(
            RegisteredDataset.id == uuid.UUID(identity), RegisteredDataset.owner_id == owner,
        ))
        if registration is None or registration.raw_sha256 != pin.get("raw_sha256"):
            writer.dependency(identity, "dataset", "publication_pinned", status="missing",
                              reason="Pinned owned source unavailable or identity differs.")
            continue
        if not writer.seen("dataset_registration", identity):
            await archive_io(writer, writer.json, f"inputs/datasets/{identity}.json", {
                "id": identity, "name": registration.name, "description": registration.description,
                "dictionary_json": registration.dictionary_json, "target_column": registration._target_column,
                "raw_sha256": registration.raw_sha256, "raw_artifact_id": registration.raw_artifact_id,
                "created_at": registration.created_at,
            }, identity=identity)
        if pin.get("split_id") and not writer.seen("split", pin["split_id"]):
            split = await db.scalar(select(DatasetSplit).where(
                DatasetSplit.id == uuid.UUID(pin["split_id"]), DatasetSplit.dataset_id == registration.id,
            ))
            if split is None:
                writer.dependency(pin["split_id"], "split", "publication_pinned", status="missing")
            elif (split.source_sha256 != pin.get("raw_sha256")
                  or str(split.train_artifact_id) != (pin.get("train_artifact") or {}).get("id")
                  or str(split.test_artifact_id) != (pin.get("test_artifact") or {}).get("id")):
                writer.dependency(pin["split_id"], "split", "publication_pinned", status="missing",
                                  reason="Split identity differs from publication pins.")
            else:
                await archive_io(writer, writer.json, f"inputs/splits/{split.id}.json", {
                    key: getattr(split, key) for key in (
                        "id", "dataset_id", "source_sha256", "train_artifact_id", "test_artifact_id", "target_column",
                        "method", "strategy", "group_column", "stratify_column", "test_size", "seed", "legacy",
                        "created_at",
                    )
                }, identity=str(split.id))
        for key in ("raw_artifact", "train_artifact", "test_artifact"):
            reference = pin.get(key)
            artifact_id = reference["id"] if reference else f"{identity}:original"
            if reference is None and key != "raw_artifact" or writer.seen("dataset_file", artifact_id):
                continue
            artifact = await db.get(DatasetArtifact, uuid.UUID(reference["id"])) if reference else None
            expected = reference.get("sha256") if reference else pin.get("raw_sha256")
            if not expected or (reference and (
                artifact is None or artifact.dataset_id != registration.id or artifact.status != "ready"
                or artifact.sha256 != expected
            )):
                writer.dependency(artifact_id, "dataset_file", "publication_pinned", status="missing")
                continue
            name = f"inputs/files/{artifact_id.replace(':', '-')}.{artifact.format if artifact else 'csv'}"
            try:
                raw_path = artifact.path if artifact else registration.raw_path
                if not raw_path:
                    raise ValueError("Missing pinned source locator")
                path = Path(raw_path)
                record = await archive_io(writer, writer.file, path, name, identity=artifact_id,
                                          provenance="publication_pinned", expected=expected)
                writer.dependency(artifact_id, "dataset_file", "publication_pinned", file=record["path"],
                                  role=key, dataset_id=identity)
            except (OSError, ValueError):
                writer.dependency(artifact_id, "dataset_file", "publication_pinned", status="missing",
                                  reason="Pinned file unavailable or hash mismatch; no replacement used.")


async def _skill(db: AsyncSession, writer: PackageWriter, identity: str, owner: uuid.UUID) -> None:
    if writer.seen("skill", identity):
        return None
    try:
        skill = await db.scalar(select(Skill).options(noload(Skill.files)).where(
                                                    Skill.id == uuid.UUID(identity), Skill.deleted_at.is_(None),
                                                    (Skill.owner_id == owner) | Skill.is_system.is_(True)))
    except (ValueError, TypeError):
        skill = None
    if skill is None:
        writer.dependency(identity, "skill", "captured_at_download", status="missing")
        return
    directory = f"resources/skills/{skill.id}"
    await archive_io(writer, writer.json, f"resources/skill-metadata/{skill.id}.json", {
        key: getattr(skill, key) for key in (
            "id", "name", "description", "body", "frontmatter", "source_filename", "created_at", "updated_at",
        )
    }, provenance="captured_at_download", identity=identity)
    document = writer.staging / "skill.md"
    document.write_text(render_skill_md(skill), encoding="utf-8")
    await archive_io(writer, writer.add, document, f"{directory}/SKILL.md",
                     provenance="captured_at_download", identity=identity)
    # Query files explicitly instead of materializing the default selectin relationship.
    query = select(SkillFile.path, SkillFile.content, SkillFile.encoding, SkillFile.id, SkillFile.position).where(
        SkillFile.skill_id == skill.id,
    ).order_by(SkillFile.position, SkillFile.id)
    rows = await db.stream(query.execution_options(yield_per=1))
    async for path, content, encoding, file_id, position in rows:
        try:
            relative_path(path)
            if encoding not in {"utf-8", "base64"}:
                raise ValueError("Unsupported skill resource encoding")
            if credential_file(path):
                writer.dependency(str(file_id), "skill_file", "captured_at_download", status="excluded",
                                  reason="Credential file excluded.")
                continue
            document.write_bytes(base64.b64decode(content, validate=True) if encoding == "base64" else content.encode())
            await archive_io(writer, writer.add, document, f"{directory}/{path}",
                             provenance="captured_at_download", identity=str(file_id))
            writer.dependency(str(file_id), "skill_file", "captured_at_download", skill_id=identity,
                              file=f"{directory}/{path}", relative_path=path, encoding=encoding, position=position)
        except (ValueError, OSError):
            writer.dependency(str(file_id), "skill_file", "captured_at_download", status="unresolved",
                              reason="Invalid resource path or encoding.")
    writer.dependency(identity, "skill", "captured_at_download", directory=directory, updated_at=skill.updated_at,
                      note="Publication retained identity, not historical library content.")


def _tree_files(root: Path) -> Iterator[tuple[str, Path, os.stat_result]]:
    """Bounded traversal; no followlinks, including internal symlinks."""
    def visit(directory: Path) -> Iterator[tuple[str, Path, os.stat_result]]:
        with os.scandir(directory) as children:
            for child in children:
                path = Path(child.path)
                relative = path.relative_to(root).as_posix()
                if credential_file(relative):
                    yield relative, path, child.stat(follow_symlinks=False)
                elif child.is_symlink():
                    raise ValueError("Knowledge resource contains a symlink")
                elif child.is_dir(follow_symlinks=False):
                    yield from visit(path)
                else:
                    yield relative, path, child.stat(follow_symlinks=False)
    yield from visit(root)


def capture_tree(writer: PackageWriter, root: Path, directory: str, identity: str) -> None:
    """Verify copied content plus a second tree identity inventory, using disk."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Knowledge directory unavailable")
    root = root.resolve()
    inventory = writer.staging / "tree.jsonl"
    with inventory.open("wb") as output:
        for relative, path, info in _tree_files(root):
            if credential_file(relative):
                writer.dependency(identity, "knowledge_file", "captured_at_download", status="excluded",
                                  reason="Credential file excluded.", relative_path=relative)
                continue
            if not stat.S_ISREG(info.st_mode) or not path.resolve().is_relative_to(root):
                raise ValueError("Unsafe knowledge resource")
            record = writer.file(path, f"{directory}/{relative}", identity=identity, provenance="captured_at_download")
            output.write(encoded({"path": relative, "inode": info.st_ino, "size": info.st_size,
                                  "mtime": info.st_mtime_ns, "ctime": info.st_ctime_ns, "sha256": record["sha256"]}))
    # Directory entry order is stable within this capture; changes fail closed.
    with inventory.open("rb") as source:
        for relative, _path, info in _tree_files(root):
            if credential_file(relative):
                continue
            line = source.readline()
            if not line:
                raise ValueError("Knowledge files added during capture")
            record = json.loads(line)
            if (record["path"], record["inode"], record["size"], record["mtime"], record["ctime"]) != (
                relative, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
            ):
                raise ValueError("Knowledge changed during capture")
        if source.read(1):
            raise ValueError("Knowledge files removed during capture")


async def _server(db: AsyncSession, writer: PackageWriter, config: dict[str, Any], owner: uuid.UUID,
                  *, knowledge: bool = False) -> None:
    identity = config.get("bundle_id") or config.get("document_id") or config.get("server_id")
    name = config.get("server_name")
    lookup = str(identity or name or "unconfigured")
    if writer.seen("server_lookup", f"{knowledge}:{lookup}"):
        return
    query = select(MCPServerConfig).where(
        (MCPServerConfig.owner_id == owner) | MCPServerConfig.is_system.is_(True),
    )
    try:
        query = query.where(MCPServerConfig.id == uuid.UUID(identity)) if identity else query.where(
            MCPServerConfig.name == name,
        )
        server = await db.scalar(query)
    except (ValueError, TypeError):
        server = None
    if server is None or (knowledge and server.owner_id != owner):
        writer.dependency(lookup, "knowledge" if knowledge else "mcp", "captured_at_download", status="missing")
        return
    server_id = str(server.id)
    # Raw commands/URLs may embed arbitrary credentials. Keep portable tool
    # requirements, never a connection secret or deployment-specific command.
    if not writer.seen("mcp_requirement", server_id):
        await archive_io(writer, writer.json, f"requirements/mcp/{server_id}.json", {
            "id": server_id, "name": server.name, "transport": server.transport.value,
            "tool_names": [t.get("name") for t in (server.capabilities or {}).get("tools", []) if isinstance(t, dict)],
            "configuration_required": True, "connection_material": "excluded",
        }, provenance="captured_at_download", identity=server_id)
        writer.dependency(server_id, "mcp", "captured_at_download", name=server.name, external_requirement=True,
                          note="Connection configuration and credentials must be supplied separately.")
    if knowledge and not writer.seen("knowledge_content", server_id):
        directory = f"resources/knowledge/{server_id}"
        try:
            raw = okf_bundles.bundle_path_from_command(server.command)
            if raw is None:
                raise ValueError("Missing knowledge directory")
            root = Path(raw)
            # Registration ownership grants access, never infer ownership from
            # filesystem containment. External bundles also stay inside root.
            if server.name.startswith(okf_bundles.BUNDLE_SERVER_NAME_PREFIX):
                managed = bool(server.command and "managed://bundles/" in server.command)
                allowed = okf_bundles.owner_upload_root(owner) if managed else okf_bundles.bundle_root()
            elif okf_documents.is_document_server(server):
                allowed = okf_documents.owner_root(owner)
            else:
                raise ValueError("Unsupported knowledge registration")
            # Old absolute commands still permit downloads from this owner's
            # known knowledge folders. This does not adopt or delete their files.
            owner_storage = next((candidate for candidate in (
                okf_bundles.owner_upload_root(owner), okf_documents.owner_root(owner),
            ) if root.resolve().is_relative_to(candidate.resolve())), None)
            validate_knowledge_root(root, owner_storage or allowed, external=owner_storage is None)
            await archive_io(writer, capture_tree, writer, root, directory, server_id)
            writer.dependency(server_id, "knowledge", "captured_at_download", directory=directory,
                              note="Mutable content captured now; no historical snapshot or cross-file transaction.")
        except (OSError, ValueError):
            writer.dependency(server_id, "knowledge", "captured_at_download", status="unresolved",
                              reason="Unavailable, unauthorized, unsafe or concurrently changed knowledge content.")


def validate_knowledge_root(root: Path, allowed: Path, *, external: bool) -> None:
    """Owned registration is necessary, but cannot grant a platform-root dump.

    Legacy external bundles outside today's browse root remain exportable when
    isolated from platform storage. Never infer external ownership from location.
    """
    resolved = root.resolve()
    roots = managed_storage.roots()
    if root.is_symlink():
        raise ValueError("Knowledge root is a symlink")
    if not external and not resolved.is_relative_to(allowed.resolve()):
        raise ValueError("Knowledge directory outside owner storage")
    for namespace, platform_root in roots.items():
        platform_root = platform_root.resolve()
        if namespace in {"bundles", "documents"} and not external:
            continue
        if resolved == platform_root or resolved in platform_root.parents or platform_root in resolved.parents:
            raise ValueError("Knowledge registration overlaps platform storage")
    if resolved == Path(resolved.anchor):
        raise ValueError("Knowledge registration points to filesystem root")


async def _resources(writer: PackageWriter, graph: dict[str, Any], settings: dict[str, Any], owner: uuid.UUID) -> None:
    async with system_session(reason="owned portable experiment resource capture") as db:
        await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        for node in graph.get("nodes") or []:
            declared = {factor.get("name"): factor.get("levels") for factor in (
                settings.get("design_spec") or {}
            ).get("factors") or []}
            for name in dict.fromkeys((node.get("data") or {}).get("factor_bindings", {}).values()):
                if not declared.get(name):
                    writer.dependency(node["id"], "factor_dependency", "legacy.unknown", status="unresolved",
                                      factor=name, reason="Bound factor levels are unavailable.")
            for variant in node_variants(node, settings.get("design_spec")):
                config = (variant.get("data") or {}).get("config") or {}
                kind = variant.get("type", "")
                if kind == "skill":
                    await _skill(db, writer, str(config.get("skill_id") or ""), owner)
                elif kind in {"okf_bundle", "okf_document", "mcp_tool", "mcp_scikit_learn", "mcp_client_tool"}:
                    await _server(db, writer, config, owner, knowledge=kind in {"okf_bundle", "okf_document"})
                elif kind.startswith(("model_", "llm_")):
                    digest = hashlib.sha256(encoded(config)).hexdigest()
                    if not writer.seen("model", digest):
                        await archive_io(writer, writer.json, f"requirements/models/{digest}.json", safe_config(config),
                                         identity=node["id"])
                elif kind == "script":
                    code = config.get("code")
                    if isinstance(code, str):
                        digest = hashlib.sha256(code.encode()).hexdigest()
                        if not writer.seen("script", digest):
                            path = writer.staging / "script.py"
                            path.write_bytes(code.encode())
                            await archive_io(writer, writer.add, path, f"resources/scripts/{digest}.py",
                                             provenance="publication_pinned", identity=node["id"])
                        writer.dependency(node["id"], "script", "publication_pinned",
                                          file=f"resources/scripts/{digest}.py")
        kinds = {node.get("type") for node in graph.get("nodes") or []}
        for kind, server in (("script", "asaree-script"), ("dataset", "asaree-workspace"),
                             ("dataset", "scikit-learn-mcp")):
            if kind in kinds:
                await _server(db, writer, {"server_name": server}, owner)
        writer.dependency("motoro_resource_capture", "capture_window", "captured_at_download",
                          completed_at=datetime.now(UTC), note="Separate snapshot from ASAREE publication/results.")


async def stream_document(db: AsyncSession, query: Any, path: Path) -> None:
    """query selects one text expression; each response is at most SQL_BLOCK characters."""
    offset = 1
    with path.open("wb") as output:
        while True:
            part = await db.scalar(query.with_only_columns(func.substr(query.selected_columns[0], offset, SQL_BLOCK)))
            if part is None:
                output.write(b"null" if offset == 1 else b"")
                return
            output.write(part.encode())
            if len(part) < SQL_BLOCK:
                return
            offset += SQL_BLOCK


async def _design(db: AsyncSession, writer: PackageWriter, publication: ProtocolRevision) -> dict[str, Any] | None:
    if publication.design_revision_id is None:
        writer.dependency(str(publication.id), "generated_design", "publication_pinned", status="excluded",
                          reason="No generated design attached to this publication.")
        return None
    revision = await db.scalar(select(ExperimentDesignRevision).where(
        ExperimentDesignRevision.id == publication.design_revision_id,
    ))
    if revision is None:
        writer.dependency(str(publication.design_revision_id), "generated_design", "publication_pinned",
                          status="missing")
        return None
    _, declaration = portable_definition(publication.graph, {"design_spec": revision.design_spec})
    await archive_io(writer, writer.json, "design/revision.json", {
        "id": revision.id, "revision": revision.revision, "created_at": revision.created_at,
        "design_spec": declaration["design_spec"],
    }, identity=str(revision.id))
    # One cell/replicate at a time; JSONL is also future-import friendly.
    for model, columns, predicate, name in (
        (FactorialCell, (FactorialCell.id, FactorialCell.cell_label, FactorialCell.factor_values),
         FactorialCell.design_revision_id == revision.id, "cells"),
        (FactorialReplicateResult, (FactorialReplicateResult.id, FactorialReplicateResult.cell_id,
                                   FactorialReplicateResult.replicate_number, FactorialReplicateResult.replicate_label),
         FactorialReplicateResult.cell_id.in_(select(FactorialCell.id).where(
             FactorialCell.design_revision_id == revision.id)), "replicates"),
    ):
        path = writer.staging / "design.jsonl"
        rows = await db.stream(select(*columns).where(predicate).order_by(model.id).execution_options(yield_per=1))
        with path.open("wb") as output:
            async for row in rows.mappings():
                record = dict(row)
                if "factor_values" in record:
                    record["factor_values"] = safe_factor_values(record["factor_values"], publication.graph)
                output.write(encoded(record))
        await archive_io(writer, writer.add, path, f"design/{name}.jsonl",
                         provenance="publication_pinned", identity=str(revision.id))
    design = declaration["design_spec"]
    return design if isinstance(design, dict) else None


async def _results(
    db: AsyncSession, writer: PackageWriter, job: ResultJob, checkpoint: Any,
    graph: dict[str, Any] | None = None,
) -> None:
    # Explicit owner/publication/design scope; never mutable latest projections.
    conditions = (
        ProtocolRun.owner_id == job.owner_id, ProtocolRun.protocol_id == job.protocol_id,
        ProtocolRun.protocol_revision_id == job.protocol_revision_id,
        ProtocolRun.design_revision_id == job.design_revision_id,
        ProtocolRun.is_test_run.is_(False), ProtocolRun.target_node_id.is_(None),
    )
    after = None
    while True:
        query = select(ProtocolRun.id, ProtocolRun.status, ProtocolRun.created_at, ProtocolRun.started_at,
                       ProtocolRun.completed_at, ProtocolRun.replicate_result_id, ProtocolRun.row_result_id).where(
                           *conditions,
                       ).order_by(ProtocolRun.id).limit(1)
        if after is not None:
            query = query.where(ProtocolRun.id > after)
        row = (await db.execute(query)).mappings().one_or_none()
        if row is None:
            break
        after = row["id"]
        directory = f"results/attempts/{after}"
        await archive_io(writer, writer.json, f"{directory}/attempt.json", dict(row),
                         provenance="recorded_attempt", identity=str(after))
        if row["row_result_id"] and not writer.seen("row_slot", str(row["row_result_id"])):
            slot = await db.get(FactorialRowResult, row["row_result_id"])
            if slot is not None:
                await archive_io(writer, writer.json, f"results/rows/{slot.id}.json", {
                    key: getattr(slot, key) for key in (
                        "id", "replicate_result_id", "protocol_revision_id", "dataset_id", "raw_sha256", "row_index",
                    )
                }, provenance="recorded_attempt", identity=str(slot.id))
            else:
                writer.dependency(str(row["row_result_id"]), "row_slot", "recorded_attempt", status="missing")
        for document in ("factor_values", "dataset_inputs", "dataset_row", "attempt_result", "node_runs",
                         "conversation",
                         "usage_summary", "error"):
            path = writer.staging / "evidence.json"
            if document == "factor_values":
                values = await db.scalar(select(ProtocolRun.factor_values).where(*conditions, ProtocolRun.id == after))
                await archive_io(writer, writer.json, f"{directory}/{document}.json",
                                 safe_factor_values(values, graph or {}), provenance="recorded_attempt",
                                 identity=str(after))
                continue
            column = getattr(ProtocolRun, document)
            if document == "error":
                column = func.to_jsonb(column)
            await stream_document(db, select(cast(column, Text)).where(
                *conditions, ProtocolRun.id == after,
            ), path)
            await archive_io(writer, writer.add, path, f"{directory}/{document}.json",
                             provenance="recorded_attempt", identity=str(after))
        try:
            await _attempt_files(writer, job, after, graph)
        except (ValueError, OSError):
            writer.dependency(str(after), "attempt_artifacts", "recorded_attempt", status="missing",
                              reason="Retained artifact tree unavailable or unsafe.")
        await checkpoint()
    count = await db.scalar(select(func.count()).select_from(ExperimentArtifact).where(
        ExperimentArtifact.experiment_id == job.experiment_id,
    ))
    if count:
        writer.dependency(str(job.experiment_id), "unscoped_experiment_artifacts", "legacy.unknown", status="excluded",
                          count=count, reason="No publication attribution recorded; not assigned to selected version.")
    writer.dependency("native_agent_runtime_state", "runtime_state", "recorded_attempt", status="excluded",
                      reason="AgentRun IDs are source provenance; native steps and resume state are not bundled.")


async def _attempt_files(
    writer: PackageWriter, job: ResultJob, run_id: uuid.UUID, graph: dict[str, Any] | None = None,
) -> None:
    """Only an attempt's identity-isolated durable output, never shared legacy workspaces."""
    from asaree.services.dataset_row_workspaces import row_attempt_workspace_id

    root = managed_storage.roots()["workspaces"]
    candidates = (root / str(job.experiment_id) / str(run_id), root / row_attempt_workspace_id(run_id))
    found = False
    for directory in candidates:
        if not directory.is_dir():
            continue
        found = True
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError("Attempt artifact tree escapes storage")
        sources = {re.sub(r"[^A-Za-z0-9_.-]", "_", str(node["id"])) + ".py"
                   for node in (graph or {}).get("nodes") or [] if node.get("type") == "script"}
        # Unknown outputs within this UUID-isolated attempt are retained durable
        # evidence, including agent= slots, row agents and script working dirs.
        # Exclude only known platform control/input/scratch identities.
        for relative, path, _ in _tree_files(directory):
            parts = PurePosixPath(relative).parts
            if credential_file(relative) or any(
                part in {"scratch", "working", "cache", "__pycache__"}
                or part.startswith(".asaree-script-context-") or part.endswith(".preparing") for part in parts
            ):
                continue
            control_parent = len(parts) == 1 or (
                len(parts) == 2 and parts[0].startswith(("dataset=", "agent="))
            ) or (len(parts) >= 3 and parts[-2].startswith(".") and "-unsplit-" in parts[-2])
            if path.name == "state.json" and control_parent:
                continue
            if (path.name == "dataset_inputs.json" and len(parts) == 1
                or path.name == "row-view.json" and len(parts) == 3 and parts[0] == "agents"):
                continue
            if "scripts" in parts and (path.name in sources or re.fullmatch(r"inline-[0-9a-f]{12}\.py", path.name)):
                continue
            if (len(parts) >= 3 and parts[0] == "agents" and re.fullmatch(r"[0-9a-f]{64}\.csv", path.name)):
                continue
            try:
                await archive_io(writer, writer.file, path, f"results/artifacts/{run_id}/{relative}",
                                 identity=str(run_id), provenance="retained_attempt_artifact")
            except (ValueError, OSError):
                writer.dependency(str(run_id), "attempt_artifact", "recorded_attempt", status="missing",
                                  relative_path=relative, reason="Retained file unavailable or changed.")
    if not found:
        writer.dependency(str(run_id), "attempt_artifacts", "recorded_attempt", status="missing",
                          reason="No retained identity-isolated workspace; historical files may be unavailable.")


async def execute_package(db: AsyncSession, job: ResultJob, temporary: Path, destination: Path) -> None:
    """Called inside the export worker's repeatable-read/advisory-lock scope."""
    from asaree_workspace_core.storage import atomic_write

    from asaree.services.result_exports import export_path

    experiment = await db.scalar(select(ResearchExperiment).where(
        ResearchExperiment.id == job.experiment_id, ResearchExperiment.owner_id == job.owner_id,
    ))
    publication = await db.scalar(select(ProtocolRevision).join(Protocol).where(
        ProtocolRevision.id == job.protocol_revision_id, ProtocolRevision.protocol_id == job.protocol_id,
        Protocol.experiment_id == job.experiment_id, Protocol.owner_id == job.owner_id,
    ))
    if experiment is None or publication is None:
        raise ValueError("Owned experiment publication unavailable")
    if job.design_revision_id != publication.design_revision_id:
        raise ValueError("Package design does not match publication")
    async with get_session() as status_db:
        await status_db.execute(update(ResultJob).where(ResultJob.id == job.id, ResultJob.status != "cancelled").values(
            status="running", progress={"bytes": 0}, error=None,
        ))
    started = datetime.now(UTC)
    checkpoint_time = 0.0

    async def checkpoint() -> None:
        nonlocal checkpoint_time
        if time.monotonic() - checkpoint_time < 1:
            return
        async with get_session() as status_db:
            status = await status_db.scalar(select(ResultJob.status).where(ResultJob.id == job.id))
            if status == "cancelled":
                raise ValueError("Export cancelled")
            await status_db.execute(update(ResultJob).where(ResultJob.id == job.id).values(
                progress={"bytes": temporary.stat().st_size if temporary.exists() else 0},
            ))
        checkpoint_time = time.monotonic()

    with tempfile.TemporaryDirectory(prefix=f".{job.id}-package-", dir=destination.parent) as staging:
        writer = PackageWriter(temporary, Path(staging))
        writer.checkpoint = checkpoint
        try:
            graph, settings = portable_definition(publication.graph, publication.experiment_snapshot or {})
            if publication.experiment_snapshot is None:
                writer.dependency(str(publication.id), "experiment_settings", "legacy.unknown", status="unresolved",
                                  reason="Historical settings were not recorded; current draft is not substituted.")
            await archive_io(writer, writer.json, "publication/canvas.json", graph, identity=str(publication.id))
            await archive_io(writer, writer.json, "publication/settings.json", settings, identity=str(publication.id))
            await archive_io(writer, writer.json, "experiment-metadata.json", {
                "id": str(job.experiment_id), "name": getattr(experiment, "name", None),
                "description": getattr(experiment, "description", None),
            }, provenance="captured_at_download", identity=str(job.experiment_id))
            await _datasets(db, writer, publication, job.owner_id)
            await checkpoint()
            design = await _design(db, writer, publication)
            await _resources(writer, graph, settings if settings else {"design_spec": design}, job.owner_id)
            await checkpoint()
            if job.params["selection"].get("include_results", False):
                await _results(db, writer, job, checkpoint, graph)
            await _import_sources(db, writer, job.experiment_id,
                                  include_results=job.params['selection'].get('include_results', False))
            software = {}
            for package in ("asaree", "motoro", "asaree-workspace-core", "asaree-sklearn-core", "scikit-learn-mcp",
                            "asaree-sklearn-dc", "asaree-sklearn-eda", "asaree-sklearn-fs", "asaree-sklearn-fte",
                            "asaree-sklearn-model", "asaree-sklearn-stats"):
                try:
                    software[package] = importlib.metadata.version(package)
                except importlib.metadata.PackageNotFoundError:
                    software[package] = "unknown"
            manifest = await archive_io(writer, writer.finish, {
                "format": "asaree.experiment-package", "schema_version": 2,
                "experiment_id": str(job.experiment_id), "protocol_id": str(job.protocol_id),
                "publication": {"id": str(publication.id), "version": publication.revision,
                                "published_at": publication.published_at, "name": publication.name,
                                "note": publication.note,
                                "annotations_provenance": "captured_at_download"},
                "design_revision_id": str(publication.design_revision_id) if publication.design_revision_id else None,
                "results_included": job.params["selection"].get("include_results", False),
                "capture": {"started_at": started, "completed_at": datetime.now(UTC),
                            "database": "ASAREE repeatable-read snapshot; separate Motoro resource snapshot",
                            "mutable_files": "download-time capture; no historical/cross-file atomicity claim"},
                "requirements": {"python": __import__("platform").python_version(), "software": software},
                "excluded": ["credentials and connection material", "credential files", "derived row indexes",
                             "caches", "scratch", "runtime queues", "workspace resume state",
                             "unscoped experiment artifacts", "test runs and node previews"],
                "portability": "Logical export; configure local connections and publish before execution.",
                "configuration_rewrites": ["credential/connection fields omitted",
                                           "knowledge display paths refer to archive directories",
                                           "sampling keys hash credential-free explicit input, not runtime requests"],
            })
        finally:
            writer.checkpoint = None
            await archive_io(writer, writer.close)
    checkpoint_time = 0
    await checkpoint()
    writer.checkpoint = checkpoint
    digest = await archive_io(writer, archive_hash, writer, temporary)
    await archive_io(writer, temporary.replace, destination)
    await archive_io(writer, atomic_write, export_path(job.id, metadata=True), encoded(manifest))
    async with get_session() as status_db:
        await status_db.execute(update(ResultJob).where(ResultJob.id == job.id, ResultJob.status != "cancelled").values(
            status="completed", progress={"bytes": destination.stat().st_size, "sha256": digest}, error=None,
        ))


async def _import_sources(db: AsyncSession, writer: PackageWriter, experiment_id: uuid.UUID,
                          *, include_results: bool) -> None:
    """Local exports keep imported provenance separate from local attempt history."""
    from asaree.models.package_import import ImportedIdentity, PackageImport
    from asaree.services.package_imports import directory

    imports = await db.stream_scalars(select(PackageImport).where(
        PackageImport.experiment_id == experiment_id, PackageImport.status == 'completed',
    ).execution_options(yield_per=1))
    async for imported in imports:
        prefix = f'sources/{imported.id}'
        root = directory(imported.id)
        # Source manifest and publication always remain immutable attribution.
        for name in ('manifest.json', 'publication/canvas.json', 'publication/settings.json',
                     'experiment-metadata.json'):
            source = root / 'payload' / name
            await archive_io(writer, writer.file, source, f'{prefix}/{name}',
                             identity=str(imported.id), provenance='imported_source')
        path = writer.staging / 'source-identities.jsonl'
        rows = await db.stream_scalars(select(ImportedIdentity).where(
            ImportedIdentity.import_id == imported.id,
        ).order_by(ImportedIdentity.kind, ImportedIdentity.source_id).execution_options(yield_per=1))
        with path.open('wb') as output:
            async for row in rows:
                if not include_results and row.kind.startswith('historical_'):
                    continue
                output.write(encoded({'kind': row.kind, 'source_id': row.source_id,
                                      'local_id': row.local_id, 'provenance': row.provenance}))
        await archive_io(writer, writer.add, path, f'{prefix}/identities.jsonl',
                         identity=str(imported.id), provenance='imported_source')
        if include_results:
            await archive_io(writer, writer.file, root / 'archive.tar.gz', f'{prefix}/source.tar.gz',
                             identity=str(imported.id), provenance='imported_source', expected=imported.sha256)
        writer.dependency(str(imported.id), 'imported_source', 'imported_source', directory=prefix,
                          archive_included=include_results,
                          note='Source evidence is separate from local execution; nested archives are never executed.')
