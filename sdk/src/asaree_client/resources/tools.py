"""MCP server management and direct tool calls."""

from __future__ import annotations

import uuid
from typing import Any

from asaree_client.models import MCPServer, ToolCallResult

ResourceId = uuid.UUID | str
_UNSET: Any = object()


class Tools:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list_servers(self) -> list[MCPServer]:
        data = self._client._get("/mcp-servers")
        return [MCPServer(**s) for s in data]

    def create_server(
        self,
        *,
        name: str,
        transport: str,
        command: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> MCPServer:
        payload: dict[str, Any] = {"name": name, "transport": transport}
        for key, value in {"command": command, "url": url, "headers": headers}.items():
            if value is not None:
                payload[key] = value
        return MCPServer(**self._client._post("/mcp-servers", json=payload))

    def get_server(self, server_id: ResourceId) -> MCPServer:
        return MCPServer(**self._client._get(f"/mcp-servers/{server_id}"))

    def update_server(
        self,
        server_id: ResourceId,
        *,
        name: str | None = _UNSET,
        transport: str | None = _UNSET,
        command: str | None = _UNSET,
        url: str | None = _UNSET,
        headers: dict[str, str] | None = _UNSET,
    ) -> MCPServer:
        payload = {
            key: value
            for key, value in {
                "name": name,
                "transport": transport,
                "command": command,
                "url": url,
                "headers": headers,
            }.items()
            if value is not _UNSET
        }
        return MCPServer(**self._client._patch(f"/mcp-servers/{server_id}", json=payload))

    def delete_server(self, server_id: ResourceId) -> None:
        self._client._delete(f"/mcp-servers/{server_id}")

    def refresh_server(self, server_id: ResourceId) -> MCPServer:
        return MCPServer(**self._client._post(f"/mcp-servers/{server_id}/refresh"))

    def reconnect_server(self, server_id: ResourceId) -> MCPServer:
        return MCPServer(**self._client._post(f"/mcp-servers/{server_id}/reconnect"))

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
