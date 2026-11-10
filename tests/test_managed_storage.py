"""Storage contract checks without PostgreSQL, Redis, or MCP infrastructure."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from asaree_workspace_core import Workspace
from asaree_workspace_core.storage import atomic_write, reference, resolve, sha256, storage_roots

from asaree.config import AsareeSettings, configure_storage_environment
from asaree.services import managed_storage, okf_bundles
from asaree.storage_migration import copy_files, make_plan, verify_files


@pytest.fixture
def clean_storage(monkeypatch, tmp_path):
    from asaree_workspace_core.storage import NAMESPACES

    from asaree import config

    # Settings are process-cached; other suites may have resolved a different
    # storage deployment before this fixture scopes the environment.
    monkeypatch.setattr(config, "_instance", None)
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for field in ["data_root", *(field for field, _ in NAMESPACES.values())]:
        monkeypatch.delenv(f"ASAREE_{field.upper()}", raising=False)
    monkeypatch.setenv("ASAREE_DATA_ROOT", str(tmp_path / "data"))
    return tmp_path


def test_configuration_precedence_and_cross_process_visibility(clean_storage, monkeypatch):
    root = clean_storage
    monkeypatch.setenv("ASAREE_DATASET_STORAGE_DIR", str(root / "old-datasets"))
    settings = AsareeSettings(_env_file=None, data_root=str(root / "new-root"),
                              result_export_dir=str(root / "exports-override"))
    assert settings.dataset_storage_dir == str(root / "old-datasets")
    assert settings.result_export_dir == str(root / "exports-override")
    assert settings.dataset_workspace_dir == str(root / "new-root" / "workspaces")
    configure_storage_environment(settings)
    from motoro.mcp.client import build_subprocess_env

    # Check the same allowlist used for real MCP processes, rather than just
    # inheriting all of the parent's environment in this test.
    env = build_subprocess_env(allowed_env_vars=frozenset(settings.mcp_allowed_env_vars.split(",")))
    completed = subprocess.run(
        [sys.executable, "-c", "from asaree_workspace_core.storage import storage_roots; "
         "print(storage_roots()['workspaces'])"],
        cwd=root, env={**env, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True, text=True, check=True,
    )
    assert completed.stdout.strip() == settings.dataset_workspace_dir


def test_references_reject_traversal_and_symlinks(clean_storage):
    roots = storage_roots()
    roots["datasets"].mkdir(parents=True)
    (roots["datasets"] / "escape").symlink_to(clean_storage)
    for key in ("../outside", "/absolute", "a/../b", "a\\b", "a//b"):
        with pytest.raises(ValueError):
            reference("datasets", key)
    with pytest.raises(ValueError):
        resolve("managed://datasets/escape/file.csv", roots)


def test_atomic_create_and_interrupted_replace_preserve_ready_bytes(clean_storage, monkeypatch):
    path = clean_storage / "ready"
    atomic_write(path, b"original", overwrite=False)
    with pytest.raises(FileExistsError):
        atomic_write(path, b"replacement", overwrite=False)
    assert path.read_bytes() == b"original"
    monkeypatch.setattr(Path, "replace", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        atomic_write(path, b"incomplete")
    assert path.read_bytes() == b"original"
    assert list(clean_storage.glob("*.preparing")) == []


def test_workspace_relocation_preserves_format_slots_and_resume(clean_storage, monkeypatch):
    old = storage_roots()
    dataset = old["datasets"] / "input"
    dataset.mkdir(parents=True)
    frame = pd.DataFrame({"x": [1, 2], "target": [0, 1]})
    for name in ("train", "test"):
        frame.to_parquet(dataset / f"{name}.parquet", index=False)
    ws = Workspace.open("experiment/cell", root=str(old["workspaces"]), target_column="target",
                        seed_train_path=str(dataset / "train.parquet"),
                        seed_test_path=str(dataset / "test.parquet"), slot="dataset:ordered-first")
    original = json.loads(ws.state_path.read_text())
    assert original["slots"]["dataset:ordered-first"]["versions"][0]["train"].startswith("managed://datasets/")
    new_data = clean_storage / "relocated"
    shutil.copytree(clean_storage / "data", new_data)
    shutil.rmtree(clean_storage / "data")
    monkeypatch.setenv("ASAREE_DATA_ROOT", str(new_data))
    moved = Workspace("experiment/cell", root=str(new_data / "workspaces"), slot="dataset:ordered-first")
    assert moved.read_head()[0]["x"].tolist() == [1, 2]
    assert moved.load_state()["head"] == "v0_raw"
    Workspace.open("experiment/cell", root=str(new_data / "workspaces"), target_column="target",
                   seed_train_path=str(new_data / "datasets/input/train.parquet"),
                   seed_test_path=str(new_data / "datasets/input/test.parquet"), slot="dataset:ordered-first")


async def test_external_registration_under_upload_root_never_deletes(clean_storage, monkeypatch):
    directory = storage_roots()["bundles"] / "user-owned"
    directory.mkdir(parents=True)
    file = directory / "index.md"
    file.write_text("external")
    monkeypatch.setattr(okf_bundles.mcp_service, "delete_server", AsyncMock())
    await okf_bundles.delete_bundle(SimpleNamespace(id=uuid.uuid4(), command=f"python --bundle {directory}"))
    assert file.read_text() == "external"


def test_cleanup_protects_retained_legacy_workspace_and_unknown_state(clean_storage, monkeypatch):
    monkeypatch.setattr(managed_storage, "roots", storage_roots)
    root = storage_roots()["workspaces"]
    state = root / "experiment/cell/state.json"
    source = storage_roots()["datasets"] / "source.csv"
    atomic_write(state, json.dumps({"versions": [{"train": str(source)}]}).encode())
    assert managed_storage.workspace_references({source})
    assert not managed_storage.workspace_references({source.with_name("other.csv")})
    state.write_text("interrupted")
    assert managed_storage.workspace_references({source.with_name("other.csv")})


def test_migration_dry_run_integrity_legacy_formats_and_retry(clean_storage):
    old = storage_roots()
    new = {key: clean_storage / "new" / key for key in old}
    source = old["datasets"] / "source.csv"
    atomic_write(source, b"x\n1\n")
    state = old["workspaces"] / "exp/cell/state.json"
    atomic_write(state, json.dumps({"head": "v0_raw", "versions": [{"train": str(source)}]}).encode())
    databases = {"asaree": {"dataset_artifacts": [{"id": "source-id", "namespace": "datasets",
                 "storage_key": "source.csv", "status": "ready", "sha256": sha256(source),
                 "byte_size": source.stat().st_size}], "registered_datasets": []}}
    plan = make_plan(old, new, databases, set())
    assert not plan["problems"]
    assert not new["datasets"].exists()
    copy_files(plan)
    copy_files(plan)  # safe recovery after interrupted copy/database update
    verify_files(plan)
    migrated = json.loads((new["workspaces"] / "exp/cell/state.json").read_text())
    assert "slots" not in migrated
    assert migrated["versions"][0]["train"] == "managed://datasets/source.csv"
    assert source.read_bytes() == b"x\n1\n"
    source.write_bytes(b"x\ncorrupted\n")
    with pytest.raises(ValueError, match="changed after inventory"):
        copy_files(plan)
    assert make_plan(old, new, databases, set())["problems"]


def test_row_snapshots_use_workspace_root_independent_of_dataset_parent(clean_storage, monkeypatch):
    from asaree.services import dataset_row_workspaces

    settings = SimpleNamespace(dataset_workspace_dir=str(clean_storage / "workspaces"),
                               dataset_storage_dir=str(clean_storage / "unrelated/datasets"))
    monkeypatch.setattr(dataset_row_workspaces, "get_settings", lambda: settings)
    assert dataset_row_workspaces._workspace_root() == clean_storage / "workspaces"


def test_interrupted_stage_pointer_keeps_previous_pair_and_manifest(clean_storage, monkeypatch):
    from asaree_workspace_core.workspace import WorkspaceError

    directory = storage_roots()["datasets"]
    directory.mkdir(parents=True)
    frame = pd.DataFrame({"x": [1, 2], "target": [0, 1]})
    for partition in ("train", "test"):
        frame.to_parquet(directory / f"{partition}.parquet", index=False)
    ws = Workspace.open("exp/cell", root=str(storage_roots()["workspaces"]), target_column="target",
                        seed_train_path=str(directory / "train.parquet"),
                        seed_test_path=str(directory / "test.parquet"))
    inputs = dict(zip(("X_train", "y_train", "X_test", "y_test"), ws.read_head(), strict=True))
    ws.write_stage("dc", **inputs, learned={"generation": "original"})
    previous = ws.state_path.read_bytes()
    monkeypatch.setattr(ws, "_save_state", lambda *_: (_ for _ in ()).throw(OSError("interrupted pointer")))
    with pytest.raises(OSError, match="interrupted pointer"):
        ws.write_stage("dc", **inputs, learned={"generation": "interrupted"})
    assert ws.state_path.read_bytes() == previous
    assert json.loads(ws.manifest_path("dc").read_text())["learned"] == {"generation": "original"}
    version = ws._find_version("v1_dc")
    Path(version["train"]).write_bytes(b"changed")
    with pytest.raises(WorkspaceError, match="hash mismatch"):
        ws.read_stage_working("dc")


def test_migration_preserves_pins_and_requires_knowledge_ownership(clean_storage):
    old = storage_roots()
    new = {key: clean_storage / "new" / key for key in old}
    source = old["datasets"] / "source.csv"
    atomic_write(source, b"x\n1\n")
    digest = sha256(source)
    pin = {"dataset_id": "dataset-id", "raw_artifact": {"id": "artifact-id", "sha256": digest}}
    command = f"uv run python -m asaree.mcp_servers.okf_bundle --bundle {old['bundles']}/external"
    databases = {
        "asaree": {"dataset_artifacts": [{"id": "artifact-id", "dataset_id": "dataset-id",
                     "namespace": "datasets", "storage_key": "source.csv", "sha256": digest,
                     "byte_size": source.stat().st_size, "status": "ready"}],
                   "protocol_revisions": [{"id": "publication", "dataset_inputs": {"inputs": [pin]}}]},
        "motoro": {"mcp_server_configs": [{"id": "external", "command": command}]},
    }
    plan = make_plan(old, new, databases, set())
    assert not plan["problems"]
    assert not plan["updates"]  # containment does not transfer ownership
    assert plan["databases"]["asaree"]["protocol_revisions"][0]["dataset_inputs"]["inputs"] == [pin]
    pin["raw_artifact"]["sha256"] = "0" * 64
    assert any("Pinned artifact identity mismatch" in problem
               for problem in make_plan(old, new, databases, set())["problems"])


def test_migration_refuses_overlapping_roots_and_preserves_unknown_files(clean_storage):
    old = storage_roots()
    atomic_write(old["workspaces"] / "unknown/output.bin", b"durable until reviewed")
    plan = make_plan(old, old, {}, set())
    assert plan["files"]
    with pytest.raises(ValueError, match="inventory problems"):
        copy_files(plan)
    assert Path(plan["files"][0]["source"]).read_bytes() == b"durable until reviewed"


def test_migration_typed_locators_preserve_exact_payloads_and_missing_history(clean_storage):
    old = storage_roots()
    new = {key: clean_storage / "new" / key for key in old}
    exact = str(old["datasets"] / "an-exact-cell-value")
    opaque = {"train": exact, "test": exact, "manifest_path": exact, "nested": [exact]}
    workspace = old["workspaces"] / "exp/cell"
    state = {"head": "v0_raw", "versions": [{"train": exact, "learned": opaque}], "rationale": opaque}
    atomic_write(workspace / "state.json", json.dumps(state).encode())
    for name in ("row-view.json", "dataset_inputs.json", "output.json", "manifest.json"):
        atomic_write(workspace / name, json.dumps(opaque).encode())
    atomic_write(workspace / "outputs/state.json", json.dumps(state).encode())
    missing = {"id": "missing", "dataset_id": "dataset", "namespace": "datasets", "storage_key": "lost.csv",
               "status": "missing", "sha256": None, "byte_size": None}
    pin = {"dataset_id": "dataset", "raw_artifact": {"id": "missing", "sha256": None}}
    databases = {"asaree": {"dataset_artifacts": [missing], "protocol_revisions": [
        {"id": "publication", "dataset_inputs": {"inputs": [pin]}}]}}
    plan = make_plan(old, new, databases, set())
    assert not plan["problems"]
    assert plan["diagnostics"]
    assert plan["databases"] == databases
    copy_files(plan)
    migrated = json.loads((new["workspaces"] / "exp/cell/state.json").read_text())
    assert migrated["versions"][0]["train"] == "managed://datasets/an-exact-cell-value"
    assert migrated["versions"][0]["learned"] == opaque
    assert migrated["rationale"] == opaque
    for name in ("row-view.json", "dataset_inputs.json", "output.json", "manifest.json"):
        assert (new["workspaces"] / "exp/cell" / name).read_bytes() == (workspace / name).read_bytes()
    nested_state = new["workspaces"] / "exp/cell/outputs/state.json"
    assert nested_state.read_bytes() == (workspace / "outputs/state.json").read_bytes()
    # The workspace serializer must obey the same typed boundary on save/load.
    ws = Workspace("exp/cell", root=str(old["workspaces"]))
    encoded = ws._map_paths(state, encode=True)
    assert encoded["versions"][0]["train"].startswith("managed://")
    assert encoded["versions"][0]["learned"] == opaque
    assert ws._map_paths(encoded, encode=False) == state


def test_motoro_queued_and_native_snapshot_locators_relocate_without_payload_changes(clean_storage, monkeypatch):
    from motoro.engine.context import RunContext
    from motoro.schemas.agent import ModelConfig
    from scikit_learn_mcp import server as sklearn_server

    from asaree.mcp_servers import script_server

    old = storage_roots()
    new = {key: clean_storage / "new" / key for key in old}
    legacy = clean_storage / "legacy-attempts"
    script = legacy / "attempt/scripts/script.py"
    atomic_write(script, b"print('exact')")
    row = old["workspaces"] / "attempt/row.csv"
    atomic_write(row, b"x\nexact\n")
    opaque = {"path": str(script), "train": str(row)}
    ambient = {"script_path": str(script), "script_paths": [{"id": "script", "path": str(script)}],
               "data_path": str(row), "dataset_mode": "per_row",
               "row_inputs": [{"path": str(row), "mode": "per_row", "values": opaque}],
               "data_slots": {"slot": {"data_path": str(row), "name": str(row)}}, "opaque": opaque}
    metadata = {"ambient_meta": ambient, "opaque": opaque}
    snapshot = RunContext(agent_goal="goal", system_prompt="prompt", model_config=ModelConfig(), user_input="input",
                          ambient_meta=ambient, metadata=metadata, conversation_history=[opaque]).to_snapshot()
    databases = {"motoro": {"agent_runs": [{"id": "run", "run_metadata": metadata, "state_snapshot": snapshot}]}}
    plan = make_plan(old, new, databases, set(), legacy)
    assert not plan["problems"]
    assert {update["column"] for update in plan["updates"]} == {"run_metadata", "state_snapshot"}
    copy_files(plan)
    shutil.rmtree(clean_storage / "data")
    shutil.rmtree(legacy)
    monkeypatch.setenv("ASAREE_DATA_ROOT", str(clean_storage / "new"))
    # New namespace directories deliberately match storage_roots' defaults.
    for namespace, path in new.items():
        from asaree_workspace_core.storage import NAMESPACES

        monkeypatch.setenv(f"ASAREE_{NAMESPACES[namespace][0].upper()}", str(path))
    for update in plan["updates"]:
        migrated = update["after"]
        assert migrated["ambient_meta"]["opaque"] == opaque
        assert migrated["ambient_meta"]["row_inputs"][0]["values"] == opaque
        assert migrated["ambient_meta"]["data_slots"]["slot"]["name"] == str(row)
        if update["column"] == "state_snapshot":
            assert migrated["metadata"] == metadata
            assert migrated["conversation_history"] == [opaque]
            assert RunContext.from_snapshot(migrated).ambient_meta == migrated["ambient_meta"]
        ctx = SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(
            model_extra={f"motoro.ambient.{key}": value for key, value in migrated["ambient_meta"].items()})))
        resolved_script = script_server._wired_scripts(ctx)[0]["path"]
        assert Path(resolved_script).read_bytes() == b"print('exact')"
        resolved_row = script_server._runtime_manifest(ctx, "")["training_inputs"][0]["path"]
        assert Path(resolved_row).read_bytes() == b"x\nexact\n"
        assert sklearn_server._ambient_script_path(ctx) == resolved_script
        assert sklearn_server._resolve_dataset("", "", ctx, target_required=False)[0] == resolved_row


def test_new_dispatch_ambient_references_are_durable_and_payloads_opaque(clean_storage):
    from asaree.services.protocol_execution import _durable_ambient

    path = str(storage_roots()["workspaces"] / "attempt/script.py")
    values = {"path": path, "train": path}
    ambient = {"script_path": path, "row_inputs": [{"path": path, "values": values}], "dataset_row": values}
    durable = _durable_ambient(ambient)
    assert durable["script_path"] == "managed://workspaces/attempt/script.py"
    assert durable["row_inputs"][0]["values"] == values
    assert durable["dataset_row"] == values


def test_script_cleanup_without_scripts_needs_no_product_storage_settings(monkeypatch):
    from asaree.services import protocol_execution

    def unexpected_storage_read():
        raise AssertionError("No scripts require no workspace storage lookup")

    monkeypatch.setattr(protocol_execution, "_workspace_storage_root", unexpected_storage_read)
    protocol_execution._cleanup_adhoc_scripts(None)
    protocol_execution._cleanup_adhoc_scripts({"dataset_mode": "per_row", "script_paths": []})


@pytest.mark.skipif(not os.environ.get("ASAREE_MIGRATIONS_TEST_DATABASE_URL"),
                    reason="Disposable migration PostgreSQL not configured")
async def test_two_database_inventory_apply_verify_and_rollback(tmp_path, monkeypatch, clean_storage):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from asaree import config
    from asaree.migrations import upgrade
    from asaree.storage_migration import database_inventory, update_databases

    admin_url = os.environ["ASAREE_MIGRATIONS_TEST_DATABASE_URL"]
    names = {key: f"storage_{key}_{uuid.uuid4().hex}" for key in ("asaree", "motoro")}
    urls = {key: f"{admin_url.rpartition('/')[0]}/{name}" for key, name in names.items()}
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    engines = {key: create_async_engine(url) for key, url in urls.items()}
    settings = AsareeSettings(_env_file=None, product_database_url=urls["asaree"], database_url=urls["motoro"],
                              data_root=str(tmp_path / "new"))
    monkeypatch.setattr(config, "_instance", settings)
    old = {key: tmp_path / "old" / key for key in storage_roots()}
    source = old["datasets"] / "source.csv"
    atomic_write(source, b"x\n1\n")
    atomic_write(old["bundles"] / "owner/upload/index.md", b"managed knowledge")
    owner, dataset, server, run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    command = f"python -m asaree.mcp_servers.okf_bundle --bundle {old['bundles']}/owner/upload"
    ambient = {"data_path": str(source), "opaque": {"path": str(source)}}
    metadata = {"ambient_meta": ambient, "opaque": str(source)}
    snapshot = {"ambient_meta": ambient, "metadata": metadata, "conversation_history": [str(source)]}
    try:
        async with admin.connect() as connection:
            for name in names.values():
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
        await asyncio.to_thread(upgrade, urls["asaree"], "head")
        async with engines["asaree"].begin() as connection:
            await connection.execute(text("INSERT INTO users (id,email,hashed_password,display_name) "
                                          "VALUES (:id,'storage@test.example','unused','Storage')"), {"id": owner})
            await connection.execute(text("INSERT INTO registered_datasets (id,owner_id,name,raw_path) "
                                          "VALUES (:id,:owner,'legacy',:path)"),
                                     {"id": dataset, "owner": owner, "path": str(source)})
        async with engines["motoro"].begin() as connection:
            # Only the Motoro columns consumed by the migration; no ASAREE
            # tables are introduced into that database.
            await connection.execute(text("CREATE TABLE mcp_server_configs "
                                          "(id uuid PRIMARY KEY, name text, owner_id uuid, command text)"))
            await connection.execute(text("CREATE TABLE agent_runs "
                                          "(id uuid PRIMARY KEY, status text, run_metadata json, state_snapshot json)"))
            await connection.execute(text("INSERT INTO mcp_server_configs VALUES (:id,'uploaded',:owner,:command)"),
                                     {"id": server, "owner": owner, "command": command})
            await connection.execute(text("INSERT INTO agent_runs VALUES "
                                          "(:id,'pending',CAST(:metadata AS json),CAST(:snapshot AS json))"),
                                     {"id": run, "metadata": json.dumps(metadata), "snapshot": json.dumps(snapshot)})
        inventory = await database_inventory()
        plan = make_plan(old, managed_storage.roots(), inventory, {str(server)})
        assert not plan["problems"]
        assert len(plan["updates"]) == 4
        copy_files(plan)
        await update_databases(plan)
        await update_databases(plan)  # recovery after separate database transactions
        await update_databases(plan, verify_only=True)
        assert source.read_bytes() == b"x\n1\n"
        async with engines["motoro"].connect() as connection:
            migrated = (await connection.execute(text("SELECT run_metadata,state_snapshot FROM agent_runs"))).one()
            for value in migrated:
                assert value["ambient_meta"]["data_path"] == "managed://datasets/source.csv"
                assert value["ambient_meta"]["opaque"] == ambient["opaque"]
            assert migrated.state_snapshot["metadata"] == metadata
        await update_databases(plan, rollback=True)
        await update_databases(plan, rollback=True)
        async with engines["asaree"].connect() as connection:
            assert await connection.scalar(text("SELECT raw_path FROM registered_datasets")) == str(source)
        async with engines["motoro"].connect() as connection:
            assert await connection.scalar(text("SELECT command FROM mcp_server_configs")) == command
            restored = (await connection.execute(text("SELECT run_metadata,state_snapshot FROM agent_runs"))).one()
            assert restored.run_metadata == metadata
            assert restored.state_snapshot == snapshot
    finally:
        for engine in engines.values():
            await engine.dispose()
        async with admin.connect() as connection:
            for name in names.values():
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await admin.dispose()
