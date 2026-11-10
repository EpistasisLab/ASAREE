"""Portable package contracts without infrastructure; PostgreSQL checks live below."""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from asaree.api import result_jobs as api
from asaree.services import experiment_packages as packages
from asaree.services import result_exports
from asaree.services.result_reads import ResultScope


def writer_at(tmp_path):
    staging = tmp_path / "stage"
    staging.mkdir()
    return packages.PackageWriter(tmp_path / "package.tar.gz", staging)


def unpack(tmp_path):
    import tarfile

    with tarfile.open(tmp_path / "package.tar.gz") as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def test_archive_hashes_inventory_and_tar_metadata_are_bounded(tmp_path):
    writer = writer_at(tmp_path)
    for number in range(1250):
        writer.json(f"records/{number}.json", {"number": number}, identity=str(number))
        assert not writer.tar.members
    writer.dependency("missing", "dataset", "publication_pinned", status="missing")
    result = writer.finish({"schema_version": 1})
    contents = unpack(tmp_path)
    inventory = [json.loads(line) for line in contents["files.jsonl"].splitlines()]
    assert result["completeness"] == {"status": "incomplete", "unresolved_count": 1}
    assert result["file_count"] == 1251
    for record in [*inventory, result["files"]]:
        assert record["sha256"] == hashlib.sha256(contents[record["path"]]).hexdigest()
        assert not Path(record["path"]).is_absolute()
    assert len(contents) == 1253


def test_exact_opaque_bytes_credentials_and_mismatched_source(tmp_path):
    writer = writer_at(tmp_path)
    source = tmp_path / "dataset.csv"
    original = b'api_key,password,path\r\n"not a credential",exact,/app/data/original\r\n'
    source.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    writer.file(source, "inputs/source.csv", identity="source", provenance="publication_pinned", expected=digest)
    with pytest.raises(ValueError, match="hash mismatch"):
        writer.file(source, "bad.csv", identity="source", provenance="publication_pinned", expected="0" * 64)
    writer.finish({})
    assert unpack(tmp_path)["inputs/source.csv"] == original
    assert "bad.csv" not in unpack(tmp_path)


def test_only_schema_owned_secret_fields_are_removed_including_factor_alternatives():
    literal = {"password": "research outcome", "api_key": "dataset column", "path": "/app/data/exact"}
    graph = {"nodes": [{"id": "script", "type": "script", "data": {
        "config": {"code": "print('password=exact')", "api_key": "SECRET", "arguments": literal},
        "factor_bindings": {"config": "variant"},
    }}], "edges": []}
    settings = {"design_spec": {"factors": [{"name": "variant", "levels": [
        {"code": "print('api_key=exact')", "password": "SECRET", "arguments": literal},
    ]}]}, "task_brief": literal}
    clean_graph, clean_settings = packages.portable_definition(graph, settings)
    assert "SECRET" not in json.dumps((clean_graph, clean_settings))
    assert clean_graph["nodes"][0]["data"]["config"]["arguments"] == literal
    assert clean_settings["task_brief"] == literal
    assert graph["nodes"][0]["data"]["config"]["api_key"] == "SECRET"
    assert clean_settings["design_spec"]["factors"][0]["levels"][0]["code"] == "print('api_key=exact')"


def test_node_variants_cover_whole_configuration_and_individual_field_levels():
    node = {"id": "s", "type": "skill", "data": {
        "config": {"skill_id": "baseline"}, "factor_bindings": {"config.skill_id": "Skill"},
    }}
    variants = list(packages.node_variants(node, {"factors": [
        {"name": "Skill", "levels": ["historical-a", "historical-b"]},
        {"name": "Unrelated", "levels": list(range(10000))},
    ]}))
    assert [item["data"]["config"]["skill_id"] for item in variants] == ["baseline", "historical-a", "historical-b"]


