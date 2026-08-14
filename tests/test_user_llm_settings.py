"""Unit tests for services.user_llm_settings -- CRUD for per-user LLM
credentials. Same real-Postgres, throwaway-user fixture as
tests/test_credential_resolver.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio

from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.user_llm_settings import delete_setting, get_setting, upsert_setting


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"user-llm-settings-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="User LLM Settings Test User",
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        uid = user.id
    yield uid
    async with get_session() as db:
        db_user = await db.get(User, uid)
        if db_user is not None:
            await db.delete(db_user)


async def test_upsert_then_get_round_trips_azure_project_endpoint(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        await upsert_setting(
            db,
            user_id=owner_id,
            provider="azure_foundry",
            api_key="secret-key",
            api_base="https://my-resource.services.ai.azure.com",
            azure_project_endpoint="https://my-resource.services.ai.azure.com/api/projects/my-project",
        )

    async with get_session() as db:
        setting = await get_setting(db, user_id=owner_id, provider="azure_foundry")
        assert setting is not None
        assert setting.api_base == "https://my-resource.services.ai.azure.com"
        assert setting.azure_project_endpoint == "https://my-resource.services.ai.azure.com/api/projects/my-project"


async def test_upsert_derives_api_base_from_azure_project_endpoint(owner_id: uuid.UUID) -> None:
    # The GUI only asks for the Project endpoint now -- api_base must be
    # derived even when it's not passed separately (or is stale/wrong),
    # since the project endpoint already contains the resource host.
    async with get_session() as db:
        await upsert_setting(
            db,
            user_id=owner_id,
            provider="azure_foundry",
            api_key="secret-key",
            api_base="this-should-be-overridden",
            azure_project_endpoint="https://my-resource.services.ai.azure.com/api/projects/my-project",
        )

    async with get_session() as db:
        setting = await get_setting(db, user_id=owner_id, provider="azure_foundry")
        assert setting is not None
        assert setting.api_base == "https://my-resource.services.ai.azure.com"


async def test_upsert_leaves_api_base_alone_without_a_project_endpoint(owner_id: uuid.UUID) -> None:
    # No project endpoint given (e.g. a hypothetical project-less resource) --
    # api_base is used exactly as passed, no derivation attempted.
    async with get_session() as db:
        await upsert_setting(
            db, user_id=owner_id, provider="azure_foundry", api_key="secret-key", api_base="my-resource"
        )

    async with get_session() as db:
        setting = await get_setting(db, user_id=owner_id, provider="azure_foundry")
        assert setting is not None
        assert setting.api_base == "my-resource"
        assert setting.azure_project_endpoint is None


async def test_upsert_replaces_azure_project_endpoint_with_none_when_omitted(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        await upsert_setting(
            db,
            user_id=owner_id,
            provider="azure_foundry",
            api_key="secret-key",
            api_base="my-resource",
            azure_project_endpoint="https://my-resource.services.ai.azure.com/api/projects/my-project",
        )

    # A second save without azure_project_endpoint is a full replace, not a
    # partial patch -- matches update_protocol's "always full replacement"
    # convention elsewhere in this codebase.
    async with get_session() as db:
        await upsert_setting(
            db, user_id=owner_id, provider="azure_foundry", api_key="new-secret-key", api_base="my-resource"
        )

    async with get_session() as db:
        setting = await get_setting(db, user_id=owner_id, provider="azure_foundry")
        assert setting is not None
        assert setting.azure_project_endpoint is None


async def test_delete_setting_removes_it(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        await upsert_setting(db, user_id=owner_id, provider="anthropic", api_key="secret-key")

    async with get_session() as db:
        deleted = await delete_setting(db, user_id=owner_id, provider="anthropic")
        assert deleted is True

    async with get_session() as db:
        assert await get_setting(db, user_id=owner_id, provider="anthropic") is None


async def test_delete_setting_returns_false_when_nothing_to_delete(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        deleted = await delete_setting(db, user_id=owner_id, provider="anthropic")
        assert deleted is False
