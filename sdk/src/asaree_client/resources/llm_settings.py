"""Per-user LLM provider credentials — create/list, matching asaree.api.llm_settings."""

from __future__ import annotations

from typing import Any

from asaree_client.models import LLMSetting


class LLMSettings:
    def __init__(self, client: Any) -> None:
        self._client = client

    def upsert(self, provider: str, api_key: str, *, api_base: str | None = None) -> LLMSetting:
        """Create or replace this user's credential for *provider*.

        For ``azure_foundry``, *api_base* is the Foundry resource name (or a
        full URL) — the server derives the actual endpoint from it.
        """
        payload: dict[str, Any] = {"provider": provider, "api_key": api_key}
        if api_base is not None:
            payload["api_base"] = api_base
        data = self._client._put("/llm-settings", json=payload)
        return LLMSetting(**data)

    def list(self) -> list[LLMSetting]:
        data = self._client._get("/llm-settings")
        return [LLMSetting(**s) for s in data]
