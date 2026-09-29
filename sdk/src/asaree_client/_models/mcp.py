from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class MCPAuthenticationStatus(BaseModel):
    """Non-secret credential metadata; stored secret values are never returned."""

    auth_mode: Literal["none", "static_headers", "stdio_env", "oauth"]
    configured: bool
    authorization_required: bool
    static_headers_configured: bool
    stdio_env_configured: bool
    stdio_env_names: list[str]


class MCPOAuthAuthorization(BaseModel):
    """Open ``authorization_url`` in a browser; the server consumes the callback."""

    authorization_url: str
    expires_at: datetime
    transaction_id: str


class MCPServer(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    command: str | None
    url: str | None
    status: str
    error_message: str | None
    capabilities: dict[str, Any] | None
    authentication: MCPAuthenticationStatus
    credential_management_allowed: bool
    created_at: datetime


class ToolCallResult(BaseModel):
    is_error: bool
    content: str
