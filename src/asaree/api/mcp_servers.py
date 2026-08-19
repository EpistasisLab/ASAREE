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

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from motoro.services import mcp_service
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


def _to_response(config: Any) -> ServerResponse:
    return ServerResponse(
        id=config.id,
        name=config.name,
        transport=config.transport.value,
        command=config.command,
        url=config.url,
        status=config.status.value,
        error_message=config.error_message,
        capabilities=config.capabilities,
        created_at=config.created_at,
    )


@router.post("", response_model=ServerResponse, status_code=201)
async def register_server_endpoint(body: RegisterServerRequest, user: CurrentUser) -> ServerResponse:
    if await mcp_service.get_server_by_name(body.name) is not None:
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(config)


@router.get("", response_model=list[ServerResponse])
async def list_servers_endpoint(user: CurrentUser) -> list[ServerResponse]:
    servers = await mcp_service.list_servers(owner_id=user.id)
    return [_to_response(s) for s in servers]


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server_endpoint(server_id: uuid.UUID, user: CurrentUser) -> ServerResponse:
    config = await mcp_service.get_server(server_id)
    if config is None or not _readable(config, user):
        raise HTTPException(status_code=404, detail="No such server")
    return _to_response(config)


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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert config is not None  # existence already checked above
    return _to_response(config)


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
    return _to_response(config)


@router.post("/{server_id}/reconnect", response_model=ServerResponse)
async def reconnect_server_endpoint(server_id: uuid.UUID, user: CurrentUser) -> ServerResponse:
    existing = await mcp_service.get_server(server_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such server")
    config = await mcp_service.reconnect_server(server_id)
    assert config is not None
    return _to_response(config)


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
