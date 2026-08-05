"""User creation and API token issuance.

Deliberately open for now: no admin/invite gate exists yet, so anyone who can
reach this API can create a user. That's a real, known gap — acceptable for
the current vetted-researcher use case, not for a public deployment. Revisit
before ASAREE has users you didn't personally provision.

Token issuance asks for the password rather than an existing API key,
specifically to avoid the bootstrap chicken-and-egg problem: a freshly created
user has no token yet to authenticate a request for one.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.database import get_db
from asaree.security.passwords import verify_password
from asaree.services.api_tokens import issue_api_token
from asaree.services.users import create_user, get_user, get_user_by_email

router = APIRouter(prefix="/users", tags=["users"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str


class IssueTokenRequest(BaseModel):
    password: str
    name: str = "default"


class IssueTokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    token: str


@router.post("", response_model=UserResponse, status_code=201)
async def create_user_endpoint(body: CreateUserRequest, db: DbSession) -> UserResponse:
    if await get_user_by_email(db, body.email) is not None:
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    user = await create_user(db, email=body.email, password=body.password)
    return UserResponse(id=user.id, email=user.email)


@router.post("/{user_id}/tokens", response_model=IssueTokenResponse, status_code=201)
async def issue_token_endpoint(user_id: uuid.UUID, body: IssueTokenRequest, db: DbSession) -> IssueTokenResponse:
    user = await get_user(db, user_id)
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid user id or password")
    token, raw = await issue_api_token(db, user_id=user.id, name=body.name)
    return IssueTokenResponse(id=token.id, name=token.name, token=raw)
