"""Immutable selection, resolution, Agent binding and workspace contracts."""

import hashlib
import io
import json
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from asaree.models.dataset import RegisteredDataset
from asaree.models.dataset_split import DatasetArtifact, DatasetSplit
from asaree.services import protocol_execution as pe
from asaree.services.dataset_inputs import (
    DatasetInputError,
    attempt_workspace_id,
    freeze_dataset_inputs,
    prepare_attempt_graph,
    resolve_attempt_inputs,
    resolve_pinned_registration,
    validate_workspace_inputs,
)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr("asaree.config.get_settings", lambda: SimpleNamespace(dataset_storage_dir=str(tmp_path)))
    monkeypatch.setattr("asaree_workspace_core.workspace.WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    owner = uuid.uuid4()
    objects = {}
    registrations = {}

    def artifact(dataset_id, filename, contents):
        path = tmp_path / filename
        if filename.endswith("parquet"):
            pd.DataFrame({"x": [contents], "y": [1]}).to_parquet(path)
        else:
            path.write_bytes(contents)
        result = DatasetArtifact(id=uuid.uuid4(), dataset_id=dataset_id, namespace="datasets", storage_key=filename,
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest(), status="ready", format="csv")
        objects[result.id] = result
        return result

    def split(dataset, suffix):
        train = artifact(dataset.id, f"{dataset.name}-{suffix}-train.parquet", suffix)
        test = artifact(dataset.id, f"{dataset.name}-{suffix}-test.parquet", suffix)
        result = DatasetSplit(id=uuid.uuid4(), dataset_id=dataset.id, source_sha256=dataset.raw_sha256,
                              train_artifact=train, test_artifact=test, train_artifact_id=train.id,
                              test_artifact_id=test.id, target_column="y")
        objects[result.id] = result
        dataset.current_split = result
        dataset.current_split_id = result.id
        return result

    for name in ("a", "b"):
        dataset_id = uuid.uuid4()
        raw = artifact(dataset_id, f"{name}.csv", b"x,y\n001,1\n002,0\n")
        dataset = RegisteredDataset(id=dataset_id, owner_id=owner, name=name, raw_artifact=raw,
                                    raw_sha256=raw.sha256, target_column="y")
        registrations[name] = dataset
        objects[dataset.id] = dataset

    async def scalar(statement):
        params = statement.compile().params
        row = objects.get(next(value for key, value in params.items() if key.startswith("id_")))
        return row if row is not None and row.owner_id == params.get("owner_id_1") else None

    db = SimpleNamespace(scalar=AsyncMock(side_effect=scalar),
                         get=AsyncMock(side_effect=lambda model, id: objects.get(id)))
    return SimpleNamespace(db=db, datasets=registrations, owner=owner, split=split, objects=objects, root=tmp_path)


def graph_for(*datasets):
    return {"nodes": [
        *[{"id": dataset.name, "type": "dataset", "data": {"config": {
            "dataset_id": str(dataset.id), "dataset_name": dataset.name,
        }}} for dataset in datasets],
        {"id": "agent", "type": "agent", "data": {}},
    ], "edges": [{"source": dataset.name, "target": "agent", "targetHandle": "dataset"} for dataset in datasets]}


def run_for(catalog, manifest, **fields):
    return SimpleNamespace(id=uuid.uuid4(), owner_id=catalog.owner, dataset_inputs=manifest, factor_values=None,
                           dataset_row=fields.get("dataset_row"), target_node_id=None,
                           protocol_revision_id=uuid.uuid4(),
                           design_revision_id=None, replicate_result_id=None, row_result_id=None)


async def test_split_a_survives_b_selection_and_workspace_resume(catalog, monkeypatch):
    dataset = catalog.datasets["a"]
    split_a = catalog.split(dataset, "A")
    graph = graph_for(dataset)
    publication = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    manifest = resolve_attempt_inputs(publication, graph)
    run = run_for(catalog, manifest)
    catalog.split(dataset, "B")
    monkeypatch.setattr(pe, "fetch_owned_registration", AsyncMock(side_effect=AssertionError("mutable lookup")))
    workspace = attempt_workspace_id(None, run.id)
    for _ in range(2):
        prepared = await prepare_attempt_graph(catalog.db, run, graph)
        validate_workspace_inputs(workspace, manifest, prepared["_dataset_input_scope"])
        seeded = await pe._resolve_node_dataset(prepared, "agent", workspace, catalog.owner)
        assert seeded.seeded_names == ("a",)
        from asaree.services.dataset_workspaces import head_data_locator

        assert pd.read_parquet(head_data_locator(workspace)[0])["x"].tolist() == ["A"]
    assert manifest["inputs"][0]["split_id"] == str(split_a.id)
    assert (await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)) != publication


