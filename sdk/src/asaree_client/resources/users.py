"""Unauthenticated user and API-key bootstrap endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from asaree_client.models import BootstrapApiToken, BootstrapUser

ResourceId = uuid.UUID | str


class Users:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(self, *, email: str, password: str) -> BootstrapUser:
        data = self._client._post("/users", json={"email": email, "password": password})
        return BootstrapUser(**data)

    def issue_token(
        self,
        user_id: ResourceId,
        *,
        password: str,
        name: str = "default",
    ) -> BootstrapApiToken:
        data = self._client._post(
            f"/users/{user_id}/tokens",
            json={"password": password, "name": name},
        )
        return BootstrapApiToken(**data)
