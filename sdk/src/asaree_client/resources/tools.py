"""MCP tool passthrough, matching asaree.api.mcp_servers.

Registration itself is one-time environment setup (done once via curl, per
the SDK README), not part of the ongoing driver-script surface — this
resource is deliberately limited to what a notebook calls per experiment
run: discover registered servers, call a tool directly, reset a session.
"""

from __future__ import annotations

import uuid
from typing import Any

from asaree_client.models import MCPServer, ToolCallResult

ResourceId = uuid.UUID | str


class Tools:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list_servers(self) -> list[MCPServer]:
        data = self._client._get("/mcp-servers")
        return [MCPServer(**s) for s in data]

    def call_tool(
        self,
        server_id: ResourceId,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        retry: bool = True,
    ) -> ToolCallResult:
        """*timeout* overrides the client's default for just this call (e.g. a
        long-running direct tool invocation like ``run_model_script``).
        *retry* set to ``False`` opts this call out of the client's automatic
        retry policy — for a non-idempotent call where re-sending on a
        transient failure could double-run something expensive.
        """
        kwargs: dict[str, Any] = {"json": {"arguments": arguments or {}}}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if not retry:
            kwargs["extensions"] = {"asaree_no_retry": True}
        data = self._client._post(f"/mcp-servers/{server_id}/tools/{tool_name}/call", **kwargs)
        return ToolCallResult(**data)

    def reset_session(self, server_id: ResourceId) -> dict[str, Any]:
        return self._client._post(f"/mcp-servers/{server_id}/reset-session")  # type: ignore[no-any-return]
