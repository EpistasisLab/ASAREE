"""FastAPI dependencies shared across routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.database import get_db
from asaree.models.user import User
from asaree.services.api_tokens import authenticate_api_key


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the caller from ``X-API-Key`` — the header the SDK already sends."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    user = await authenticate_api_key(db, x_api_key)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