def test_many_factors_on_one_node_have_linear_dependency_work():
    factors = [{"name": f"factor-{number}", "levels": list(range(10))} for number in range(12)]
    node = {"id": "agent", "type": "agent", "data": {"config": {}, "factor_bindings": {
        f"config.field_{number}": factor["name"] for number, factor in enumerate(factors)
    }}}
    assert sum(1 for _ in packages.node_variants(node, {"factors": factors})) == 121


def test_real_sampling_snapshot_and_nested_fallback_credentials_never_leave_archive(tmp_path):
    from motoro.schemas.agent import ModelConfig

    from asaree.services.sampling_snapshots import _pin_config

    secret, fallback_secret = "PRIMARY-SECRET-55", "FALLBACK-SECRET-55"
    config = ModelConfig(provider="openai", model="gpt-4o", api_key=secret, fallback_models=[
        ModelConfig(provider="openai", model="gpt-4o-mini", api_key=fallback_secret),
    ])
    snapshot = _pin_config(config)
    # Native dumps exclude api_key today. Legacy/raw graph dictionaries can
    # still contain credentials, including nested fallback configurations.
    raw_config = config.model_dump(mode="json")
    raw_config["api_key"] = secret
    raw_config["fallback_models"][0]["api_key"] = fallback_secret
    snapshot["explicit_input"] = raw_config
    snapshot["config"]["api_key"] = secret
    snapshot["fallbacks"][0]["input"]["api_key"] = fallback_secret
    snapshot["discovery_context"] = {"api_base": "https://user:SECRET@provider.test", "model": "gpt-4o"}
    graph = {"nodes": [{"id": "model", "type": "model_openai", "data": {
        "config": raw_config, "factor_bindings": {"config": "Model"},
    }}], "edges": [], "sampling_snapshot": {"original-key": snapshot}}
    settings = {"design_spec": {"factors": [{"name": "Model", "levels": [raw_config]}]}}
    graph, settings = packages.portable_definition(graph, settings)
    writer = writer_at(tmp_path)
    writer.json("canvas.json", graph)
    writer.json("settings.json", settings)
    writer.json("cell.json", packages.safe_factor_values({"Model": raw_config}, graph))
    writer.finish({})
    for content in unpack(tmp_path).values():
        assert secret.encode() not in content
        assert fallback_secret.encode() not in content
        assert b"https://user:SECRET" not in content
    assert "original-key" not in graph["sampling_snapshot"]
    assert "gpt-4o-mini" in json.dumps(graph)


async def test_cancelled_archive_thread_is_stopped_and_joined_before_return(tmp_path):
    import asyncio
    import threading
    import time

    writer = writer_at(tmp_path)
    started, stopped = threading.Event(), threading.Event()

    def capture():
        started.set()
        try:
            while True:
                writer.check()
                time.sleep(0.005)
        finally:
            stopped.set()

    task = asyncio.create_task(packages.archive_io(writer, capture))
    await asyncio.to_thread(started.wait)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    writer.close()


def test_tree_captures_all_resources_excludes_credential_files_and_rejects_symlinks(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "index.md").write_bytes(b"exact\r\n")
    (root / "opaque.sqlite").write_bytes(b"user resource, not a platform row index")
    (root / ".env").write_bytes(b"DO NOT READ")
    writer = writer_at(tmp_path)
    packages.capture_tree(writer, root, "knowledge/id", "id")
    writer.finish({})
    contents = unpack(tmp_path)
    assert contents["knowledge/id/index.md"] == b"exact\r\n"
    assert contents["knowledge/id/opaque.sqlite"] == b"user resource, not a platform row index"
    assert "knowledge/id/.env" not in contents
    (root / "link.md").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="symlink"):
        list(packages._tree_files(root))
    with pytest.raises(OSError):
        packages.open_resource(root / "link.md")


def test_tree_change_is_explicit_not_a_successful_capture(tmp_path, monkeypatch):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "one.md").write_text("first")
    writer = writer_at(tmp_path)
    original = writer.file

    def mutate(*args, **kwargs):
        record = original(*args, **kwargs)
        (root / "one.md").write_text("changed after copying")
        return record

    monkeypatch.setattr(writer, "file", mutate)
    with pytest.raises(ValueError, match="changed during capture"):
        packages.capture_tree(writer, root, "knowledge/id", "id")
    writer.close()


