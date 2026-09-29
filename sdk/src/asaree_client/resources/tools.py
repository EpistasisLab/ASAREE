"""MCP server management and direct tool calls."""

from __future__ import annotations

import uuid
from typing import Any

from asaree_client._sentinel import UNSET, UnsetType
from asaree_client.models import MCPOAuthAuthorization, MCPServer, ToolCallResult

ResourceId = uuid.UUID | str


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
        server_env: dict[str, str] | None = None,
    ) -> MCPServer:
        """Register a server.

        *headers* carries static HTTP credentials (e.g. ``{"Authorization":
        "Bearer ..."}``); *server_env* carries encrypted environment
        credentials for a stdio server. Neither is ever returned by the API.
        """
        payload: dict[str, Any] = {"name": name, "transport": transport}
        for key, value in {"command": command, "url": url, "headers": headers, "server_env": server_env}.items():
            if value is not None:
                payload[key] = value
        return MCPServer(**self._client._post("/mcp-servers", json=payload))

    def get_server(self, server_id: ResourceId) -> MCPServer:
        return MCPServer(**self._client._get(f"/mcp-servers/{server_id}"))

    def update_server(
        self,
        server_id: ResourceId,
        *,
        name: str | None = None,
        transport: str | None = None,
        command: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None | UnsetType = UNSET,
        server_env: dict[str, str] | None | UnsetType = UNSET,
    ) -> MCPServer:
        """Update supplied connection fields and credentials.

        For *name*, *transport*, *command* and *url*, ``None`` means unchanged:
        the endpoint cannot clear them. Credentials distinguish omission from
        clearing: leave *headers*/*server_env* unset to keep the stored values,
        pass a mapping to replace them, or ``None`` to clear them.
        """
        payload: dict[str, Any] = {
            key: value
            for key, value in {"name": name, "transport": transport, "command": command, "url": url}.items()
            if value is not None
        }
        if not isinstance(headers, UnsetType):
            payload["headers"] = headers
        if not isinstance(server_env, UnsetType):
            payload["server_env"] = server_env
        return MCPServer(**self._client._patch(f"/mcp-servers/{server_id}", json=payload))

    def begin_oauth(self, server_id: ResourceId, *, scope: str | None = None) -> MCPOAuthAuthorization:
        """Start an OAuth authorization for an HTTP server.

        Open the returned ``authorization_url`` in a browser before
        ``expires_at``. The provider redirects to the ASAREE callback, which
        stores the tokens server-side; poll :meth:`get_server`
        and check ``authentication.configured`` to observe completion.
        """
        data = self._client._post(f"/mcp-servers/{server_id}/oauth/start", json={"scope": scope})
        return MCPOAuthAuthorization(**data)

    def clear_credentials(self, server_id: ResourceId, *, revoke: bool = False) -> MCPServer:
        """Clear every stored credential kind without deleting the registration.

        With *revoke*, ASAREE first asks the OAuth provider to revoke its
        tokens; local credentials are cleared whether or not that succeeds.
        """
        data = self._client._post(f"/mcp-servers/{server_id}/credentials/clear", json={"revoke": revoke})
        return MCPServer(**data)

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
        retry: bool = False,
    ) -> ToolCallResult:
        """*timeout* overrides the client's default for just this call (e.g. a
        long-running direct tool invocation like ``run_model_script``).
        Direct tool calls are not retried by default because a timeout may
        happen after the tool already changed state. Set *retry* to ``True``
        only for a tool known to be safe to replay.
        """
        kwargs: dict[str, Any] = {"json": {"arguments": arguments or {}}}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if retry:
            kwargs["extensions"] = {"asaree_allow_retry": True}
        data = self._client._post(f"/mcp-servers/{server_id}/tools/{tool_name}/call", **kwargs)
        return ToolCallResult(**data)

    def reset_session(self, server_id: ResourceId) -> dict[str, Any]:
        return self._client._post(f"/mcp-servers/{server_id}/reset-session")  # type: ignore[no-any-return]
