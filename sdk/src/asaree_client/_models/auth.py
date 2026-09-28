from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class UserAccount(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    is_admin: bool
    created_at: datetime


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    user: UserAccount


class Message(BaseModel):
    message: str


class ApiToken(BaseModel):
    id: uuid.UUID
    name: str
    token: str
    token_prefix: str | None
    expires_at: datetime | None
    created_at: datetime


class ApiTokenSummary(BaseModel):
    id: uuid.UUID
    name: str
    token_prefix: str | None
    last_used_at: datetime | None
    expires_at: datetime | None
    is_revoked: bool
    created_at: datetime


class ApiTokenPage(BaseModel):
    items: list[ApiTokenSummary]
    total: int
    offset: int
    limit: int


class BootstrapUser(BaseModel):
    id: uuid.UUID
    email: str


class BootstrapApiToken(BaseModel):
    id: uuid.UUID
    name: str
    token: str
