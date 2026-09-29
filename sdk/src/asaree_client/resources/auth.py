"""Account sessions, profiles, passwords, and API-token management."""

from __future__ import annotations

import uuid
from typing import Any

from asaree_client.exceptions import AsareeAuthenticationError
from asaree_client.models import ApiToken, ApiTokenPage, AuthTokens, Message, UserAccount

ResourceId = uuid.UUID | str


class Auth:
    def __init__(self, client: Any) -> None:
        self._client = client

    def register(self, *, email: str, password: str, display_name: str) -> UserAccount:
        data = self._client._post(
            "/auth/register",
            json={"email": email, "password": password, "display_name": display_name},
        )
        return UserAccount(**data)

    def login(self, *, email: str, password: str) -> AuthTokens:
        data = self._client._post("/auth/login", json={"email": email, "password": password})
        tokens = AuthTokens(**data)
        self._client._set_session_tokens(tokens.access_token, tokens.refresh_token)
        return tokens

    def refresh(self, refresh_token: str | None = None) -> AuthTokens:
        token = refresh_token or self._client._refresh_token
        if token is None:
            raise AsareeAuthenticationError(detail="No refresh token available")
        data = self._client._post(
            "/auth/refresh",
            headers={"Authorization": f"Bearer {token}"},
        )
        tokens = AuthTokens(**data)
        self._client._set_session_tokens(tokens.access_token, tokens.refresh_token)
        return tokens

    def logout(self) -> None:
        self._client._post("/auth/logout")
        self._client._clear_session_tokens()

    def forgot_password(self, email: str) -> Message:
        return Message(**self._client._post("/auth/forgot-password", json={"email": email}))

    def reset_password(self, *, token: str, new_password: str) -> Message:
        data = self._client._post("/auth/reset-password", json={"token": token, "new_password": new_password})
        return Message(**data)

    def get_profile(self) -> UserAccount:
        return UserAccount(**self._client._get("/auth/me"))

    def update_profile(self, *, display_name: str | None = None, email: str | None = None) -> UserAccount:
        payload = {
            key: value for key, value in {"display_name": display_name, "email": email}.items() if value is not None
        }
        return UserAccount(**self._client._patch("/auth/me", json=payload))

    def change_password(self, *, current_password: str, new_password: str) -> None:
        self._client._post(
            "/auth/me/password",
            json={"current_password": current_password, "new_password": new_password},
        )

    def create_api_token(self, *, name: str, expires_in_days: int | None = None) -> ApiToken:
        payload: dict[str, Any] = {"name": name}
        if expires_in_days is not None:
            payload["expires_in_days"] = expires_in_days
        return ApiToken(**self._client._post("/auth/me/tokens", json=payload))

    def list_api_tokens(self, *, offset: int = 0, limit: int = 20) -> ApiTokenPage:
        data = self._client._get("/auth/me/tokens", params={"offset": offset, "limit": limit})
        return ApiTokenPage(**data)

    def revoke_api_token(self, token_id: ResourceId) -> None:
        self._client._delete(f"/auth/me/tokens/{token_id}")