async def test_unsplit_remains_explicit_after_split_creation(catalog):
    dataset = catalog.datasets["a"]
    graph = graph_for(dataset)
    publication = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    catalog.split(dataset, "later")
    run = run_for(catalog, resolve_attempt_inputs(publication, graph))
    prepared = await prepare_attempt_graph(catalog.db, run, graph)
    resolved = await pe._resolve_node_dataset(prepared, "agent", None, catalog.owner)
    assert resolved.unsplit_name == "a"
    assert resolved.data_path == dataset.raw_path
    assert run.dataset_inputs["inputs"][0]["split_id"] is None


async def test_new_publication_executes_legacy_split_with_effective_target(catalog):
    dataset = catalog.datasets["a"]
    split = catalog.split(dataset, "legacy")
    split.legacy = True
    split.target_column = None
    assert dataset.target_column == "y"
    graph = graph_for(dataset)
    publication = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    run = run_for(catalog, resolve_attempt_inputs(publication, graph))
    dataset.target_column = "later"
    workspace = attempt_workspace_id(None, run.id)
    for _ in range(2):
        prepared = await prepare_attempt_graph(catalog.db, run, graph)
        opened = await pe._resolve_node_dataset(prepared, "agent", workspace, catalog.owner)
        assert opened.seeded_names == ("a",)
        from asaree.services.dataset_workspaces import head_data_locator

        assert head_data_locator(workspace)[1] == "y"
    assert publication["inputs"][0]["target_column"] == "y"
    assert split.target_column is None and split.legacy


async def test_connector_factor_alternatives_pin_all_and_preserve_wiring_order(catalog):
    a, b = catalog.datasets.values()
    a_split, b_split = catalog.split(a, "A"), catalog.split(b, "A")
    graph = graph_for(b, a)
    graph["nodes"][-1]["data"] = {"factor_bindings": {"dataset_selection": "Input"},
                                    "dataset_selection": [str(a.id)]}
    publication = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    catalog.split(a, "B")
    catalog.split(b, "B")
    for ids in ([str(a.id)], [str(b.id)], [str(a.id), str(b.id)], []):
        patched = pe.apply_factor_bindings(graph, {"Input": ids})
        manifest = resolve_attempt_inputs(publication, patched)
        expected = [dataset for dataset in (b, a) if str(dataset.id) in ids]
        assert [binding["dataset_id"] for binding in manifest["bindings"]] == [str(ds.id) for ds in expected]
        assert [pin["split_id"] for pin in manifest["inputs"]] == [
            str(b_split.id if ds is b else a_split.id) for ds in expected
        ]
        for pin in manifest["inputs"]:
            resolved = await resolve_pinned_registration(catalog.db, pin, catalog.owner)
            assert pd.read_parquet(resolved["train_path"])["x"].tolist() == ["A"]


async def test_whole_config_factor_pins_alternatives(catalog):
    a, b = catalog.datasets.values()
    graph = graph_for(a)
    graph["nodes"][0]["data"]["factor_bindings"] = {"config": "Dataset"}
    levels = [{"dataset_id": str(ds.id), "dataset_name": ds.name} for ds in (a, b)]
    publication = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner,
                                              design_spec={"factors": [{"name": "Dataset", "levels": levels}]})
    for level in levels:
        manifest = resolve_attempt_inputs(publication, pe.apply_factor_bindings(graph, {"Dataset": level}))
        assert manifest["inputs"][0]["dataset_id"] == level["dataset_id"]


