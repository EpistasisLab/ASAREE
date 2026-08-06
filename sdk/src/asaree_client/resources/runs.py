"""Run resource, matching asaree.api.runs.

``POST /runs`` executes inline — by the time ``start`` returns, the run is
already terminal. ``wait`` exists only so a ported driver script that used
to poll ARES doesn't need its call sites rewritten; here it's just a
re-fetch, no actual waiting happens.

Because the run executes inline, ``start`` itself is the long-blocking call —
not ``wait``, despite the name suggesting otherwise. The client's default HTTP
timeout (``ASAREE_TIMEOUT``, 30s) is nowhere near enough for a real ReAct loop
with real tool calls; pass ``timeout=`` explicitly for anything but a trivial
single-pass agent, the same way ``tools.call_tool`` takes one for long-running
tools.
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
        timeout: float | None = None,
    ) -> Run:
        """Create and execute a run, blocking until it's terminal.

        *timeout* overrides the client's default HTTP timeout for this one
        call — see the module docstring for why that default is too short
        for anything but a trivial run.
        """
        payload: dict[str, Any] = {"agent_id": str(agent_id), "user_input": user_input}
        if pattern_overrides is not None:
            payload["pattern_overrides"] = pattern_overrides
        if metadata is not None:
            payload["metadata"] = metadata
        if model_config_override is not None:
            payload["model_config_override"] = model_config_override
        kwargs: dict[str, Any] = {"json": payload}
        if timeout is not None:
            kwargs["timeout"] = timeout
        data = self._client._post("/runs", **kwargs)
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
