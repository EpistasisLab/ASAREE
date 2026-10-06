from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import asaree.models.dataset  # noqa: F401
import asaree.models.experiment  # noqa: F401
from asaree.models.database import dispose_engine, get_session
from asaree.models.dataset import RegisteredDataset
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.user import User
from asaree.services.dataset_row_csv import DatasetRowCsvError
from asaree.services.dataset_row_inputs import DatasetRowInputError
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocols import create_protocol, delete_protocol, update_protocol
from asaree.services.users import create_user, get_user_by_email, set_password


async def _row_test_user(
    db: AsyncSession, *, email: str, display_name: str
) -> tuple[User, bool]:
    user = await get_user_by_email(db, email)
    if user is None:
        return await create_user(db, email=email, password="Test1234", display_name=display_name), True
    await set_password(db, user, new_password="Test1234")
    user.display_name = display_name
    user.is_active = True
    return user, False


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest.mark.asyncio
async def test_row_publication_revisions_and_source_validation(tmp_path: Path) -> None:
    dataset_id = uuid.uuid4()
    csv_path = tmp_path / "rows.csv"
    contents = b"question,answer\nfirst,one\nsecond,two\n"
    csv_path.write_bytes(contents)
    digest = hashlib.sha256(contents).hexdigest()
    async with get_session() as db:
        owner, owner_created = await _row_test_user(
            db, email="test@test.com", display_name="Row Publisher"
        )
        other, other_created = await _row_test_user(
            db, email="other@test.com", display_name="Other Publisher"
        )
        owner_id = owner.id
        other_owner_id = other.id
        dataset = RegisteredDataset(
            id=dataset_id,
            name="cohort",
            raw_path=str(csv_path),
            raw_sha256=digest,
            owner_id=owner_id,
        )
        other_dataset = RegisteredDataset(
            id=uuid.uuid4(),
            name="other-cohort",
            raw_path=str(csv_path),
            raw_sha256=digest,
            owner_id=owner_id,
        )
        db.add_all([dataset, other_dataset])
        graph = {
            "nodes": [
                {
                    "id": "d",
                    "type": "dataset",
                    "data": {"config": {"dataset_id": str(dataset_id), "dataset_name": "cohort"}},
                },
                {"id": "a", "type": "agent", "data": {}},
                {"id": "b", "type": "agent", "data": {}},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "d",
                    "target": "a",
                    "targetHandle": "dataset",
                    "data": {"dataset_input": {"mode": "per_row", "columns": ["question"]}},
                },
                {
                    "id": "e2",
                    "source": "d",
                    "target": "b",
                    "targetHandle": "dataset",
                    "data": {"dataset_input": {"mode": "per_row", "columns": ["answer"]}},
                },
            ],
        }
        protocol = await create_protocol(db, name="row publication", owner_id=owner_id, graph=graph)
        first = await publish_protocol(db, protocol, owner_id=owner_id)
        original_snapshot = deepcopy(first.graph)

        changed = deepcopy(graph)
        changed["edges"][0]["data"]["dataset_input"]["columns"] = ["answer", "question"]
        await update_protocol(db, protocol.id, fields={"graph": changed})
        second = await publish_protocol(db, protocol, owner_id=owner_id)
        assert second.revision == first.revision + 1
        assert first.graph == original_snapshot

        whole = deepcopy(changed)
        whole["edges"] = [{"id": "e1", "source": "d", "target": "a", "targetHandle": "dataset"}]
        await update_protocol(db, protocol.id, fields={"graph": whole})
        third = await publish_protocol(db, protocol, owner_id=owner_id)
        explicit_whole = deepcopy(whole)
        explicit_whole["edges"][0]["data"] = {"dataset_input": {"mode": "whole_dataset"}}
        await update_protocol(db, protocol.id, fields={"graph": explicit_whole})
        assert (await publish_protocol(db, protocol, owner_id=owner_id)).id == third.id

        cosmetic = deepcopy(explicit_whole)
        cosmetic["nodes"][0]["position"] = {"x": 200, "y": 300}
        cosmetic["edges"][0]["id"] = "new-edge-id"
        await update_protocol(db, protocol.id, fields={"graph": cosmetic})
        assert (await publish_protocol(db, protocol, owner_id=owner_id)).id == third.id

        invalid = deepcopy(graph)
        invalid["edges"][0]["data"]["dataset_input"]["columns"] = ["missing"]
        await update_protocol(db, protocol.id, fields={"graph": invalid})
        with pytest.raises(DatasetRowCsvError, match="invalid_columns"):
            await publish_protocol(db, protocol, owner_id=owner_id)
        unknown = deepcopy(graph)
        unknown["nodes"][0]["data"]["config"]["dataset_id"] = str(uuid.uuid4())
        await update_protocol(db, protocol.id, fields={"graph": unknown})
        with pytest.raises(DatasetRowInputError, match="source_unavailable"):
            await publish_protocol(db, protocol, owner_id=owner_id)
        unowned = deepcopy(graph)
        unowned["nodes"][0]["data"]["config"]["dataset_id"] = str(dataset_id)
        await update_protocol(db, protocol.id, fields={"graph": unowned})
        with pytest.raises(DatasetRowInputError, match="source_unavailable"):
            await publish_protocol(db, protocol, owner_id=other_owner_id)

        mismatched_alias = deepcopy(graph)
        mismatched_alias["nodes"].append(
            {
                "id": "whole-alias",
                "type": "dataset",
                "data": {"config": {"dataset_id": str(uuid.uuid4()), "dataset_name": "other-cohort"}},
            }
        )
        mismatched_alias["edges"].append(
            {"id": "whole-edge", "source": "whole-alias", "target": "a", "targetHandle": "dataset"}
        )
        await update_protocol(db, protocol.id, fields={"graph": mismatched_alias})
        with pytest.raises(DatasetRowInputError, match="source_unavailable"):
            await publish_protocol(db, protocol, owner_id=owner_id)

        revision_count = (
            await db.execute(
                select(func.count()).select_from(ProtocolRevision).where(ProtocolRevision.protocol_id == protocol.id)
            )
        ).scalar_one()
        assert revision_count == 3
        await delete_protocol(db, protocol.id)
        await db.delete(dataset)
        await db.delete(other_dataset)
        if owner_created:
            await db.delete(owner)
        if other_created:
            await db.delete(other)
