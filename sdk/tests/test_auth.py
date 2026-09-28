from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from asaree_client._transport import build_headers
from asaree_client.exceptions import AsareeAuthenticationError
from asaree_client.resources.auth import Auth
from asaree_client.resources.users import Users


class AuthClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._refresh_token: str | None = None
        self.session_tokens: tuple[str, str] | None = None
        self.cleared = False

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        return next(self.responses)

    def _get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs)

    def _patch(self, path: str, **kwargs: Any) -> Any:
        return self._request("PATCH", path, **kwargs)

    def _delete(self, path: str, **kwargs: Any) -> Any:
        return self._request("DELETE", path, **kwargs)

    def _set_session_tokens(self, access_token: str, refresh_token: str) -> None:
        self.session_tokens = (access_token, refresh_token)
        self._refresh_token = refresh_token

    def _clear_session_tokens(self) -> None:
        self.cleared = True
        self._refresh_token = None


def _token_response() -> dict[str, Any]:
    return {
        "access_token": "access-2",
        "refresh_token": "refresh-2",
        "token_type": "bearer",
        "expires_in": 900,
        "user": {
            "id": str(uuid.uuid4()),
            "email": "researcher@example.com",
            "display_name": "Researcher",
            "is_active": True,
            "is_admin": False,
            "created_at": datetime.now(tz=UTC).isoformat(),
        },
    }


def test_build_headers_supports_exactly_one_authentication_mode() -> None:
    assert build_headers("api-key", "ignored")["X-API-Key"] == "api-key"
    assert "Authorization" not in build_headers("api-key", "ignored")
    assert build_headers(None, "access")["Authorization"] == "Bearer access"


def test_login_activates_returned_session() -> None:
    client = AuthClient([_token_response()])

    tokens = Auth(client).login(email="researcher@example.com", password="password123")

    assert client.calls == [
        (
            "POST",
            "/auth/login",
            {"json": {"email": "researcher@example.com", "password": "password123"}},
        )
    ]
    assert client.session_tokens == (tokens.access_token, tokens.refresh_token)


def test_refresh_uses_and_rotates_stored_refresh_token() -> None:
    client = AuthClient([_token_response()])
    client._refresh_token = "refresh-1"

    Auth(client).refresh()

    assert client.calls == [("POST", "/auth/refresh", {"headers": {"Authorization": "Bearer refresh-1"}})]
    assert client.session_tokens == ("access-2", "refresh-2")


def test_refresh_requires_a_token() -> None:
    with pytest.raises(AsareeAuthenticationError, match="No refresh token available"):
        Auth(AuthClient([])).refresh()


def test_logout_clears_session_after_server_accepts_it() -> None:
    client = AuthClient([None])

    Auth(client).logout()

    assert client.calls == [("POST", "/auth/logout", {})]
    assert client.cleared is True


def test_bootstrap_user_and_token_requests_are_json() -> None:
    user_id = uuid.uuid4()
    client = AuthClient(
        [
            {"id": str(user_id), "email": "researcher@example.com"},
            {"id": str(uuid.uuid4()), "name": "automation", "token": "secret"},
        ]
    )
    users = Users(client)

    user = users.create(email="researcher@example.com", password="password123")
    token = users.issue_token(user.id, password="password123", name="automation")

    assert token.token == "secret"
    assert client.calls == [
        (
            "POST",
            "/users",
            {"json": {"email": "researcher@example.com", "password": "password123"}},
        ),
        (
            "POST",
            f"/users/{user_id}/tokens",
            {"json": {"password": "password123", "name": "automation"}},
        ),
    ]
