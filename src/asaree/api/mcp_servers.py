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
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from motoro.mcp.oauth import (
    MCPAuthConfigurationError,
    MCPAuthenticationError,
    MCPOAuthCallbackError,
    MCPOAuthDiscoveryError,
    MCPOAuthStateError,
    MCPReauthorizationRequiredError,
)
from motoro.mcp.registry import get_registry
from motoro.security.mcp_credentials import MCPCredentialValidationError
from motoro.services import mcp_service
from motoro.services.mcp_service import (
    UNSET,
    MCPOAuthClientMetadata,
    MCPServerNameConflictError,
    MCPServerNotFoundError,
)
from pydantic import BaseModel, Field

from asaree.config import get_settings
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
    server_env: dict[str, str] | None = None


class UpdateServerRequest(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    server_env: dict[str, str] | None = None


class AuthenticationStatusResponse(BaseModel):
    auth_mode: str
    configured: bool
    authorization_required: bool
    static_headers_configured: bool
    stdio_env_configured: bool
    stdio_env_names: list[str]


class OAuthStartRequest(BaseModel):
    scope: str | None = Field(default=None, max_length=2000)


class OAuthStartResponse(BaseModel):
    authorization_url: str
    expires_at: datetime
    transaction_id: str


class ClearCredentialsRequest(BaseModel):
    revoke: bool = False


class ServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    command: str | None
    url: str | None
    status: str
    error_message: str | None
    capabilities: dict[str, Any] | None
    authentication: AuthenticationStatusResponse
    credential_management_allowed: bool
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


async def _to_response(
    config: Any, *, viewer_id: uuid.UUID | None = None, refresh_annotations: bool = False
) -> ServerResponse:
    auth = await mcp_service.get_authentication_status(config.id)
    return ServerResponse(
        id=config.id,
        name=config.name,
        transport=config.transport.value,
        command=config.command,
        url=config.url,
        status=config.status.value,
        error_message=config.error_message,
        capabilities=await _capabilities_with_tool_annotations(config, refresh=refresh_annotations),
        authentication=AuthenticationStatusResponse(
            auth_mode=auth.auth_mode,
            configured=auth.configured,
            authorization_required=auth.authorization_required,
            static_headers_configured=auth.static_headers_configured,
            stdio_env_configured=auth.stdio_env_configured,
            stdio_env_names=list(auth.stdio_env_names),
        ),
        credential_management_allowed=viewer_id is not None and config.owner_id == viewer_id,
        created_at=config.created_at,
    )


def _raise_auth_http_error(exc: Exception) -> None:
    if isinstance(exc, MCPServerNotFoundError):
        raise HTTPException(status_code=404, detail="No such server") from exc
    if isinstance(exc, MCPReauthorizationRequiredError):
        raise HTTPException(status_code=409, detail="MCP OAuth authorization is required") from exc
    if isinstance(exc, MCPOAuthDiscoveryError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, (MCPAuthConfigurationError, MCPCredentialValidationError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, MCPAuthenticationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


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
            server_env=body.server_env,
            owner_id=user.id,
        )
    except MCPServerNameConflictError as exc:
        raise HTTPException(status_code=409, detail="A server with this name already exists") from exc
    except (MCPAuthenticationError, MCPCredentialValidationError, MCPServerNotFoundError) as exc:
        _raise_auth_http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _to_response(config, viewer_id=user.id, refresh_annotations=True)


@router.get("", response_model=list[ServerResponse])
async def list_servers_endpoint(user: CurrentUser) -> list[ServerResponse]:
    servers = await mcp_service.list_servers(owner_id=user.id)
    return list(await asyncio.gather(*(_to_response(s, viewer_id=user.id) for s in servers)))


@router.get("/oauth/callback", name="complete_mcp_oauth_callback")
async def complete_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    iss: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Consume the provider callback and return to the frontend popup route.

    This endpoint is intentionally unauthenticated: the random, one-use OAuth
    state is the callback credential. Motoro binds it to the owner and server.
    """
    settings = get_settings()
    params: dict[str, str]
    if error or not code or not state:
        params = {"status": "error", "code": "authorization_rejected"}
    else:
        try:
            config = await mcp_service.complete_oauth_authorization(code=code, state=state, iss=iss)
            params = {"status": "success", "server_id": str(config.id)}
        except MCPOAuthStateError:
            params = {"status": "error", "code": "invalid_state"}
        except MCPOAuthCallbackError:
            params = {"status": "error", "code": "token_exchange_failed"}
        except (MCPAuthenticationError, MCPServerNotFoundError, MCPReauthorizationRequiredError):
            params = {"status": "error", "code": "authorization_failed"}
    target = f"{settings.frontend_url.rstrip('/')}/mcp/oauth/callback?{urlencode(params)}"
    return RedirectResponse(target, status_code=303)


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server_endpoint(server_id: uuid.UUID, user: CurrentUser) -> ServerResponse:
    config = await mcp_service.get_server(server_id)
    if config is None or not _readable(config, user):
        raise HTTPException(status_code=404, detail="No such server")
    return await _to_response(config, viewer_id=user.id)


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
            headers=body.headers if "headers" in body.model_fields_set else UNSET,
            server_env=body.server_env if "server_env" in body.model_fields_set else UNSET,
        )
    except MCPServerNameConflictError as exc:
        raise HTTPException(status_code=409, detail="A server with this name already exists") from exc
    except (MCPAuthenticationError, MCPCredentialValidationError, MCPServerNotFoundError) as exc:
        _raise_auth_http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert config is not None  # existence already checked above
    return await _to_response(config, viewer_id=user.id, refresh_annotations=True)


@router.post("/{server_id}/oauth/start", response_model=OAuthStartResponse)
async def start_oauth_endpoint(
    server_id: uuid.UUID, body: OAuthStartRequest, user: CurrentUser
) -> OAuthStartResponse:
    settings = get_settings()
    try:
        authorization = await mcp_service.begin_oauth_authorization(
            server_id,
            redirect_uri=settings.mcp_oauth_callback_url,
            client_metadata=MCPOAuthClientMetadata(
                client_name="ASAREE",
                scope=body.scope,
                client_uri=settings.frontend_url,
            ),
            client_metadata_url=settings.mcp_oauth_client_metadata_url,
            owner_id=user.id,
        )
    except (MCPAuthenticationError, MCPCredentialValidationError, MCPServerNotFoundError) as exc:
        _raise_auth_http_error(exc)
        raise AssertionError("unreachable") from exc
    return OAuthStartResponse(
        authorization_url=authorization.authorization_url,
        expires_at=authorization.expires_at,
        transaction_id=authorization.transaction_id,
    )


@router.post("/{server_id}/credentials/clear", response_model=ServerResponse)
async def clear_credentials_endpoint(
    server_id: uuid.UUID, body: ClearCredentialsRequest, user: CurrentUser
) -> ServerResponse:
    try:
        if body.revoke:
            await mcp_service.clear_oauth_credentials(server_id, owner_id=user.id, revoke=True)
        config = await mcp_service.clear_server_credentials(server_id, owner_id=user.id)
    except (MCPAuthenticationError, MCPServerNotFoundError) as exc:
        _raise_auth_http_error(exc)
        raise AssertionError("unreachable") from exc
    return await _to_response(config, viewer_id=user.id)


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
    return await _to_response(config, viewer_id=user.id, refresh_annotations=True)


@router.post("/{server_id}/reconnect", response_model=ServerResponse)
async def reconnect_server_endpoint(server_id: uuid.UUID, user: CurrentUser) -> ServerResponse:
    existing = await mcp_service.get_server(server_id)
    if existing is None or existing.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such server")
    config = await mcp_service.reconnect_server(server_id)
    assert config is not None
    return await _to_response(config, viewer_id=user.id, refresh_annotations=True)


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
    except MCPReauthorizationRequiredError as exc:
        raise HTTPException(status_code=409, detail="MCP OAuth authorization is required") from exc
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
