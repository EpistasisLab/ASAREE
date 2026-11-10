"""Package assembly against the existing disposable, isolated PostgreSQL fixture."""
import json
import tarfile
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_result_reads import results_db as _results_db

from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.services import experiment_packages as packages
from asaree.services.dataset_inputs import freeze_dataset_inputs
from asaree.services.datasets import create_dataset, quick_split_dataset

results_db = _results_db


async def test_historical_package_pins_all_sources_splits_and_every_attempt(results_db, tmp_path, monkeypatch):
    from asaree.config import AsareeSettings

    db, scope, cell, user = results_db
    settings = AsareeSettings(_env_file=None, data_root=str(tmp_path / "storage"))
    monkeypatch.setattr("asaree.config.get_settings", lambda: settings)
    monkeypatch.setattr("asaree.services.datasets.get_settings", lambda: settings)
    graph = {"nodes": [{"id": "agent", "type": "agent", "data": {}}], "edges": []}
    originals, pinned_splits = {}, []
    for number in range(2):
        raw = b"x,y\r\n1,0\r\n2,1\r\n3,0\r\n4,1\r\n5,0\r\n6,1\r\n7,0\r\n8,1\r\n"
        dataset = await create_dataset(db, name=f"dataset-{number}", csv_bytes=raw, owner_id=user.id, target_column="y")
        await quick_split_dataset(db, dataset=dataset, seed=1)
        pinned_splits.append(dataset.current_split_id)
        originals[str(dataset.id)] = raw
        graph["nodes"].append({"id": f"data-{number}", "type": "dataset", "data": {"config": {
            "dataset_id": str(dataset.id), "dataset_name": dataset.name,
        }}})
        graph["edges"].append({"source": f"data-{number}", "target": "agent", "targetHandle": "dataset"})
    publication = await db.get(ProtocolRevision, scope.protocol_revision_id)
    publication.graph = graph
    publication.dataset_inputs = await freeze_dataset_inputs(db, graph, owner_id=user.id)
    for pin in publication.dataset_inputs["inputs"]:
        from asaree.models.dataset import RegisteredDataset

        dataset = await db.get(RegisteredDataset, uuid.UUID(pin["dataset_id"]))
        await quick_split_dataset(db, dataset=dataset, seed=42)
        assert str(dataset.current_split_id) != pin["split_id"]
    runs = []
    for number in range(3):
        run = ProtocolRun(protocol_id=scope.protocol_id, owner_id=user.id,
                          protocol_revision_id=scope.protocol_revision_id if number < 2 else None,
                          design_revision_id=scope.design_revision_id, status="completed",
                          attempt_result={"measurement": {"observations": [{"value": "é" * 100000}]}},
                          node_runs={}, factor_values={"attempt": number})
        db.add(run)
        await db.flush()
        runs.append(run.id)
    staging = tmp_path / "stage"
    staging.mkdir()
    writer = packages.PackageWriter(tmp_path / "package.tar.gz", staging)
    await packages._datasets(db, writer, publication, user.id)
    await packages._design(db, writer, publication)
    await packages._results(db, writer, SimpleNamespace(owner_id=user.id, experiment_id=scope.experiment_id,
                           protocol_id=scope.protocol_id, protocol_revision_id=scope.protocol_revision_id,
                           design_revision_id=scope.design_revision_id), AsyncMock())
    writer.finish({})
    with tarfile.open(tmp_path / "package.tar.gz") as archive:
        names = archive.getnames()
        assert f"results/attempts/{runs[0]}/attempt_result.json" in names
        assert f"results/attempts/{runs[1]}/attempt_result.json" in names
        assert all(str(runs[2]) not in name for name in names)
        for pin in publication.dataset_inputs["inputs"]:
            assert archive.extractfile(f"inputs/files/{pin['raw_artifact']['id']}.csv").read() == originals[
                pin["dataset_id"]]
            assert f"inputs/splits/{pin['split_id']}.json" in names
        assert all(f"inputs/splits/{split}.json" in names for split in pinned_splits)
        document = json.load(archive.extractfile(f"results/attempts/{runs[0]}/attempt_result.json"))
        assert document["measurement"]["observations"][0]["value"] == "é" * 100000
        assert all("row-index" not in name and "scratch" not in name for name in names)
