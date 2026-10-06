"""Version annotations can change without mutating published definitions."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from asaree.api.protocols import VersionAnnotationsRequest, update_protocol_revision_endpoint
from asaree.models.protocol_revision import ProtocolRevision


def test_annotations_trim_clear_and_reject_definition_edits():
    assert VersionAnnotationsRequest(name="  Baseline  ", note="  ").model_dump() == {
        "name": "Baseline", "note": None,
    }
    for payload in ({"graph": {}}, {"experiment_snapshot": {}}, {"name": "x" * 121}, {"note": "x" * 4001}):
        with pytest.raises(ValidationError):
            VersionAnnotationsRequest(**payload)


@pytest.mark.asyncio
async def test_edit_preserves_snapshot_and_unspecified_annotation(monkeypatch):
    protocol_id, revision_id = uuid.uuid4(), uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    snapshot = {"hypothesis": "Original"}
    revision = ProtocolRevision(
        id=revision_id, protocol_id=protocol_id, revision=25, graph=graph,
        experiment_snapshot=snapshot, name="Old", note="Keep this note", published_at=datetime.now(UTC),
    )
    user = SimpleNamespace(id=uuid.uuid4())
    db = SimpleNamespace(flush=AsyncMock())
    owned = AsyncMock()
    monkeypatch.setattr("asaree.api.protocols._get_owned_protocol", owned)
    monkeypatch.setattr("asaree.api.protocols.get_revision", AsyncMock(return_value=revision))
    monkeypatch.setattr("asaree.api.protocols._version_run_counts", AsyncMock(return_value={revision_id: (4, 2)}))
    response = await update_protocol_revision_endpoint(
        protocol_id, revision_id, VersionAnnotationsRequest(name=" Reference "), user, db,
    )
    owned.assert_awaited_once_with(db, protocol_id, user)
    assert response.name == "Reference"
    assert response.note == "Keep this note"
    assert (response.run_count, response.result_count) == (4, 2)
    assert revision.graph is graph
    assert revision.experiment_snapshot is snapshot
    assert revision.revision == 25


@pytest.mark.asyncio
async def test_edit_rejects_revision_from_another_protocol(monkeypatch):
    monkeypatch.setattr("asaree.api.protocols._get_owned_protocol", AsyncMock())
    monkeypatch.setattr(
        "asaree.api.protocols.get_revision", AsyncMock(return_value=SimpleNamespace(protocol_id=uuid.uuid4())),
    )
    with pytest.raises(HTTPException) as error:
        await update_protocol_revision_endpoint(
            uuid.uuid4(), uuid.uuid4(), VersionAnnotationsRequest(note="Changed"), SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(flush=AsyncMock()),
        )
    assert error.value.status_code == 404
