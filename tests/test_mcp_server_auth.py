import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from motoro.services.mcp_service import UNSET

from asaree.api import mcp_servers


def _config(owner_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), owner_id=owner_id)


@pytest.mark.asyncio
async def test_update_leaves_omitted_credentials_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = uuid.uuid4()
    config = _config(owner_id)
    captured: dict[str, object] = {}

    async def update(server_id: uuid.UUID, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return config

    monkeypatch.setattr("asaree.api.mcp_servers.mcp_service.get_server", lambda _server_id: _async(config))
    monkeypatch.setattr("asaree.api.mcp_servers.mcp_service.update_server", update)
    monkeypatch.setattr(mcp_servers, "_to_response", lambda _config, **_kwargs: _async("response"))

    result = cast(Any, await mcp_servers.update_server_endpoint(
        config.id,
        mcp_servers.UpdateServerRequest(name="renamed"),
        cast(Any, SimpleNamespace(id=owner_id)),
    ))

    assert result == "response"
    assert captured["headers"] is UNSET
    assert captured["server_env"] is UNSET


@pytest.mark.asyncio
async def test_update_passes_explicit_credential_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = uuid.uuid4()
    config = _config(owner_id)
    captured: dict[str, object] = {}

    async def update(server_id: uuid.UUID, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return config

    monkeypatch.setattr("asaree.api.mcp_servers.mcp_service.get_server", lambda _server_id: _async(config))
    monkeypatch.setattr("asaree.api.mcp_servers.mcp_service.update_server", update)
    monkeypatch.setattr(mcp_servers, "_to_response", lambda _config, **_kwargs: _async("response"))

    await mcp_servers.update_server_endpoint(
        config.id,
        mcp_servers.UpdateServerRequest(headers=None, server_env={}),
        cast(Any, SimpleNamespace(id=owner_id)),
    )

    assert captured["headers"] is None
    assert captured["server_env"] == {}


@pytest.mark.asyncio
async def test_oauth_start_uses_configured_callback_and_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = uuid.uuid4()
    server_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def begin(server_id_arg: uuid.UUID, **kwargs: object) -> SimpleNamespace:
        captured["server_id"] = server_id_arg
        captured.update(kwargs)
        return SimpleNamespace(
            authorization_url="https://auth.example/authorize",
            expires_at=datetime.now(UTC),
            transaction_id="transaction",
        )

    monkeypatch.setattr("asaree.api.mcp_servers.mcp_service.begin_oauth_authorization", begin)
    monkeypatch.setattr(
        mcp_servers,
        "get_settings",
        lambda: SimpleNamespace(
            mcp_oauth_callback_url="https://asaree.example/api/mcp-servers/oauth/callback",
            mcp_oauth_client_metadata_url=None,
            frontend_url="https://asaree.example",
        ),
    )

    response = await mcp_servers.start_oauth_endpoint(
        server_id,
        mcp_servers.OAuthStartRequest(scope="tools.read"),
        cast(Any, SimpleNamespace(id=owner_id)),
    )

    assert response.authorization_url == "https://auth.example/authorize"
    assert captured["server_id"] == server_id
    assert captured["redirect_uri"] == "https://asaree.example/api/mcp-servers/oauth/callback"
    assert captured["owner_id"] == owner_id
    assert cast(Any, captured["client_metadata"]).scope == "tools.read"


@pytest.mark.asyncio
async def test_oauth_callback_completes_and_redirects_without_exposing_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def complete(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(id=server_id)

    monkeypatch.setattr("asaree.api.mcp_servers.mcp_service.complete_oauth_authorization", complete)
    monkeypatch.setattr(mcp_servers, "get_settings", lambda: SimpleNamespace(frontend_url="https://asaree.example"))

    response = await mcp_servers.complete_oauth_callback(
        code="secret-code", state="secret-state", iss="https://issuer", error=None
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"https://asaree.example/mcp/oauth/callback?status=success&server_id={server_id}"
    )
    assert "secret-code" not in response.headers["location"]
    assert captured == {"code": "secret-code", "state": "secret-state", "iss": "https://issuer"}


@pytest.mark.asyncio
async def test_server_response_exposes_only_safe_authentication_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    config = SimpleNamespace(
        id=server_id,
        owner_id=owner_id,
        name="private-server",
        transport=SimpleNamespace(value="http"),
        command=None,
        url="https://mcp.example/mcp",
        status=SimpleNamespace(value="connected"),
        error_message=None,
        capabilities={"tools": []},
        created_at=datetime.now(UTC),
        headers_encrypted="encrypted-secret-material",
    )
    status = SimpleNamespace(
        auth_mode="static_headers",
        configured=True,
        authorization_required=False,
        static_headers_configured=True,
        stdio_env_configured=False,
        stdio_env_names=(),
    )
    monkeypatch.setattr(
        "asaree.api.mcp_servers.mcp_service.get_authentication_status",
        lambda _server_id: _async(status),
    )

    response = await mcp_servers._to_response(config, viewer_id=owner_id)
    payload = response.model_dump(mode="json")

    assert payload["authentication"]["auth_mode"] == "static_headers"
    assert payload["credential_management_allowed"] is True
    assert "headers" not in payload
    assert "encrypted-secret-material" not in str(payload)


async def _async(value: object) -> object:
    return value