def test_external_legacy_bundle_is_allowed_but_platform_overlaps_are_not(tmp_path, monkeypatch):
    roots = {name: tmp_path / "data" / name for name in ("datasets", "workspaces", "backups", "bundles", "scratch")}
    monkeypatch.setattr(packages.managed_storage, "roots", lambda: roots)
    # Older registrations may predate today's narrower browse jail.
    packages.validate_knowledge_root(tmp_path / "data" / "legacy-knowledge", tmp_path / "browse", external=True)
    for unsafe in (tmp_path / "data", roots["datasets"] / "other-user", roots["backups"], roots["bundles"]):
        with pytest.raises(ValueError, match="overlaps"):
            packages.validate_knowledge_root(unsafe, tmp_path / "browse", external=True)
    with pytest.raises(ValueError, match="outside owner"):
        packages.validate_knowledge_root(tmp_path / "another-owner", roots["bundles"] / "owner", external=False)


async def test_dataset_pin_reads_exact_artifact_even_after_current_split_changes(tmp_path):
    writer = writer_at(tmp_path)
    dataset, artifact_id = uuid.uuid4(), uuid.uuid4()
    source = tmp_path / "original.csv"
    source.write_bytes(b"x\noriginal\n")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    pin = {"dataset_id": str(dataset), "raw_sha256": digest, "split_id": None,
           "raw_artifact": {"id": str(artifact_id), "sha256": digest}, "train_artifact": None, "test_artifact": None}
    registration = SimpleNamespace(id=dataset, raw_sha256=digest, current_split_id=uuid.uuid4(),
        name="Pinned source", description=None, dictionary_json=None, _target_column=None,
        raw_artifact_id=artifact_id, created_at=datetime.now(UTC))
    artifact = SimpleNamespace(dataset_id=dataset, status="ready", sha256=digest, path=str(source), format="csv")
    db = SimpleNamespace(scalar=AsyncMock(return_value=registration), get=AsyncMock(return_value=artifact))
    await packages._datasets(
        db, writer, SimpleNamespace(id=uuid.uuid4(), dataset_inputs={"inputs": [pin]}), uuid.uuid4(),
    )
    writer.finish({})
    assert unpack(tmp_path)[f"inputs/files/{artifact_id}.csv"] == source.read_bytes()
    assert "current_split_id" not in str(db.scalar.call_args)


async def test_missing_and_foreign_datasets_never_substitute_current_bytes(tmp_path):
    writer = writer_at(tmp_path)
    pin = {"dataset_id": str(uuid.uuid4()), "raw_sha256": "0" * 64}
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    await packages._datasets(
        db, writer, SimpleNamespace(id=uuid.uuid4(), dataset_inputs={"inputs": [pin]}), uuid.uuid4(),
    )
    result = writer.finish({})
    assert result["completeness"]["status"] == "incomplete"
    assert all(not name.startswith("inputs/files/") for name in unpack(tmp_path))


