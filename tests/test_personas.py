"""Persona ownership, edits, and published snapshots against real Postgres."""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from asaree.api.personas import router
from asaree.deps import get_current_user
from asaree.models.database import dispose_engine, get_session
from asaree.models.persona import Persona
from asaree.models.user import User
from asaree.services.personas import (
    PersonaRevisionConflictError,
    create_persona,
    delete_persona,
    get_persona,
    list_personas,
    update_persona,
)
from asaree.services.protocol_revisions import get_revision, publish_protocol
from asaree.services.protocols import create_protocol, delete_protocol, get_protocol


@pytest_asyncio.fixture(autouse=True)
async def fresh_engine() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"persona-{uuid.uuid4().hex}@example.com", hashed_password="unused", display_name="Persona test"
        )
        db.add(user)
        await db.flush()
        uid = user.id
    yield uid
    async with get_session() as db:
        await db.execute(delete(Persona).where(Persona.owner_id == uid))
        user = await db.get(User, uid)
        if user is not None:
            await db.delete(user)


async def test_owner_scoped_crud_and_revision_conflicts(owner_id: uuid.UUID) -> None:
    other = uuid.uuid4()
    async with get_session() as db:
        persona = await create_persona(db, owner_id=owner_id, prompt="You are careful.", source_filename="analyst.md")
        pid = persona.id
        assert persona.name == "analyst"
        assert persona.revision == 1
        assert await list_personas(db, owner_id=other) == []
        assert await get_persona(db, pid, owner_id=other) is None
        assert await update_persona(db, pid, owner_id=other, expected_revision=1, fields={"prompt": "wrong"}) is None
        assert not await delete_persona(db, pid, owner_id=other)
        updated = await update_persona(db, pid, owner_id=owner_id, expected_revision=1, fields={
            "prompt": "You are curious.", "description": "Explorer", "traits": {"openness": "high"},
        })
        assert updated is not None
        assert updated.prompt == "You are curious."
        assert updated.revision == 2
        assert updated.traits == {"openness": "high"}
    async with get_session() as db:
        with pytest.raises(PersonaRevisionConflictError):
            await update_persona(db, pid, owner_id=owner_id, expected_revision=1, fields={"prompt": "stale"})
    async with get_session() as db:
        assert await delete_persona(db, pid, owner_id=owner_id)
        assert not await delete_persona(db, pid, owner_id=owner_id)


async def test_edit_and_delete_preserve_draft_and_published_persona_text(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        persona = await create_persona(db, owner_id=owner_id, prompt="Original instructions")
        pid = persona.id
        graph = {"nodes": [{"id": "p", "type": "persona", "data": {"config": {
            "mode": "library", "persona_id": str(pid), "persona_text": persona.prompt,
        }}}], "edges": []}
        original = copy.deepcopy(graph)
        protocol = await create_protocol(db, owner_id=owner_id, name="Persona snapshot", graph=graph)
        protocol_id = protocol.id
        revision = await publish_protocol(db, protocol, owner_id=owner_id)
        revision_id = revision.id
        await update_persona(db, pid, owner_id=owner_id, expected_revision=1, fields={"prompt": "Updated instructions"})
        await delete_persona(db, pid, owner_id=owner_id)
    try:
        async with get_session() as db:
            draft = await get_protocol(db, protocol_id)
            published = await get_revision(db, revision_id)
            assert draft is not None and draft.graph == original
            assert published is not None and published.graph == original
            assert await get_persona(db, pid, owner_id=owner_id) is None
    finally:
        async with get_session() as db:
            await delete_protocol(db, protocol_id)


async def test_persona_api_upload_edit_ownership_and_delete(owner_id: uuid.UUID) -> None:
    app = FastAPI()
    app.include_router(router)
    current = owner_id

    async def user() -> User:
        return User(id=current)

    app.dependency_overrides[get_current_user] = user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/personas/upload", files={"file": ("careful_analyst.md", b"Be careful.")})
        assert response.status_code == 201
        persona = response.json()
        assert persona["name"] == "careful analyst"
        assert persona["prompt"] == "Be careful."
        assert persona["description"] is None
        assert persona["traits"] == {}
        assert len(persona["prompt_sha256"]) == 64
        path = f"/personas/{persona['id']}"
        current = uuid.uuid4()
        assert (await client.get("/personas")).json() == []
        assert (await client.get(path)).status_code == 404
        assert (await client.patch(path, json={"expected_revision": 1, "prompt": "wrong"})).status_code == 404
        assert (await client.delete(path)).status_code == 404
        current = owner_id
        updated = await client.patch(path, json={"expected_revision": 1, "prompt": "New text"})
        assert updated.status_code == 200
        assert updated.json()["revision"] == 2
        assert updated.json()["prompt_sha256"] != persona["prompt_sha256"]
        assert (await client.patch(path, json={"expected_revision": 1, "prompt": "stale"})).status_code == 409
        assert (await client.patch(path, json={"expected_revision": 2, "prompt": None})).status_code == 422
        assert (await client.delete(path)).status_code == 204
        assert (await client.get(path)).status_code == 404


async def test_persona_api_rejects_bad_uploads_and_requires_authentication() -> None:
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/personas")).status_code == 401

        async def user() -> User:
            return User(id=uuid.uuid4())

        app.dependency_overrides[get_current_user] = user
        assert (await client.post("/personas/upload", files={"file": ("persona.txt", b"text")})).status_code == 422
        assert (await client.post("/personas/upload", files={"file": ("persona.md", b"\xff")})).status_code == 422