async def test_per_row_pins_source_and_original_projection_without_split(catalog):
    dataset = catalog.datasets["a"]
    catalog.split(dataset, "A")
    graph = graph_for(dataset)
    graph["edges"][0]["data"] = {"dataset_input": {"mode": "per_row", "columns": ["x"]}}
    publication = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    row = {"dataset_id": str(dataset.id), "raw_sha256": dataset.raw_sha256,
           "row_index": 1, "columns": ["x"], "values": {"x": "002"}}
    manifest = resolve_attempt_inputs(publication, graph, dataset_row=row)
    catalog.split(dataset, "B")
    run = run_for(catalog, manifest, dataset_row=row)
    await prepare_attempt_graph(catalog.db, run, graph)
    binding = manifest["bindings"][0]
    assert binding["row_index"] == 1 and binding["columns"] == ["x"] and binding["values"] == {"x": "002"}
    assert manifest["inputs"][0]["split_id"] is None
    assert row["values"] == {"x": "002"}
    with pytest.raises(DatasetInputError, match="identity_mismatch"):
        resolve_attempt_inputs(publication, graph, dataset_row={**row, "raw_sha256": "0" * 64})
    with pytest.raises(DatasetInputError, match="invalid_columns"):
        resolve_attempt_inputs(publication, graph, dataset_row={**row, "values": {}})


@pytest.mark.parametrize("damage", ["missing", "hash", "identity", "owner"])
async def test_pinned_input_failures_are_explicit(catalog, damage):
    dataset = catalog.datasets["a"]
    split = catalog.split(dataset, "A")
    graph = graph_for(dataset)
    publication = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    pin = publication["inputs"][0]
    catalog.split(dataset, "B")
    if damage == "missing":
        (catalog.root / split.train_artifact.storage_key).unlink()
    elif damage == "hash":
        (catalog.root / split.train_artifact.storage_key).write_bytes(b"changed")
    elif damage == "identity":
        split.source_sha256 = "0" * 64
    else:
        dataset.owner_id = uuid.uuid4()
    with pytest.raises(DatasetInputError, match="source_unavailable|hash_mismatch|identity_mismatch"):
        await resolve_pinned_registration(catalog.db, pin, catalog.owner)


async def test_legacy_publication_never_infers_current_split(catalog):
    dataset = catalog.datasets["a"]
    catalog.split(dataset, "today")
    graph = graph_for(dataset)
    assert resolve_attempt_inputs(None, graph) is None
    future = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    future["schema_version"] = 2
    with pytest.raises(DatasetInputError, match="unsupported_provenance"):
        resolve_attempt_inputs(future, graph)
    with pytest.raises(DatasetInputError, match="legacy_unknown"):
        await prepare_attempt_graph(catalog.db, run_for(catalog, None), graph)
    recorded = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    missing_publication = run_for(catalog, resolve_attempt_inputs(recorded, graph))
    missing_publication.protocol_revision_id = None
    with pytest.raises(DatasetInputError, match="scope_unavailable"):
        await prepare_attempt_graph(catalog.db, missing_publication, graph)
    graph["edges"][0]["data"] = {"dataset_input": {"mode": "per_row", "columns": ["x"]}}
    snapshot = {"dataset_id": str(dataset.id), "raw_sha256": dataset.raw_sha256,
                "row_index": 0, "columns": ["x"], "values": {"x": "001"}}
    run = run_for(catalog, None, dataset_row=snapshot)
    prepared = await prepare_attempt_graph(catalog.db, run, graph)
    assert "_pinned_registrations" not in prepared
    assert run.dataset_inputs is None
    assert run.dataset_row == snapshot


async def test_workspace_rejects_same_bytes_with_different_scope_or_identity(catalog):
    graph = graph_for(catalog.datasets["a"])
    manifest = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    validate_workspace_inputs("experiment/scoped", manifest, {"run_id": "first"})
    with pytest.raises(DatasetInputError, match="workspace_conflict"):
        validate_workspace_inputs("experiment/scoped", manifest, {"run_id": "second"})
    changed = deepcopy(manifest)
    changed["inputs"][0]["dataset_id"] = str(uuid.uuid4())
    with pytest.raises(DatasetInputError, match="workspace_conflict"):
        validate_workspace_inputs("experiment/scoped", changed, {"run_id": "first"})
    from asaree_workspace_core import Workspace

    unknown = Workspace("experiment/unknown")
    unknown.dir.mkdir(parents=True)
    unknown.state_path.write_text("{}")
    with pytest.raises(DatasetInputError, match="unknown inputs"):
        validate_workspace_inputs("experiment/unknown", manifest, {})


