"""Regression loops for export authorization and structural scratch selection."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_experiment_packages import unpack, writer_at

from asaree.services import experiment_packages as packages


@pytest.fixture
def knowledge_registry(tmp_path, monkeypatch):
    owner = uuid.uuid4()
    browse = tmp_path / "shared-knowledge" / "browse"
    roots = {name: tmp_path / "platform" / name for name in ("datasets", "workspaces", "bundles", "documents")}
    grants = {}
    monkeypatch.setattr(packages.managed_storage, "roots", lambda: roots)
    monkeypatch.setattr(packages.okf_bundles, "bundle_root", lambda: browse)
    monkeypatch.setattr(packages.okf_bundles, "owner_upload_root", lambda identity: roots["bundles"] / str(identity))
    monkeypatch.setattr(packages.okf_documents, "owner_root", lambda identity: roots["documents"] / str(identity))
    monkeypatch.setattr(packages, "get_settings", lambda: SimpleNamespace(knowledge_export_grants=grants))
    capture = Mock(wraps=packages.capture_tree)
    monkeypatch.setattr(packages, "capture_tree", capture)

    def registration(path, identity=None, name="okf-bundle-selected"):
        return SimpleNamespace(id=uuid.uuid4(), owner_id=identity or owner, name=name,
                               transport=SimpleNamespace(value="stdio"), capabilities={},
                               command=f"python -m asaree.mcp_servers.okf_bundle --bundle {path}")

    def database(server, foreign=()):
        async def stream(_query):
            class Rows:
                close = AsyncMock()

                def __aiter__(self):
                    async def items():
                        for row in foreign:
                            yield row
                    return items()
            return Rows()
        return SimpleNamespace(scalar=AsyncMock(return_value=server), stream=AsyncMock(side_effect=stream))

    return SimpleNamespace(owner=owner, browse=browse, roots=roots, grants=grants,
                           registration=registration, database=database, capture=capture)


@pytest.mark.parametrize("overlap", ["browse_root", "browse_ancestor", "foreign_nested", "foreign_ancestor",
                                     "foreign_exact", "ownerless_exact"])
async def test_server_never_serializes_unauthorized_external_knowledge(tmp_path, knowledge_registry, overlap):
    registry = knowledge_registry
    owner, foreign, browse = registry.owner, uuid.uuid4(), registry.browse
    selected = {"browse_root": browse, "browse_ancestor": browse.parent}.get(overlap, browse / "registered-bundle")
    selected.mkdir(parents=True)
    private = selected / "another-researcher.md"
    private.write_bytes(b"FOREIGN OWNER CONTENT MUST NOT APPEAR")
    foreign_root = selected / "nested" if overlap == "foreign_nested" else (
        selected.parent if overlap == "foreign_ancestor" else selected
    )
    if overlap == "foreign_nested":
        foreign_root.mkdir()
        private.rename(foreign_root / private.name)
    registry.grants[owner] = [selected]  # Overlap rejection must also hold with a valid exact grant.
    server = registry.registration(selected)
    other = registry.registration(foreign_root, foreign, "okf-bundle-foreign")
    if overlap == "ownerless_exact":
        other.owner_id = None
    db = registry.database(server, [other])
    writer = writer_at(tmp_path)
    await packages._server(db, writer, {"bundle_id": str(server.id)}, owner, knowledge=True)
    result = writer.finish({})
    contents = unpack(tmp_path)
    assert all(b"FOREIGN OWNER CONTENT MUST NOT APPEAR" not in value for value in contents.values())
    assert result["completeness"]["status"] == "incomplete"
    dependencies = [json.loads(line) for line in contents["dependencies.jsonl"].splitlines()]
    assert any(item["kind"] == "knowledge" and item["status"] == "unresolved" for item in dependencies)
    assert str(foreign).encode() not in b"".join(contents.values())
    assert str(other.id).encode() not in b"".join(contents.values())
    registry.capture.assert_not_called()
    if not overlap.startswith("browse_"):
        db.stream.assert_awaited_once()


def test_operator_grants_are_parsed_as_owner_uuids_and_paths_without_environment_files():
    from asaree.config import AsareeSettings

    owner = uuid.uuid4()
    settings = AsareeSettings(_env_file=None, knowledge_export_grants={str(owner): ["/srv/knowledge/cohort"]})
    assert settings.knowledge_export_grants == {owner: [Path("/srv/knowledge/cohort")]}


@pytest.mark.parametrize("location", ["bundle_upload", "document_upload", "external", "legacy_external"])
async def test_server_serializes_authorized_owned_and_granted_knowledge(tmp_path, knowledge_registry, location):
    registry = knowledge_registry
    if location in {"bundle_upload", "document_upload"}:
        namespace = "bundles" if location == "bundle_upload" else "documents"
        selected = registry.roots[namespace] / str(registry.owner) / "owned"
    else:
        selected = registry.browse / "named-bundle" if location == "external" else tmp_path / "legacy" / "bundle"
        registry.grants[registry.owner] = [selected]
    selected.mkdir(parents=True)
    (selected / "index.md").write_bytes(b"AUTHORIZED KNOWLEDGE")
    server = registry.registration(selected, name="okf-doc-owned" if location == "document_upload" else
                                   "okf-bundle-selected")
    db = registry.database(server)
    writer = writer_at(tmp_path)
    await packages._server(db, writer, {"bundle_id": str(server.id)}, registry.owner, knowledge=True)
    result = writer.finish({})
    assert unpack(tmp_path)[f"resources/knowledge/{server.id}/index.md"] == b"AUTHORIZED KNOWLEDGE"
    assert result["completeness"]["status"] == "complete"
    db.stream.assert_awaited_once()
    registry.capture.assert_called_once()


@pytest.mark.parametrize("grant", ["missing", "parent", "child", "foreign_parent", "foreign_child", "foreign_exact"])
async def test_grants_are_exact_and_do_not_override_foreign_grant_boundaries(tmp_path, knowledge_registry, grant):
    registry = knowledge_registry
    selected = tmp_path / "legacy" / "bundle"
    selected.mkdir(parents=True)
    (selected / "index.md").write_bytes(b"UNAUTHORIZED CONTENT")
    if grant in {"parent", "child"}:
        registry.grants[registry.owner] = [selected.parent if grant == "parent" else selected / "child"]
    elif grant.startswith("foreign_"):
        registry.grants[registry.owner] = [selected]
        registry.grants[uuid.uuid4()] = [{"foreign_parent": selected.parent, "foreign_child": selected / "child",
                                        "foreign_exact": selected}[grant]]
    server = registry.registration(selected)
    writer = writer_at(tmp_path)
    await packages._server(registry.database(server), writer, {"bundle_id": str(server.id)}, registry.owner,
                           knowledge=True)
    result = writer.finish({})
    assert result["completeness"]["status"] == "incomplete"
    assert all(b"UNAUTHORIZED CONTENT" not in value for value in unpack(tmp_path).values())
    registry.capture.assert_not_called()


@pytest.mark.parametrize("missing", ["malformed_grant", "unreadable_registry", "unparseable_registration"])
async def test_missing_boundary_evidence_is_unresolved_before_capture(tmp_path, knowledge_registry, missing):
    from sqlalchemy.exc import SQLAlchemyError

    registry = knowledge_registry
    selected = tmp_path / "legacy-bundle"
    selected.mkdir()
    (selected / "index.md").write_bytes(b"MUST NOT CAPTURE WITHOUT AUTHORIZATION EVIDENCE")
    registry.grants[registry.owner] = [selected]
    server = registry.registration(selected)
    foreign = registry.registration(tmp_path / "other", uuid.uuid4(), "okf-doc-foreign")
    foreign.command = "unparseable foreign registration"
    db = registry.database(server, [foreign] if missing == "unparseable_registration" else [])
    if missing == "malformed_grant":
        registry.grants[registry.owner] = [None]
    elif missing == "unreadable_registry":
        db.stream.side_effect = SQLAlchemyError("PRIVATE REGISTRY DETAILS")
    writer = writer_at(tmp_path)
    await packages._server(db, writer, {"bundle_id": str(server.id)}, registry.owner, knowledge=True)
    manifest = writer.finish({})
    contents = unpack(tmp_path)
    assert manifest["completeness"]["status"] == "incomplete"
    assert not any(name.startswith("resources/knowledge/") for name in contents)
    assert b"PRIVATE REGISTRY DETAILS" not in b"".join(contents.values())
    registry.capture.assert_not_called()


@pytest.mark.parametrize(("row_mode", "verification"), [(False, "durable_outputs"), (False, "platform_scratch"),
                                                       (True, "durable_outputs")])
async def test_wired_script_user_directories_survive_and_actual_workspace_scratch_does_not(
    tmp_path, monkeypatch, verification, row_mode,
):
    import pandas as pd
    from asaree_workspace_core import Workspace

    from asaree.mcp_servers import script_server, workspace_server
    from asaree.services import protocol_execution

    run, experiment = uuid.uuid4(), uuid.uuid4()
    root = tmp_path / "workspaces"
    workspace_id = f"_protocol_runs/{run}" if row_mode else f"{experiment}/{run}"
    monkeypatch.setattr(protocol_execution, "_workspace_storage_root", lambda: root)
    monkeypatch.setattr(packages.managed_storage, "roots", lambda: {"workspaces": root})
    source = (
        "from pathlib import Path\n"
        "for name in ('working', 'cache', 'scratch', '__pycache__', '.scratch', "
        "'results.preparing', '.report.preparing'):\n"
        "    output = Path('outputs') / name / 'prediction.csv'\n"
        "    output.parent.mkdir(parents=True, exist_ok=True)\n"
        "    output.write_text('durable prediction')\n"
    )
    script = protocol_execution._materialize_script(workspace_id, "script", source)
    ctx = SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(model_extra={
        "motoro.ambient.script_path": script,
    })))
    result = json.loads(script_server.run_wired_script(ctx=ctx))
    assert result["exit_code"] == 0
    for slot in (() if row_mode else (None, "dataset:cohort", "agent:worker")):
        seed = tmp_path / f"seed-{slot or 'default'}.parquet"
        pd.DataFrame({"x": [1], "y": [0]}).to_parquet(seed, index=False)
        ws = Workspace.open(workspace_id, root=str(root), slot=slot, target_column="y",
                            seed_train_path=str(seed), seed_test_path=str(seed))
        scratch = workspace_server._scratch_dir(ws, "dc")
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "unretained.tmp").write_text("PLATFORM SCRATCH MUST NOT APPEAR")
        (ws.slot_dir / ".train.parquet.abcdefgh.preparing").write_text("PLATFORM PREPARATION MUST NOT APPEAR")
    writer = writer_at(tmp_path)
    await packages._attempt_files(writer, SimpleNamespace(experiment_id=experiment), run,
                                  {"nodes": [{"id": "script", "type": "script"}]})
    writer.finish({})
    contents = unpack(tmp_path)
    if verification == "durable_outputs":
        for name in ("working", "cache", "scratch", "__pycache__", ".scratch", "results.preparing",
                     ".report.preparing"):
            assert any(path.endswith(f"scripts/outputs/{name}/prediction.csv") for path in contents)
    else:
        assert all(b"PLATFORM SCRATCH MUST NOT APPEAR" not in value for value in contents.values())
        assert all(b"PLATFORM PREPARATION MUST NOT APPEAR" not in value for value in contents.values())


@pytest.mark.parametrize("placement", ["workspace", "standalone_agent"])
async def test_nested_script_outputs_keep_source_like_names_but_materialized_sources_are_excluded(
    tmp_path, monkeypatch, placement,
):
    from asaree.mcp_servers import script_server
    from asaree.services import protocol_execution

    run, experiment = uuid.uuid4(), uuid.uuid4()
    root = tmp_path / "workspaces"
    monkeypatch.setattr(protocol_execution, "_workspace_storage_root", lambda: root)
    monkeypatch.setattr(packages.managed_storage, "roots", lambda: {"workspaces": root})
    materialization = f"{experiment}/{run}" if placement == "workspace" else (
        protocol_execution._script_workspace_id(None, run, "worker")
    )
    source = (
        "from pathlib import Path\n"
        "for directory in ('outputs', 'outputs/scripts'):\n"
        "    Path(directory).mkdir(parents=True, exist_ok=True)\n"
        "    for name in ('script.py', 'inline-0123456789ab.py'):\n"
        "        (Path(directory) / name).write_text('durable Script output')\n"
    )
    script = Path(protocol_execution._materialize_script(materialization, "script", source))
    inline = Path(protocol_execution._materialize_script(materialization, "inline-0123456789ab", "print('source')"))
    ctx = SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(model_extra={
        "motoro.ambient.script_path": str(script),
    })))
    assert json.loads(script_server.run_wired_script(ctx=ctx))["exit_code"] == 0
    assert script.is_file() and inline.is_file()
    writer = writer_at(tmp_path)
    await packages._attempt_files(writer, SimpleNamespace(experiment_id=experiment), run, {"nodes": [
        {"id": "script", "type": "script"}, {"id": "worker", "type": "agent"},
    ]})
    writer.finish({})
    contents = unpack(tmp_path)
    attempt = root / str(experiment) / str(run) if placement == "workspace" else root / "_protocol_runs" / str(run)
    prefix = f"results/artifacts/{run}/"
    for file in (script, inline):
        assert prefix + file.relative_to(attempt).as_posix() not in contents
    for directory in ("outputs", "outputs/scripts"):
        for name in ("script.py", "inline-0123456789ab.py"):
            member = prefix + (script.parent / directory / name).relative_to(attempt).as_posix()
            assert contents[member] == b"durable Script output"


async def test_row_inputs_are_excluded_only_at_the_producers_exact_row_location(tmp_path, monkeypatch):
    from asaree.mcp_servers import script_server
    from asaree.services import dataset_row_workspaces, protocol_execution
    from asaree.services.dataset_row_csv import RowSource

    run, whole_run, experiment, dataset = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    root = tmp_path / "workspaces"
    monkeypatch.setattr(dataset_row_workspaces, "_workspace_root", lambda: root)
    monkeypatch.setattr(protocol_execution, "_workspace_storage_root", lambda: root)
    monkeypatch.setattr(packages.managed_storage, "roots", lambda: {"workspaces": root})
    prepared = dataset_row_workspaces.prepare_agent_row_inputs(run_id=run, agent_node_id="worker",
        source=RowSource(str(dataset), "0" * 64, ("x",), (("exact",),)),
        bindings=[{"agent_node_id": "worker", "dataset_id": str(dataset), "dataset_name": "cohort",
                   "row_index": 0, "columns": ["x"], "target_column": ""}])
    snapshot = Path(prepared["row_inputs"][0]["path"])
    row_attempt = root / "_protocol_runs" / str(run)
    whole_attempt = root / str(experiment) / str(whole_run)
    whole_agent = whole_attempt / "agents" / snapshot.parent.name
    outputs = [snapshot.parent / "outputs" / snapshot.name, snapshot.parent / "outputs" / "row-view.json",
               whole_agent / snapshot.name, whole_agent / "row-view.json",
               row_attempt / "agents" / "user-output" / snapshot.name,
               row_attempt / "agents" / "user-output" / "row-view.json"]
    source = (
        "from pathlib import Path\n"
        f"for filename in {[str(path) for path in outputs]!r}:\n"
        "    path = Path(filename)\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    path.write_text('durable row output')\n"
    )
    script = protocol_execution._materialize_script(prepared["workspace_id"], "script", source)
    ctx = SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(model_extra={
        "motoro.ambient.script_path": script,
    })))
    assert json.loads(script_server.run_wired_script(ctx=ctx))["exit_code"] == 0
    assert snapshot.is_file() and snapshot.with_name("row-view.json").is_file()
    writer = writer_at(tmp_path)
    for attempt_id in (run, whole_run):
        await packages._attempt_files(writer, SimpleNamespace(experiment_id=experiment), attempt_id,
                                      {"nodes": [{"id": "script", "type": "script"}]})
    writer.finish({})
    contents = unpack(tmp_path)
    prefix = f"results/artifacts/{run}/"
    assert prefix + snapshot.relative_to(row_attempt).as_posix() not in contents
    assert prefix + snapshot.with_name("row-view.json").relative_to(row_attempt).as_posix() not in contents
    for path in outputs:
        attempt = whole_attempt if path.is_relative_to(whole_attempt) else row_attempt
        attempt_id = whole_run if attempt == whole_attempt else run
        member = f"results/artifacts/{attempt_id}/" + path.relative_to(attempt).as_posix()
        assert contents[member] == b"durable row output"
