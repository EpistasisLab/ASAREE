"""Agent resource — create/get/update, matching asaree.api.agents."""

from __future__ import annotations

import uuid
from typing import Any

from asaree_client.models import Agent

ResourceId = uuid.UUID | str


class Agents:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(
        self,
        *,
        name: str,
        goal: str,
        description: str = "",
        system_prompt: str = "",
        model_config_data: dict[str, Any] | None = None,
        pattern_config: dict[str, Any] | None = None,
        tool_config: dict[str, Any] | None = None,
        memory_config: dict[str, Any] | None = None,
        output_contract: dict[str, Any] | None = None,
        budget_limit_usd: float | None = None,
        max_run_duration_seconds: int | None = None,
    ) -> Agent:
        payload: dict[str, Any] = {
            "name": name,
            "goal": goal,
            "description": description,
            "system_prompt": system_prompt,
        }
        if model_config_data is not None:
            payload["model_config_data"] = model_config_data
        if pattern_config is not None:
            payload["pattern_config"] = pattern_config
        if tool_config is not None:
            payload["tool_config"] = tool_config
        if memory_config is not None:
            payload["memory_config"] = memory_config
        if output_contract is not None:
            payload["output_contract"] = output_contract
        if budget_limit_usd is not None:
            payload["budget_limit_usd"] = budget_limit_usd
        if max_run_duration_seconds is not None:
            payload["max_run_duration_seconds"] = max_run_duration_seconds
        data = self._client._post("/agents", json=payload)
        return Agent(**data)

    def get(self, agent_id: ResourceId) -> Agent:
        data = self._client._get(f"/agents/{agent_id}")
        return Agent(**data)

    def get_by_name(self, name: str) -> Agent:
        data = self._client._get(f"/agents/by-name/{name}")
        return Agent(**data)

    def list(self) -> list[Agent]:
        data = self._client._get("/agents")
        return [Agent(**a) for a in data]

    def update(
        self,
        agent_id: ResourceId,
        *,
        name: str | None = None,
        goal: str | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        model_config_data: dict[str, Any] | None = None,
        pattern_config: dict[str, Any] | None = None,
        tool_config: dict[str, Any] | None = None,
        memory_config: dict[str, Any] | None = None,
        output_contract: dict[str, Any] | None = None,
        budget_limit_usd: float | None = None,
        max_run_duration_seconds: int | None = None,
    ) -> Agent:
        payload: dict[str, Any] = {}
        for key, value in {
            "name": name,
            "goal": goal,
            "description": description,
            "system_prompt": system_prompt,
            "model_config_data": model_config_data,
            "pattern_config": pattern_config,
            "tool_config": tool_config,
            "memory_config": memory_config,
            "output_contract": output_contract,
            "budget_limit_usd": budget_limit_usd,
            "max_run_duration_seconds": max_run_duration_seconds,
        }.items():
            if value is not None:
                payload[key] = value
        data = self._client._patch(f"/agents/{agent_id}", json=payload)
        return Agent(**data)