async def test_results_evidence_is_sliced_in_sql_without_loading_whole_document(tmp_path):
    text = json.dumps({"large": "é" * (packages.SQL_BLOCK * 3)})
    offsets = []

    async def scalar(statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        offset, size = compiled.params["substr_2"], compiled.params["substr_3"]
        offsets.append((offset, size))
        return text[offset - 1:offset - 1 + size]

    from sqlalchemy import Text, cast, select

    from asaree.models.protocol_run import ProtocolRun

    path = tmp_path / "result.json"
    await packages.stream_document(SimpleNamespace(scalar=scalar), select(cast(ProtocolRun.attempt_result, Text)), path)
    assert path.read_text() == text
    assert len(offsets) >= 3
    assert all(size == packages.SQL_BLOCK for _, size in offsets)


async def test_skill_capture_has_binary_resources_and_owner_predicate(tmp_path):
    import base64

    writer = writer_at(tmp_path)
    identity, owner = uuid.uuid4(), uuid.uuid4()
    skill = SimpleNamespace(id=identity, name="bundle", description="A bundled skill", body="Exact instructions",
                            frontmatter={"metadata": {"research": "opaque"}}, updated_at=datetime.now(UTC),
                            source_filename="bundle.md", created_at=datetime.now(UTC))
    binary = b"\x00\x81\xffRESOURCE"

    async def stream(_query):
        async def rows():
            yield "reference.md", "exact\r\n", "utf-8", uuid.uuid4(), 0
            yield "assets/image.bin", base64.b64encode(binary).decode(), "base64", uuid.uuid4(), 1
            yield ".env", "MUST NOT APPEAR", "utf-8", uuid.uuid4(), 2
        return rows()

    db = SimpleNamespace(scalar=AsyncMock(return_value=skill), stream=stream)
    await packages._skill(db, writer, str(identity), owner)
    query = db.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert owner in query.params.values() and "is_system IS true" in str(query)
    writer.finish({})
    contents = unpack(tmp_path)
    assert contents[f"resources/skills/{identity}/assets/image.bin"] == binary
    assert contents[f"resources/skills/{identity}/reference.md"] == b"exact\r\n"
    assert all(b"MUST NOT APPEAR" not in content for content in contents.values())


async def test_dependency_capture_visits_every_factor_alternative_not_only_baseline(tmp_path, monkeypatch):
    writer = writer_at(tmp_path)
    graph = {"nodes": [{"id": "skill", "type": "skill", "data": {
        "config": {"skill_id": "baseline"}, "factor_bindings": {"config.skill_id": "Skill"},
    }}, {"id": "knowledge", "type": "okf_bundle", "data": {
        "config": {"bundle_id": "baseline"}, "factor_bindings": {"config": "Knowledge"},
    }}], "edges": []}

    @asynccontextmanager
    async def session(**_kwargs):
        yield SimpleNamespace(connection=AsyncMock())

    monkeypatch.setattr(packages, "system_session", session)
    skill, server = AsyncMock(), AsyncMock()
    monkeypatch.setattr(packages, "_skill", skill)
    monkeypatch.setattr(packages, "_server", server)
    await packages._resources(writer, graph, {"design_spec": {"factors": [
        {"name": "Skill", "levels": ["skill-a", "skill-b"]},
        {"name": "Knowledge", "levels": [{"bundle_id": "knowledge-a"}, {"bundle_id": "knowledge-b"}]},
    ]}}, uuid.uuid4())
    assert [call.args[2] for call in skill.call_args_list] == ["baseline", "skill-a", "skill-b"]
    assert [call.args[2]["bundle_id"] for call in server.call_args_list] == ["baseline", "knowledge-a", "knowledge-b"]
    writer.close()


@pytest.mark.parametrize("knowledge_first", [False, True])
async def test_knowledge_capture_is_not_suppressed_by_tool_alias_or_node_order(tmp_path, monkeypatch, knowledge_first):
    writer = writer_at(tmp_path)
    identity, owner = uuid.uuid4(), uuid.uuid4()
    root = tmp_path / "storage" / "bundles" / str(owner) / "bundle"
    root.mkdir(parents=True)
    (root / "index.md").write_text("all knowledge")
    roots = {"bundles": tmp_path / "storage" / "bundles", "documents": tmp_path / "storage" / "documents",
             "datasets": tmp_path / "storage" / "datasets"}
    monkeypatch.setattr(packages.managed_storage, "roots", lambda: roots)
    monkeypatch.setattr(packages.okf_bundles, "owner_upload_root", lambda _owner: roots["bundles"] / str(owner))
    monkeypatch.setattr(packages.okf_documents, "owner_root", lambda _owner: roots["documents"] / str(owner))
    monkeypatch.setattr(packages.okf_bundles, "bundle_root", lambda: tmp_path / "new-browse-root")
    server = SimpleNamespace(id=identity, name="okf-bundle-legacy", owner_id=owner,
                             command=f"python -m asaree.mcp_servers.okf_bundle --bundle {root}",
                             capabilities={"tools": [{"name": "read_concept"}]},
                             transport=SimpleNamespace(value="stdio"))
    db = SimpleNamespace(scalar=AsyncMock(return_value=server))
    calls = [(True, {"bundle_id": str(identity)}), (False, {"server_name": server.name})]
    for knowledge, config in calls if knowledge_first else reversed(calls):
        await packages._server(db, writer, config, owner, knowledge=knowledge)
    writer.finish({})
    contents = unpack(tmp_path)
    assert contents[f"resources/knowledge/{identity}/index.md"] == b"all knowledge"
    assert sum(path.startswith("requirements/mcp/") for path in contents) == 1


async def test_actual_row_script_and_compatibility_outputs_and_agent_slots_are_retained(tmp_path, monkeypatch):
    from asaree.mcp_servers import script_server
    from asaree.services import protocol_execution

    run, experiment = uuid.uuid4(), uuid.uuid4()
    root = tmp_path / "workspaces"
    monkeypatch.setattr(protocol_execution, "_workspace_storage_root", lambda: root)
    monkeypatch.setattr(packages.managed_storage, "roots", lambda: {"workspaces": root})
    source = "from pathlib import Path\nPath('predictions.csv').write_text('exact prediction')\nprint('done')\n"
    script = protocol_execution._materialize_script(f"_protocol_runs/{run}", "script-node", source)
    extra = {"motoro.ambient.script_path": script}
    ctx = SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(model_extra=extra)))
    result = json.loads(script_server.run_wired_script(ctx=ctx))
    assert result["exit_code"] == 0
    legacy = source.replace("print('done')", "assert Path('state.json').exists()")
    script = protocol_execution._materialize_script(f"_protocol_runs/{run}", "legacy-script", legacy)
    extra.update({"motoro.ambient.script_path": script, "motoro.ambient.dataset_mode": "per_row",
                  "motoro.ambient.data_path": str(tmp_path / "row.csv")})
    result = json.loads(script_server.run_wired_script(ctx=ctx))
    assert result["exit_code"] == 0
    agent_slot = root / str(experiment) / str(run) / "agent=worker" / "v1_dc"
    agent_slot.mkdir(parents=True)
    (agent_slot / "manifest.json").write_text('{"learned": "exact"}')
    row_output = root / f"_protocol_runs/{run}/agents/hash/outputs"
    row_output.mkdir(parents=True)
    (row_output / "state.json").write_text("opaque user output with a familiar filename")
    writer = writer_at(tmp_path)
    graph = {"nodes": [{"id": "script-node", "type": "script"}, {"id": "legacy-script", "type": "script"}]}
    await packages._attempt_files(writer, SimpleNamespace(experiment_id=experiment), run, graph)
    writer.finish({})
    contents = unpack(tmp_path)
    assert any(path.endswith("scripts/predictions.csv") for path in contents)
    assert any("-unsplit-" in path and path.endswith("predictions.csv") for path in contents)
    assert any(path.endswith("agent=worker/v1_dc/manifest.json") for path in contents)
    assert any(path.endswith("agents/hash/outputs/state.json") for path in contents)
    assert all(not path.endswith(("script-node.py", "legacy-script.py")) for path in contents)


