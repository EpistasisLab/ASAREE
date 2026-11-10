"""PostgreSQL publication/queue retention across current split changes."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from asaree.models.database import dispose_engine, get_session
from asaree.models.dataset_input_reference import DatasetInputReference
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.user import User
from asaree.services.dataset_inputs import prepare_attempt_graph
from asaree.services.dataset_row_csv import project_row, read_row_source
from asaree.services.datasets import (
    DatasetValidationError,
    create_dataset,
    delete_dataset,
    quick_split_dataset,
)
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocol_runs import create_protocol_run
from asaree.services.protocols import create_protocol, delete_protocol

_CSV = b"x,y\n1,0\n2,1\n3,0\n4,1\n5,0\n6,1\n7,0\n8,1\n"


@pytest_asyncio.fixture(autouse=True)
async def fresh_engine():
    yield
    await dispose_engine()


@pytest.mark.parametrize("mode", ["split", "unsplit", "per_row", "factor"])
async def test_publication_and_queued_attempt_keep_a_after_b(tmp_path, monkeypatch, mode):
    from asaree.config import get_settings

    settings = get_settings().model_copy(update={"dataset_storage_dir": str(tmp_path)})
    monkeypatch.setattr("asaree.config.get_settings", lambda: settings)
    monkeypatch.setattr("asaree.services.datasets.get_settings", lambda: settings)
    async with get_session() as db:
        user = User(email=f"pinned-{uuid.uuid4()}@example.com", display_name="Pinned Inputs", hashed_password="unused")
        db.add(user)
        await db.flush()
        dataset = await create_dataset(db, name="cohort", csv_bytes=_CSV, owner_id=user.id, target_column="y")
        if mode != "unsplit":
            await quick_split_dataset(db, dataset=dataset, seed=1)
        split_a = dataset.current_split_id
        graph = {
            "nodes": [
                {"id": "data", "type": "dataset", "data": {"config": {
                    "dataset_id": str(dataset.id), "dataset_name": dataset.name,
                }}},
                {"id": "agent", "type": "agent", "data": {}},
            ],
            "edges": [{"source": "data", "target": "agent", "targetHandle": "dataset"}],
        }
        if mode == "per_row":
            graph["edges"][0]["data"] = {"dataset_input": {"mode": "per_row", "columns": ["x"]}}
        alternatives = []
        if mode == "factor":
            alternative = await create_dataset(
                db, name="alternative", csv_bytes=_CSV, owner_id=user.id, target_column="y"
            )
            await quick_split_dataset(db, dataset=alternative, seed=2)
            alternatives.append(alternative)
            graph["nodes"].insert(1, {"id": "other", "type": "dataset", "data": {"config": {
                "dataset_id": str(alternative.id), "dataset_name": alternative.name,
            }}})
            graph["edges"].append({"source": "other", "target": "agent", "targetHandle": "dataset"})
            graph["nodes"][-1]["data"] = {"factor_bindings": {"dataset_selection": "Input"},
                                            "dataset_selection": [str(dataset.id)]}
        protocol = await create_protocol(db, name="Pinned experiment", owner_id=user.id, graph=graph)
        publication = await publish_protocol(db, protocol, owner_id=user.id)
        snapshot = None
        if mode == "per_row":
            source = read_row_source(dataset_id=str(dataset.id), raw_path=dataset.raw_path,
                                     raw_sha256=dataset.raw_sha256)
            snapshot = project_row(source, row_index=2, columns=["x"])
        factors = {"Input": [str(dataset.id)]} if mode == "factor" else None
        run = await create_protocol_run(
            db, protocol_id=protocol.id, owner_id=user.id,
            protocol_revision_id=publication.id, dataset_row=snapshot, factor_values=factors,
        )
        await quick_split_dataset(db, dataset=dataset, seed=42)
        for alternative in alternatives:
            await quick_split_dataset(db, dataset=alternative, seed=42)
        alternative_ids = [alternative.id for alternative in alternatives]
        # Simulate worker delivery and a later resume through fresh ORM reads.
        run_id, publication_id = run.id, publication.id
        db.expire_all()
        run = await db.get(ProtocolRun, run_id)
        publication = await db.get(ProtocolRevision, publication_id)
        for _ in range(2):
            prepared = await prepare_attempt_graph(db, run, publication.graph)
            resolved = prepared["_pinned_registrations"]["cohort"]
            pin = run.dataset_inputs["inputs"][0]
            if mode in {"unsplit", "per_row"}:
                assert pin["split_id"] is None
                assert resolved["train_path"] is None
            else:
                assert pin["split_id"] == str(split_a)
                assert str(split_a) in resolved["train_path"]
            assert run.dataset_row == snapshot
        assert len(publication.dataset_inputs["inputs"]) == (2 if mode == "factor" else 1)
        assert await db.scalar(select(func.count()).select_from(DatasetInputReference)) >= 2
        protocol = await db.get(type(protocol), run.protocol_id)
        assert (await publish_protocol(db, protocol, owner_id=run.owner_id)).id != publication_id or mode == "per_row"
        with pytest.raises(DatasetValidationError, match="referenced"):
            await delete_dataset(db, pin_id := uuid.UUID(run.dataset_inputs["inputs"][0]["dataset_id"]))
        # FK protection also covers callers that bypass the service.
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                from asaree.models.dataset import RegisteredDataset

                await db.execute(RegisteredDataset.__table__.delete().where(RegisteredDataset.id == pin_id))
        owner_id = run.owner_id
        await delete_protocol(db, protocol.id)
        await delete_dataset(db, pin_id)
        for alternative_id in alternative_ids:
            await delete_dataset(db, alternative_id)
        await db.delete(await db.get(User, owner_id))