async def test_publication_rejects_foreign_alternative(catalog):
    a, b = catalog.datasets.values()
    b.owner_id = uuid.uuid4()
    graph = graph_for(a, b)
    graph["nodes"][-1]["data"] = {"factor_bindings": {"dataset_selection": "Input"},
                                    "dataset_selection": [str(a.id)]}
    with pytest.raises(ValueError, match="source_unavailable"):
        await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)


async def test_attempt_records_manifest_and_retention_edges(catalog):
    from asaree.models.protocol import Protocol
    from asaree.models.protocol_revision import ProtocolRevision
    from asaree.services.protocol_runs import create_protocol_run

    graph = graph_for(catalog.datasets["a"])
    pins = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    publication = ProtocolRevision(id=uuid.uuid4(), protocol_id=uuid.uuid4(), graph=graph, dataset_inputs=pins)
    protocol = Protocol(id=publication.protocol_id, owner_id=catalog.owner)
    objects = {protocol.id: protocol, publication.id: publication}
    db = SimpleNamespace(get=AsyncMock(side_effect=lambda model, id: objects.get(id)),
                         add=MagicMock(), flush=AsyncMock(), refresh=AsyncMock())
    run = await create_protocol_run(db, protocol_id=protocol.id, owner_id=catalog.owner,
                                    protocol_revision_id=publication.id)
    assert run.dataset_inputs["inputs"] == publication.dataset_inputs["inputs"]
    assert run.dataset_inputs["bindings"][0]["agent_node_id"] == "agent"
    assert db.add.call_count == 2


async def test_mcp_reopen_uses_pin_and_rejects_workspace_override(catalog, monkeypatch):
    from asaree.mcp_servers import workspace_server as ws

    dataset = catalog.datasets["a"]
    split_a = catalog.split(dataset, "A")
    graph = graph_for(dataset)
    manifest = await freeze_dataset_inputs(catalog.db, graph, owner_id=catalog.owner)
    catalog.split(dataset, "B")

    @asynccontextmanager
    async def session():
        yield catalog.db

    monkeypatch.setattr("asaree.models.database.get_session", session)
    monkeypatch.setattr("asaree.services.dataset_workspaces.fetch_owned_registration",
                        AsyncMock(side_effect=AssertionError("mutable lookup")))
    meta = {"motoro.workspace_id": "experiment/attempt", "motoro.owner_id": str(catalog.owner),
            "motoro.ambient.dataset_names": ["a"], "motoro.ambient.dataset_inputs": manifest["inputs"]}
    ctx = SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(model_extra=meta)))
    response = json.loads(await ws.open_workspace(ctx=ctx))
    assert "error" not in response
    from asaree.services.dataset_workspaces import head_data_locator

    assert str(split_a.id) == manifest["inputs"][0]["split_id"]
    assert pd.read_parquet(head_data_locator("experiment/attempt")[0])["x"].tolist() == ["A"]
    response = json.loads(await ws.open_workspace(experiment_id="experiment", cell_label="other", ctx=ctx))
    assert "outside their attempt" in response["error"]


def test_migration_keeps_historical_provenance_null():
    from alembic import command

    from asaree.migrations import make_config

    output = io.StringIO()
    config = make_config("postgresql+asyncpg://localhost/unused")
    config.output_buffer = output
    command.upgrade(config, "a67b8c9d0e1f:b78c9d0e1f2a", sql=True)
    sql = output.getvalue()
    assert "ADD COLUMN IF NOT EXISTS dataset_inputs JSONB" in sql
    assert "UPDATE protocol_revisions" not in sql
    assert "UPDATE protocol_runs" not in sql
    assert "REFERENCES dataset_artifacts" in sql