def selected_scope():
    return ResultScope(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), {"CURRENT": True}, {}, False, None)


async def test_api_requires_explicit_publication_and_allows_legacy_without_current_design(monkeypatch):
    scope = selected_scope()
    user = SimpleNamespace(id=uuid.uuid4())
    command = api.Command(kind="export", request_key="request", format="experiment_package")
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        graph={"old": True}, experiment_snapshot=None, design_revision_id=None,
    )), commit=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await api.create(command, user, db, scope)
    assert error.value.status_code == 422
    create = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(api.result_jobs, "create_job", create)
    monkeypatch.setattr(api, "_committed", AsyncMock(return_value={"ok": True}))
    assert await api.create(command, user, db, scope, protocol_revision_id=scope.protocol_revision_id) == {"ok": True}
    captured = create.call_args.args[1]
    assert captured.design_revision_id is None and captured.design_spec is None
    assert captured.graph == {"old": True}
    assert create.call_args.kwargs["params"]["include_results"] is False


@pytest.mark.parametrize("include_results", [False, True])
async def test_legacy_package_reports_missing_history_and_optional_results(tmp_path, monkeypatch, include_results):
    publication = SimpleNamespace(id=uuid.uuid4(), graph={"nodes": [], "edges": []}, experiment_snapshot=None,
                                  dataset_inputs=None, design_revision_id=None, revision=1,
                                  published_at=datetime.now(UTC), name=None, note=None)
    job = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), experiment_id=uuid.uuid4(), protocol_id=uuid.uuid4(),
                          protocol_revision_id=publication.id, design_revision_id=None,
                          params={"selection": {"include_results": include_results}})
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[SimpleNamespace(), publication]))
    status_db = SimpleNamespace(scalar=AsyncMock(return_value="running"), execute=AsyncMock())

    @asynccontextmanager
    async def session():
        yield status_db

    monkeypatch.setattr(packages, "get_session", session)
    monkeypatch.setattr(packages, "_resources", AsyncMock())
    monkeypatch.setattr(packages, "_import_sources", AsyncMock())
    results = AsyncMock()
    monkeypatch.setattr(packages, "_results", results)
    monkeypatch.setattr(result_exports, "get_settings", lambda: SimpleNamespace(result_export_dir=str(tmp_path)))
    await packages.execute_package(db, job, tmp_path / "partial", tmp_path / "package.tar.gz")
    manifest = json.loads(unpack(tmp_path)["manifest.json"])
    assert manifest["completeness"]["status"] == "incomplete"
    assert json.loads(unpack(tmp_path)["publication/settings.json"]) == {}
    assert results.await_count == int(include_results)
    assert not (tmp_path / "partial").exists()


