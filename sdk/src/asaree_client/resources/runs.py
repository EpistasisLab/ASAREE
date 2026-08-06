"""Run resource, matching asaree.api.runs.

``POST /runs`` executes inline — by the time ``start`` returns, the run is
already terminal. ``wait`` exists only so a ported driver script that used
to poll ARES doesn't need its call sites rewritten; here it's just a
re-fetch, no actual waiting happens.
"""

from __future__ import annotations

import uuid
from typing import Any

from asaree_client.models import Run, RunStep

ResourceId = uuid.UUID | str


class Runs:
    def __init__(self, client: Any) -> None:
        self._client = client

    def start(
        self,
        agent_id: ResourceId,
        user_input: str,
        *,
        pattern_overrides: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        model_config_override: dict[str, Any] | None = None,
    ) -> Run:
        payload: dict[str, Any] = {"agent_id": str(agent_id), "user_input": user_input}
        if pattern_overrides is not None:
            payload["pattern_overrides"] = pattern_overrides
        if metadata is not None:
            payload["metadata"] = metadata
        if model_config_override is not None:
            payload["model_config_override"] = model_config_override
        data = self._client._post("/runs", json=payload)
        return Run(**data)

    def get(self, run_id: ResourceId) -> Run:
        data = self._client._get(f"/runs/{run_id}")
        return Run(**data)

    def wait(self, run_id: ResourceId, **_kwargs: Any) -> Run:
        """Re-fetch a run. Accepts and ignores ``timeout``/``poll_interval`` —
        ASAREE runs are already terminal by the time ``start`` returns."""
        return self.get(run_id)

    def list_all(self, *, agent_id: ResourceId | None = None) -> list[Run]:
        params = {"agent_id": str(agent_id)} if agent_id is not None else None
        data = self._client._get("/runs", params=params)
        return [Run(**r) for r in data]

    def get_steps(self, run_id: ResourceId) -> list[RunStep]:
        data = self._client._get(f"/runs/{run_id}/steps")
        return [RunStep(**s) for s in data]
