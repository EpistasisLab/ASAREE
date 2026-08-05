"""MCP server registration — a thin layer over agentic_core.services.mcp_service.

The mechanism (spawn/dial, tool discovery, encrypted headers, the stdio
allowlist, the SSRF guard) is fully built in core already — see design doc
§6. ASAREE's job is exactly what's shown here: resolve ``owner_id`` from
``CurrentUser`` and call through. Validation errors from core's security
modules (a disallowed stdio command, an SSRF-blocked URL) are ``ValueError``
subclasses — caught broadly here and reported as 422 rather than a 500,
since they're the caller's mistake, not ASAREE's.

Listing is scoped to the caller's own servers — there's no admin/cross-user
view yet, matching how far the user model itself has gotten.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from agentic_core.services import mcp_service
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from asaree.deps import CurrentUser

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


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
    if config is None or config.owner_id != user.id:
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