async def test_package_download_expiry_and_owner_token(tmp_path, monkeypatch):
    job = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), experiment_id=uuid.uuid4(), kind="export",
                          status="completed", created_at=datetime.now(UTC) - timedelta(hours=25),
                          protocol_revision_id=uuid.uuid4(), params={"selection": {"format": "experiment_package"}})
    monkeypatch.setattr(result_exports, "get_settings", lambda: SimpleNamespace(
        result_export_dir=str(tmp_path), auth_secret_key="test-secret-key-at-least-32-characters",
    ))
    owned = AsyncMock(return_value=job)
    monkeypatch.setattr(api, "owned_job", owned)
    token = result_exports.download_token(job)
    with pytest.raises(HTTPException) as error:
        await api.download(job.experiment_id, job.id, token, SimpleNamespace())
    assert error.value.status_code == 410
    assert owned.call_args.args[-1] == job.owner_id


async def test_expiry_removes_package_csv_and_partial_files(tmp_path, monkeypatch):
    identifier = uuid.uuid4()
    monkeypatch.setattr(result_exports, "get_settings", lambda: SimpleNamespace(result_export_dir=str(tmp_path)))
    paths = [result_exports.export_path(identifier, package=package, metadata=metadata)
             for package, metadata in ((False, False), (True, False), (False, True))]
    paths += [paths[0].with_suffix(".part"), paths[1].with_suffix(".part")]
    for path in paths:
        path.write_bytes(b"expired")
    results = iter([[], [identifier]])
    db = SimpleNamespace(scalars=AsyncMock(side_effect=lambda _: SimpleNamespace(all=lambda: next(results))),
                         execute=AsyncMock())

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(result_exports, "get_session", session)
    monkeypatch.setattr(result_exports, "_get_pool", AsyncMock())
    await result_exports.relay_exports({})
    assert all(not path.exists() for path in paths)
