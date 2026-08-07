"""FastAPI dependencies shared across routers."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.database import get_db
from asaree.models.user import User
from asaree.services.api_tokens import authenticate_api_key
from asaree.services.auth_service import validate_user_token
from asaree.services.users import get_user

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the caller from either an ``X-API-Key`` header (the SDK/agents)
    or an ``Authorization: Bearer <access token>`` header (the browser
    frontend's session) — both resolve to the same ``User``, so every route
    using ``CurrentUser`` works for both callers without knowing which one
    it's talking to. ``X-API-Key`` takes precedence when both are somehow
    present, matching the SDK's existing behavior exactly.
    """
    if x_api_key:
        user = await authenticate_api_key(db, x_api_key)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        return user

    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
        try:
            payload = await validate_user_token(token, expected_type="access")
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid or expired session token") from None
        user = await get_user(db, uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid or expired session token")
        return user

    raise HTTPException(status_code=401, detail="Missing X-API-Key or Authorization header")


CurrentUser = Annotated[User, Depends(get_current_user)]
