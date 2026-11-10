"""Offline, copy-first storage migration. Inventory is the default operation.

Run with all writers stopped. The versioned plan records exact bytes and
database before/after values; it never assigns orphan status or removes files.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

from asaree_workspace_core.storage import (
    atomic_write,
    managed_reference,
    map_ambient_paths,
    map_workspace_paths,
    resolve,
    sha256,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from asaree.config import get_settings
from asaree.services.managed_storage import roots

# Explicit, reviewable persisted-reference inventory. UUIDs and pinned input
# manifests are evidence; only the legacy raw location and approved managed
# knowledge commands require database rewrites.
QUERIES = {
    "asaree": {
        "registered_datasets": "SELECT id, raw_path, raw_artifact_id FROM registered_datasets",
        "dataset_artifacts": "SELECT * FROM dataset_artifacts",
        "dataset_splits": "SELECT * FROM dataset_splits",
        "dataset_input_references": "SELECT * FROM dataset_input_references",
        "protocol_revisions": "SELECT id, dataset_inputs FROM protocol_revisions",
        "protocol_runs": "SELECT id, status, dataset_inputs FROM protocol_runs",
        "factorial_replicate_results": "SELECT id, workspace_id FROM factorial_replicate_results",
        "factorial_row_results": "SELECT id, workspace_id FROM factorial_row_results",
        "dataset_workspace_events": "SELECT id, workspace_id FROM dataset_workspace_events",
        "result_jobs": "SELECT id, kind, status, created_at FROM result_jobs",
    },
    "motoro": {
        "mcp_server_configs": "SELECT id, name, owner_id, command FROM mcp_server_configs",
        "agent_runs": "SELECT id, status, run_metadata, state_snapshot FROM agent_runs",
    },
}


async def database_inventory() -> dict[str, Any]:
    settings = get_settings()
    result = {}
    for database, url in (("asaree", settings.product_database_url), ("motoro", settings.database_url)):
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                await connection.execution_options(isolation_level="REPEATABLE READ")
                result[database] = {
                    table: [dict(row) for row in (await connection.execute(text(query))).mappings()]
                    for table, query in QUERIES[database].items()
                }
        finally:
            await engine.dispose()
    # UUID/date serialization without recording connection credentials.
    return json.loads(json.dumps(result, default=str))


def rewrite_paths(value: Any, old: dict[str, Path]) -> Any:
    # Call only for fields identified as locators by their owning schema.
    if isinstance(value, str) and Path(value).is_absolute():
        return managed_reference(Path(value), old)
    return value


def make_plan(old: dict[str, Path], new: dict[str, Path], databases: dict[str, Any],
              managed_knowledge: set[str], legacy_attempt_root: Path | None = None) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "version": 1, "old_roots": {key: str(value) for key, value in old.items()},
        "new_roots": {key: str(value) for key, value in new.items()},
        "databases": databases, "files": [], "updates": [], "problems": [], "diagnostics": [],
        "retention": "All inventoried files retained; unregistered does not mean orphaned.",
    }
    locations = [(key, root, "") for key, root in old.items()]
    if legacy_attempt_root:
        locations.append(("workspaces", legacy_attempt_root.resolve(), "_protocol_runs/"))

    def locator(value: str) -> str:
        if legacy_attempt_root and Path(value).is_absolute():
            path = Path(value).resolve()
            legacy = legacy_attempt_root.resolve()
            if path != legacy and path.is_relative_to(legacy):
                return f"managed://workspaces/_protocol_runs/{path.relative_to(legacy).as_posix()}"
        return rewrite_paths(value, old)
    destinations: set[str] = set()
    for destination in new.values():
        if any(destination == source or destination.is_relative_to(source)
               or source.is_relative_to(destination) for _, source, _ in locations):
            plan["problems"].append(f"Source and destination roots overlap: {destination}")
    for namespace, root, prefix in locations:
        if not root.exists():
            continue
        for source in sorted(root.rglob("*")):
            if source.is_symlink():
                plan["problems"].append(f"Symlink requires explicit ownership review: {source}")
                continue
            if not source.is_file():
                continue
            if source.name.endswith(".env"):
                plan["problems"].append(f"Excluded environment file: {source}")
                continue
            key = prefix + source.relative_to(root).as_posix()
            destination = resolve(f"managed://{namespace}/{key}", new)
            if str(destination) in destinations:
                plan["problems"].append(f"Conflicting destination: {destination}")
                continue
            destinations.add(str(destination))
            entry = {"source": str(source), "destination": str(destination),
                     "sha256": sha256(source), "byte_size": source.stat().st_size}
            # Workspace state lives at <workspace-id>/<cell-id>/state.json.
            # Nested outputs named state.json and all payload manifests stay opaque.
            if namespace == "workspaces" and source.name == "state.json" and len(Path(key).parts) == 3:
                try:
                    before = json.loads(source.read_text())
                    after = map_workspace_paths(before, locator)
                    if before != after:
                        entry["json"] = after
                except (ValueError, UnicodeError):
                    plan["problems"].append(f"Unreadable workspace JSON: {source}")
            plan["files"].append(entry)
    for artifact in databases.get("asaree", {}).get("dataset_artifacts", []):
        if artifact["status"] != "ready":
            plan["diagnostics"].append(f"Historical artifact {artifact['id']} remains {artifact['status']}")
            continue
        try:
            path = resolve(f"managed://{artifact['namespace']}/{artifact['storage_key']}", old)
            if not artifact["sha256"] or sha256(path) != artifact["sha256"]:
                plan["problems"].append(f"Artifact not verified ready: {artifact['id']}")
            elif path.stat().st_size != artifact["byte_size"]:
                plan["problems"].append(f"Artifact size mismatch: {artifact['id']}")
        except (OSError, ValueError):
            plan["problems"].append(f"Artifact unavailable: {artifact['id']}")
    artifacts = {row["id"]: row for row in databases.get("asaree", {}).get("dataset_artifacts", [])}
    for table in ("protocol_revisions", "protocol_runs"):
        for row in databases.get("asaree", {}).get(table, []):
            for pin in (row.get("dataset_inputs") or {}).get("inputs", []):
                for field in ("raw_artifact", "train_artifact", "test_artifact"):
                    ref = pin.get(field)
                    if ref is not None:
                        artifact = artifacts.get(ref.get("id"))
                        if (artifact is None or artifact["sha256"] != ref.get("sha256")
                                or artifact.get("dataset_id") != pin.get("dataset_id")):
                            plan["problems"].append(f"Pinned artifact identity mismatch: {table}/{row['id']}/{field}")
    for row in databases.get("asaree", {}).get("registered_datasets", []):
        before = row.get("raw_path")
        after = rewrite_paths(before, old)
        if before != after:
            plan["updates"].append(dict(database="asaree", table="registered_datasets", column="raw_path",
                                        id=row["id"], before=before, after=after))
    for row in databases.get("motoro", {}).get("mcp_server_configs", []):
        if row["id"] not in managed_knowledge:
            continue  # location alone never proves that a bundle is owned
        command = row.get("command") or ""
        parts = shlex.split(command)
        if "--bundle" not in parts or "asaree.mcp_servers.okf_bundle" not in parts:
            plan["problems"].append(f"Unrecognized managed knowledge command: {row['id']}")
            continue
        index = parts.index("--bundle") + 1
        if index >= len(parts):
            plan["problems"].append(f"Missing knowledge location: {row['id']}")
            continue
        value = rewrite_paths(parts[index], old)
        if not value.startswith(("managed://documents/", "managed://bundles/")):
            plan["problems"].append(f"Knowledge location is not managed: {row['id']}")
            continue
        if not resolve(value, old).is_dir():
            plan["problems"].append(f"Managed knowledge unavailable: {row['id']}")
            continue
        from asaree.services.system_mcp_servers import command_for

        after = f"{command_for('asaree.mcp_servers.okf_bundle')} --bundle {shlex.quote(value)}"
        plan["updates"].append(dict(database="motoro", table="mcp_server_configs", column="command",
                                    id=row["id"], before=command, after=after))
    for row in databases.get("motoro", {}).get("agent_runs", []):
        for column in ("run_metadata", "state_snapshot"):
            before = row.get(column)
            if not isinstance(before, dict) or not isinstance(before.get("ambient_meta"), dict):
                continue
            after = dict(before, ambient_meta=map_ambient_paths(before["ambient_meta"], locator))
            if before != after:
                plan["updates"].append(dict(database="motoro", table="agent_runs", column=column,
                                            id=row["id"], before=before, after=after))
    known_servers = {row["id"] for row in databases.get("motoro", {}).get("mcp_server_configs", [])}
    for identity in managed_knowledge - known_servers:
        plan["problems"].append(f"Managed knowledge registration not found: {identity}")
    return plan


def copy_files(plan: dict[str, Any]) -> None:
    if plan["problems"]:
        raise ValueError("Resolve inventory problems before migration")
    # Preflight all sources before any mutation; restart safely after interruption.
    for entry in plan["files"]:
        source = Path(entry["source"])
        if source.is_symlink() or sha256(source) != entry["sha256"]:
            raise ValueError(f"Source changed after inventory: {source}")
        if source == Path(entry["destination"]):
            raise ValueError("Copy-first migration requires different source and destination roots")
    for entry in plan["files"]:
        destination = Path(entry["destination"])
        if "json" in entry:
            content = (json.dumps(entry["json"], indent=1) + "\n").encode()
            import hashlib

            expected = hashlib.sha256(content).hexdigest()
            if destination.exists() and sha256(destination) != expected:
                raise ValueError(f"Destination conflicts: {destination}")
            atomic_write(destination, content)
        else:
            if destination.exists():
                if sha256(destination) != entry["sha256"]:
                    raise ValueError(f"Destination conflicts: {destination}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=destination.parent, suffix=".preparing")
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as output, Path(entry["source"]).open("rb") as source:
                    import shutil

                    shutil.copyfileobj(source, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if sha256(temporary) != entry["sha256"]:
                    raise ValueError("Copied bytes failed verification")
                os.link(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)


def verify_files(plan: dict[str, Any]) -> None:
    import hashlib

    for entry in plan["files"]:
        expected = (hashlib.sha256((json.dumps(entry["json"], indent=1) + "\n").encode()).hexdigest()
                    if "json" in entry else entry["sha256"])
        if sha256(Path(entry["destination"])) != expected:
            raise ValueError(f"Destination integrity failure: {entry['destination']}")


async def update_databases(plan: dict[str, Any], *, rollback: bool = False, verify_only: bool = False) -> None:
    settings = get_settings()
    for database, url in (("asaree", settings.product_database_url), ("motoro", settings.database_url)):
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                for entry in plan["updates"]:
                    if entry["database"] != database:
                        continue
                    # A plan is data, never permission to execute arbitrary SQL.
                    if (database, entry["table"], entry["column"]) not in {
                        ("asaree", "registered_datasets", "raw_path"),
                        ("motoro", "mcp_server_configs", "command"),
                        ("motoro", "agent_runs", "run_metadata"),
                        ("motoro", "agent_runs", "state_snapshot"),
                    }:
                        raise ValueError("Unsupported database reference")
                    before, after = (entry["after"], entry["before"]) if rollback else (entry["before"], entry["after"])
                    table, column = entry["table"], entry["column"]
                    current = await connection.scalar(text(f"SELECT {column} FROM {table} WHERE id=:id FOR UPDATE"),
                                                      {"id": entry["id"]})
                    if current == after:
                        continue
                    if verify_only:
                        raise ValueError(f"Database reference was not migrated: {database}/{table}/{entry['id']}")
                    if current != before:
                        raise ValueError(f"Database reference changed: {database}/{table}/{entry['id']}")
                    expression = "CAST(:value AS json)" if table == "agent_runs" else ":value"
                    await connection.execute(text(f"UPDATE {table} SET {column}={expression} WHERE id=:id"),
                                             {"id": entry["id"], "value": json.dumps(after)
                                              if table == "agent_runs" else after})
        finally:
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inventory", "apply", "verify", "rollback"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--old-roots", type=Path, help="JSON namespace -> absolute old root; required for inventory")
    parser.add_argument("--legacy-attempt-root", type=Path)
    parser.add_argument("--managed-knowledge", action="append", default=[], help="Verified ASAREE-owned server UUID")
    args = parser.parse_args()
    if args.operation == "inventory":
        if not args.old_roots:
            parser.error("inventory requires --old-roots")
        old = {key: Path(value).expanduser().resolve() for key, value in json.loads(args.old_roots.read_text()).items()}
        if set(old) != set(roots()):
            parser.error("old-roots must explicitly list every namespace")
        plan = make_plan(old, roots(), asyncio.run(database_inventory()), set(args.managed_knowledge),
                         args.legacy_attempt_root)
        atomic_write(args.plan, json.dumps(plan, indent=2).encode(), overwrite=False)
        print(f"Inventoried {len(plan['files'])} files, {len(plan['updates'])} reference updates; "
              f"{len(plan['problems'])} problems, {len(plan['diagnostics'])} diagnostics. "
              "No storage/database changes made.")
        return
    plan = json.loads(args.plan.read_text())
    if plan.get("version") != 1:
        parser.error("unsupported plan version")
    if args.operation == "apply":
        if plan["new_roots"] != {key: str(value) for key, value in roots().items()}:
            parser.error("runtime storage configuration differs from the reviewed destination plan")
        copy_files(plan)
        verify_files(plan)
        asyncio.run(update_databases(plan))
    elif args.operation == "verify":
        verify_files(plan)
        asyncio.run(update_databases(plan, verify_only=True))
    else:
        asyncio.run(update_databases(plan, rollback=True))
    print(f"{args.operation}: complete; source files retained")


if __name__ == "__main__":
    main()
