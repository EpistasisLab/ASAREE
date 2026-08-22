"""Unit tests for services.credential_resolver.resolve -- the resolver installed
via motoro.services.credentials.set_credential_resolver. Same
real-Postgres, throwaway-user fixture as tests/test_experiments.py."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from motoro.schemas.agent import ModelConfig

from asaree.models.database import dispose_engine, get_session
from asaree.models.user import User
from asaree.services.credential_resolver import LLMCredentialNotConfiguredError, resolve
from asaree.services.user_llm_settings import upsert_setting


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def owner_id() -> AsyncIterator[uuid.UUID]:
    async with get_session() as db:
        user = User(
            email=f"credential-resolver-test-{uuid.uuid4().hex}@example.com",
            hashed_password="not-a-real-hash",
            display_name="Credential Resolver Test User",
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


@pytest.mark.parametrize("provider", ["anthropic", "openai", "azure_foundry", "openrouter", "local"])
async def test_resolve_raises_when_no_credential_saved(owner_id: uuid.UUID, provider: str) -> None:
    config = ModelConfig(provider=provider, model="some-model")
    with pytest.raises(LLMCredentialNotConfiguredError, match=provider):
        await resolve(config, owner_id)


async def test_resolve_returns_none_without_principal() -> None:
    config = ModelConfig(provider="anthropic", model="claude-sonnet-5")
    assert await resolve(config, None) is None


async def test_resolve_abstains_for_unsupported_provider(owner_id: uuid.UUID) -> None:
    config = ModelConfig(provider="bedrock", model="some-model")
    assert await resolve(config, owner_id) is None


async def test_resolve_anthropic_with_saved_credential(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        await upsert_setting(db, user_id=owner_id, provider="anthropic", api_key="sk-ant-real-key")

    config = ModelConfig(provider="anthropic", model="claude-sonnet-5")
    conn = await resolve(config, owner_id)

    assert conn is not None
    assert conn["api_key"] == "sk-ant-real-key"
    assert conn["model"] is None


async def test_resolve_azure_foundry_with_saved_credential(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        await upsert_setting(
            db, user_id=owner_id, provider="azure_foundry", api_key="secret-key", api_base="my-resource"
        )

    config = ModelConfig(provider="azure_foundry", model="gpt-5")
    conn = await resolve(config, owner_id)

    assert conn is not None
    assert conn["api_key"] == "secret-key"
    assert conn["model"] == "azure_ai/gpt-5"
    assert conn["api_base"] == "https://my-resource.services.ai.azure.com"


async def test_resolve_azure_foundry_saved_credential_missing_api_base_raises(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        await upsert_setting(db, user_id=owner_id, provider="azure_foundry", api_key="secret-key", api_base=None)

    config = ModelConfig(provider="azure_foundry", model="gpt-5")
    with pytest.raises(ValueError, match="api_base"):
        await resolve(config, owner_id)


async def test_resolve_openrouter_with_saved_credential(owner_id: uuid.UUID) -> None:
    """No model-string override -- litellm's own `openrouter/` route already
    matches the provider's own value, unlike azure_foundry/local."""
    async with get_session() as db:
        await upsert_setting(db, user_id=owner_id, provider="openrouter", api_key="sk-or-real-key")

    config = ModelConfig(provider="openrouter", model="anthropic/claude-sonnet-5")
    conn = await resolve(config, owner_id)

    assert conn is not None
    assert conn["api_key"] == "sk-or-real-key"
    assert conn["model"] is None


async def test_resolve_local_with_saved_credential(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        await upsert_setting(
            db, user_id=owner_id, provider="local", api_key="", api_base="http://localhost:8000/v1"
        )

    config = ModelConfig(provider="local", model="llama-3-70b-instruct")
    conn = await resolve(config, owner_id)

    assert conn is not None
    # Most self-hosted servers don't check the key, but litellm's OpenAI
    # client still requires a non-empty string.
    assert conn["api_key"] == "not-needed"
    assert conn["model"] == "openai/llama-3-70b-instruct"
    assert conn["api_base"] == "http://localhost:8000/v1"


async def test_resolve_local_saved_credential_missing_api_base_raises(owner_id: uuid.UUID) -> None:
    async with get_session() as db:
        await upsert_setting(db, user_id=owner_id, provider="local", api_key="", api_base=None)

    config = ModelConfig(provider="local", model="m")
    with pytest.raises(ValueError, match="api_base"):
        await resolve(config, owner_id)
