"""Authenticated Dataset row metadata API tests using real CSV files."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from asaree.api import datasets
from asaree.deps import get_current_user
from asaree.models.database import get_db


@pytest.fixture
def row_schema_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, uuid.UUID, dict[str, object]]:
    user_id = uuid.uuid4()
    dataset_id = uuid.uuid4()
    state: dict[str, object] = {}

    async def _auth() -> SimpleNamespace:
        return SimpleNamespace(id=user_id)

    async def _db() -> None:
        return None

    async def _get_dataset(_db_session: object, requested_id: uuid.UUID) -> object:
        assert requested_id == dataset_id
        return state["dataset"]

    app = FastAPI()
    app.include_router(datasets.router, prefix="/api")
    app.dependency_overrides[get_current_user] = _auth
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(datasets, "get_dataset", _get_dataset)
    state["dataset_id"] = dataset_id
    state["user_id"] = user_id
    return app, dataset_id, state


def _dataset(dataset_id: uuid.UUID, owner_id: uuid.UUID, path: Path, content: bytes) -> SimpleNamespace:
    return SimpleNamespace(
        id=dataset_id,
        owner_id=owner_id,
        name="private-dataset-name",
        raw_path=str(path),
        raw_sha256=hashlib.sha256(content).hexdigest(),
    )


async def _get_schema(app: FastAPI, dataset_id: uuid.UUID) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/api/datasets/{dataset_id}/row-schema")


async def test_row_schema_returns_ordered_metadata_without_source_disclosure(
    tmp_path: Path, row_schema_app: tuple[FastAPI, uuid.UUID, dict[str, object]]
) -> None:
    app, dataset_id, state = row_schema_app
    content = b"subject,value,label\n001,alpha,yes\n002,beta,no\n003,gamma,yes\n"
    raw_path = tmp_path / "private-source-file.csv"
    raw_path.write_bytes(content)
    state["dataset"] = _dataset(dataset_id, state["user_id"], raw_path, content)

    response = await _get_schema(app, dataset_id)

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": str(dataset_id),
        "raw_sha256": hashlib.sha256(content).hexdigest(),
        "columns": ["subject", "value", "label"],
        "row_count": 3,
    }
    assert "alpha" not in response.text
    assert "private-dataset-name" not in response.text
    assert str(raw_path) not in response.text


async def test_row_schema_keeps_unowned_dataset_concealed(
    tmp_path: Path, row_schema_app: tuple[FastAPI, uuid.UUID, dict[str, object]]
) -> None:
    app, dataset_id, state = row_schema_app
    content = b"id,value\n1,secret\n"
    raw_path = tmp_path / "readable-unowned.csv"
    raw_path.write_bytes(content)
    state["dataset"] = _dataset(dataset_id, uuid.uuid4(), raw_path, content)

    response = await _get_schema(app, dataset_id)

    assert response.status_code == 404
    assert "secret" not in response.text
    assert str(raw_path) not in response.text


async def test_row_schema_rejects_corrupted_registered_hash(
    tmp_path: Path, row_schema_app: tuple[FastAPI, uuid.UUID, dict[str, object]]
) -> None:
    app, dataset_id, state = row_schema_app
    content = b"id,value\n1,secret\n"
    raw_path = tmp_path / "corrupted.csv"
    raw_path.write_bytes(content + b"2,changed\n")
    state["dataset"] = _dataset(dataset_id, state["user_id"], raw_path, content)

    response = await _get_schema(app, dataset_id)

    assert response.status_code == 422
    assert response.json() == {
        "detail": "dataset_row.source_hash_mismatch: original CSV does not match its registered SHA-256"
    }
    assert str(raw_path) not in response.text
    assert "secret" not in response.text


async def test_row_schema_accepts_header_only_csv(
    tmp_path: Path, row_schema_app: tuple[FastAPI, uuid.UUID, dict[str, object]]
) -> None:
    app, dataset_id, state = row_schema_app
    content = b"first,second\n"
    raw_path = tmp_path / "header-only.csv"
    raw_path.write_bytes(content)
    state["dataset"] = _dataset(dataset_id, state["user_id"], raw_path, content)

    response = await _get_schema(app, dataset_id)

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": str(dataset_id),
        "raw_sha256": hashlib.sha256(content).hexdigest(),
        "columns": ["first", "second"],
        "row_count": 0,
    }
