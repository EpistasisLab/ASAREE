"""MCP server registration — a thin layer over motoro.services.mcp_service.

The mechanism (spawn/dial, tool discovery, encrypted headers, the stdio
allowlist, the SSRF guard) is fully built in core already — see design doc
§6. ASAREE's job is exactly what's shown here: resolve ``owner_id`` from
``CurrentUser`` and call through. Validation errors from core's security
modules (a disallowed stdio command, an SSRF-blocked URL) are ``ValueError``
subclasses — caught broadly here and reported as 422 rather than a 500,
since they're the caller's mistake, not ASAREE's.

Reading/calling is scoped to the caller's own servers plus any global system
server (e.g. ASAREE's own bundled ``asaree-workspace``) — mutating a
server's registration is owner-only, with no admin/cross-user view yet,
matching how far the user model itself has gotten.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from motoro.mcp.registry import get_registry
from motoro.services import mcp_service
from motoro.services.mcp_service import MCPServerNameConflictError
from pydantic import BaseModel

from asaree.deps import CurrentUser

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


def _readable(config: Any, user: Any) -> bool:
    """A server is readable/callable if the caller owns it, or it's a global
    system server (``is_system=True``, ``owner_id=None``) — e.g. ASAREE's own
    bundled ``asaree-workspace``, available to every user by definition.
    Mutating actions (update/delete/refresh/reconnect) stay owner-only below —
    a shared system server's registration/connection isn't any one user's to
    change through this API.
    """
    return bool(config.owner_id == user.id or config.is_system)


class CallToolRequest(BaseModel):
    arguments: dict[str, Any] = {}


class CallToolResponse(BaseModel):
    is_error: bool
    content: str


class RegisterServerRequest(BaseModel):
    name: str
    transport: str
    command: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


class UpdateServerRequest(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


class ServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    command: str | None
    url: str | None
    status: str
    error_message: str | None
    capabilities: dict[str, Any] | None
    created_at: datetime


async def _capabilities_with_tool_annotations(config: Any, *, refresh: bool = False) -> dict[str, Any] | None:
    """Add standard MCP tool annotations that Motoro 0.6's cache omits.

    Motoro currently retains names, descriptions, and schemas in ``ToolInfo``
    but not the protocol's annotations. ASAREE needs ``readOnlyHint`` for a
    risk-aware evaluator UI, so read the already-open session once and cache
    the small annotation map on the client. Unknown/failing servers remain
    unannotated and are therefore treated conservatively by the frontend.
    """
    capabilities = dict(config.capabilities) if isinstance(config.capabilities, dict) else None
    tools = capabilities.get("tools") if capabilities else None
    if not isinstance(tools, list):
        return capabilities
    assert capabilities is not None
    entry = get_registry().servers.get(config.id)
    client = entry.client if entry is not None else None
    if client is None:
        return capabilities
    session = getattr(client, "_session", None)
    if session is None:
        return capabilities
    tool_names = frozenset(item.get("name") for item in tools if isinstance(item, dict))
    cached = getattr(client, "_asaree_tool_annotations", None)
    if refresh or not isinstance(cached, tuple) or cached[0] != tool_names:
        try:
            discovered = await session.list_tools()
            annotation_map = {
                tool.name: tool.annotations.model_dump(by_alias=True, exclude_none=True)
                for tool in discovered.tools
                if tool.annotations is not None
            }
            cached = (frozenset(tool.name for tool in discovered.tools), annotation_map)
            client._asaree_tool_annotations = cached  # type: ignore[attr-defined]
        except Exception:
            return capabilities
    annotation_map = cached[1]
    capabilities["tools"] = [
        {**item, **({"annotations": annotation_map[item.get("name")]} if item.get("name") in annotation_map else {})}
        if isinstance(item, dict) else item
        for item in tools
    ]
    return capabilities


async def _to_response(config: Any, *, refresh_annotations: bool = False) -> ServerResponse:
    return ServerResponse(
        id=config.id,
        name=config.name,
        transport=config.transport.value,
        command=config.command,
        url=config.url,
        status=config.status.value,
        error_message=config.error_message,
        capabilities=await _capabilities_with_tool_annotations(config, refresh=refresh_annotations),
        created_at=config.created_at,
    )


@router.post("", response_model=ServerResponse, status_code=201)
async def register_server_endpoint(body: RegisterServerRequest, user: CurrentUser) -> ServerResponse:
    if await mcp_service.get_server_by_name(body.name, owner_id=user.id) is not None:
        raise HTTPException(status_code=409, detail="A server with this name already exists")
    try:
        config = await mcp_service.register_server(
            name=body.name,
            transport=body.transport,
            command=body.command,
            url=body.url,
            headers=body.headers,
            owner_id=user.id,
        )
    except MCPServerNameConflictError as exc:
        raise HTTPException(status_code=409, detail="A server with this name already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _to_response(config, refresh_annotations=True)


@router.get("", response_model=list[ServerResponse])
async def list_servers_endpoint(user: CurrentUser) -> list[ServerResponse]:
    servers = await mcp_service.list_servers(owner_id=user.id)
    return list(await asyncio.gather(*(_to_response(s) for s in servers)))


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server_endpoint(server_id: uuid.UUID, user: CurrentUser) -> ServerResponse:
    config = await mcp_service.get_server(server_id)
    if config is None or not _readable(config, user):
        raise HTTPException(status_code=404, detail="No such server")
    return await _to_response(config)


@router.patch("/{server_id}", response_model=ServerResponse)
async def update_server_endpoint(server_id: uuid.UUID, body: UpdateServerRequest, user: CurrentUser) -> ServerResponse:
    existing = await mcp_service.get_server(server_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such server")
    try:
        config = await mcp_service.update_server(
            server_id,
            name=body.name,
            transport=body.transport,
            command=body.command,
            url=body.url,
            headers=body.headers,
        )
    except MCPServerNameConflictError as exc:
        raise HTTPException(status_code=409, detail="A server with this name already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert config is not None  # existence already checked above
    return await _to_response(config, refresh_annotations=True)


@router.delete("/{server_id}", status_code=204)
async def delete_server_endpoint(server_id: uuid.UUID, user: CurrentUser) -> None:
    existing = await mcp_service.get_server(server_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such server")
    await mcp_service.delete_server(server_id)


@router.post("/{server_id}/refresh", response_model=ServerResponse)
async def refresh_server_endpoint(server_id: uuid.UUID, user: CurrentUser) -> ServerResponse:
    existing = await mcp_service.get_server(server_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such server")
    config = await mcp_service.refresh_server(server_id)
    assert config is not None
    return await _to_response(config, refresh_annotations=True)


@router.post("/{server_id}/reconnect", response_model=ServerResponse)
async def reconnect_server_endpoint(server_id: uuid.UUID, user: CurrentUser) -> ServerResponse:
    existing = await mcp_service.get_server(server_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such server")
    config = await mcp_service.reconnect_server(server_id)
    assert config is not None
    return await _to_response(config, refresh_annotations=True)


@router.post("/{server_id}/tools/{tool_name}/call", response_model=CallToolResponse)
async def call_tool_endpoint(
    server_id: uuid.UUID, tool_name: str, body: CallToolRequest, user: CurrentUser
) -> CallToolResponse:
    """Invoke a tool directly, outside any agent run.

    502, not 500 or 404: the server row is readable by the caller (their own,
    or a global system server), so this isn't a not-found — it's the
    *downstream* MCP server refusing or failing the call, the same shape as
    any other upstream-gateway failure.
    """
    existing = await mcp_service.get_server(server_id)
    if existing is None or not _readable(existing, user):
        raise HTTPException(status_code=404, detail="No such server")
    try:
        outcome = await mcp_service.call_server_tool(server_id, tool_name, body.arguments)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail="No such server")
    is_error, content = outcome
    return CallToolResponse(is_error=is_error, content=content)


@router.post("/{server_id}/reset-session")
async def reset_session_endpoint(server_id: uuid.UUID, user: CurrentUser) -> dict[str, Any]:
    existing = await mcp_service.get_server(server_id)
    if existing is None or not _readable(existing, user):
        raise HTTPException(status_code=404, detail="No such server")
    try:
        result = await mcp_service.reset_server_session(server_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="No such server")
    return result
