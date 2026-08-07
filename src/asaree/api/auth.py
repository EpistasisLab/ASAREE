"""Browser-session auth: register, login, refresh, logout, forgot/reset
password, profile, and API token management.

Separate from ``api/users.py`` (the SDK/agent bootstrap path: create a user,
then trade its password for an API token once, with no session at all).
Both ultimately create/read the same ``User`` row; this router is what a
browser frontend actually talks to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Cookie, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field

from asaree.config import get_settings
from asaree.deps import CurrentUser, DbSession
from asaree.security.passwords import verify_password
from asaree.services import api_tokens as api_tokens_service
from asaree.services import password_reset as password_reset_service
from asaree.services import users as users_service
from asaree.services.audit import log_action
from asaree.services.auth_service import create_user_tokens, deny_token, validate_user_token
from asaree.services.rate_limit import check_rate_limit, clear_rate_limit, record_attempt

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "asaree_refresh_token"

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300
_REGISTER_MAX_ATTEMPTS = 5
_REGISTER_WINDOW_SECONDS = 3600


# ---------------------------------------------------------------------------
# Schemas (inline, matching api/users.py's own convention — no schemas/ package)
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    is_admin: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class MessageResponse(BaseModel):
    message: str


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class TokenCreateResponse(BaseModel):
    id: uuid.UUID
    name: str
    token: str
    token_prefix: str | None
    expires_at: datetime | None
    created_at: datetime


class TokenListItem(BaseModel):
    id: uuid.UUID
    name: str
    token_prefix: str | None
    last_used_at: datetime | None
    expires_at: datetime | None
    is_revoked: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenListResponse(BaseModel):
    items: list[TokenListItem]
    total: int
    offset: int
    limit: int


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=settings.env != "development",
        samesite="strict",
        path="/api/auth",
        max_age=settings.refresh_token_expiry_seconds,
    )


# ---------------------------------------------------------------------------
# Register / login / refresh / logout
# ---------------------------------------------------------------------------


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(data: RegisterRequest, request: Request, db: DbSession) -> UserResponse:
    """Register a new account. Does not log the caller in — see /login.

    IP-throttled: max 5 registrations per IP per hour.
    """
    client_ip = (request.client.host if request.client else None) or "unknown"
    allowed, retry_after = await check_rate_limit(
        f"reg:{client_ip}", limit=_REGISTER_MAX_ATTEMPTS, window_seconds=_REGISTER_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Too many registration attempts from this network. Please wait {retry_after} seconds.",
                "code": "rate_limited",
                "retry_after_seconds": retry_after,
            },
        )
    await record_attempt(f"reg:{client_ip}", window_seconds=_REGISTER_WINDOW_SECONDS)

    if await users_service.get_user_by_email(db, data.email) is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "An account with this email already exists.", "code": "email_taken"},
        )

    user = await users_service.create_user(
        db, email=data.email, password=data.password, display_name=data.display_name
    )
    # Explicit commit, not left to get_db's own post-request auto-commit:
    # FastAPI defers a yield-dependency's post-yield cleanup (get_db's commit)
    # until after any BackgroundTasks run, not necessarily before the response
    # is sent — and empirically, even without BackgroundTasks, a fast-enough
    # follow-up request (e.g. this response's caller immediately calling
    # /login) can otherwise race ahead of it. Applied consistently to every
    # mutating endpoint in this router — see this comment for the "why"
    # wherever it's repeated below.
    await db.commit()
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest, request: Request, response: Response, db: DbSession, bg: BackgroundTasks
) -> TokenResponse:
    """Authenticate and return a JWT access+refresh pair. Rate-limited per
    email: 5 attempts / 5 minutes, cleared on a successful login."""
    key = data.email.lower().strip()
    allowed, retry_after = await check_rate_limit(key, limit=_LOGIN_MAX_ATTEMPTS, window_seconds=_LOGIN_WINDOW_SECONDS)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Too many login attempts. Please wait {retry_after} seconds.",
                "code": "rate_limited",
                "retry_after_seconds": retry_after,
            },
        )

    user = await users_service.get_user_by_email(db, data.email)
    if user is None or not verify_password(data.password, user.hashed_password):
        await record_attempt(key, window_seconds=_LOGIN_WINDOW_SECONDS)
        raise HTTPException(
            status_code=401,
            detail={"message": "Incorrect email or password. Please try again.", "code": "invalid_credentials"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "This account has been deactivated. Contact an administrator.",
                "code": "account_disabled",
            },
        )

    await clear_rate_limit(key)
    await users_service.record_login(db, user)
    # Explicit commit before scheduling the background task: FastAPI defers a
    # yield-dependency's own post-yield cleanup (get_db's auto-commit) until
    # AFTER background tasks run, not before the response is sent — so
    # without this, a fast-following request (e.g. a client that logs in and
    # immediately calls another endpoint) can race ahead of the commit and
    # see pre-login state. Committing here, before add_task, closes that gap.
    await db.commit()

    access_token, refresh_token = create_user_tokens(user.id, user.email)
    bg.add_task(log_action, action="user.login", resource_type="user", resource_id=user.id, user_id=user.id, request=request)
    _set_refresh_cookie(response, refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=get_settings().access_token_expiry_seconds,
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    asaree_refresh_token: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    """Exchange a valid refresh token (cookie, browser; or Bearer header, the
    SDK) for a new access+refresh pair. The old refresh token is denied —
    one-time use, so a stolen-and-replayed token is detectable."""
    if asaree_refresh_token:
        token = asaree_refresh_token
    elif authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
    else:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    try:
        payload = await validate_user_token(token, expected_type="refresh")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token") from None

    if "jti" in payload and "exp" in payload:
        await deny_token(payload["jti"], datetime.fromtimestamp(payload["exp"], tz=UTC))

    user = await users_service.get_user(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User account is deactivated")

    access_token, new_refresh = create_user_tokens(user.id, user.email)
    _set_refresh_cookie(response, new_refresh)
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_in=get_settings().access_token_expiry_seconds,
        user=UserResponse.model_validate(user),
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    bg: BackgroundTasks,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Deny the current access token and clear the refresh cookie."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization[len("Bearer "):]

    try:
        payload = await validate_user_token(token, expected_type="access")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token") from None

    if "jti" in payload and "exp" in payload:
        await deny_token(payload["jti"], datetime.fromtimestamp(payload["exp"], tz=UTC))

    response.delete_cookie(key=_REFRESH_COOKIE, path="/api/auth")

    user_id = payload.get("sub")
    bg.add_task(
        log_action,
        action="user.logout",
        resource_type="user",
        resource_id=uuid.UUID(user_id) if user_id else None,
        user_id=uuid.UUID(user_id) if user_id else None,
        request=request,
    )


# ---------------------------------------------------------------------------
# Forgot / reset password
# ---------------------------------------------------------------------------


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(data: ForgotPasswordRequest, db: DbSession) -> MessageResponse:
    """Always returns 200 regardless of whether the email exists, to avoid
    leaking which emails are registered. There is no email delivery yet
    (see services.password_reset) — the token is only logged server-side, a
    dev-only stand-in until ASAREE has an email sender."""
    await password_reset_service.request_password_reset(db, email=data.email)
    await db.commit()  # see the note in register() — explicit, not left implicit
    return MessageResponse(message="If an account exists with that email, a reset link has been sent.")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(data: ResetPasswordRequest, db: DbSession) -> MessageResponse:
    ok = await password_reset_service.redeem_password_reset(db, raw_token=data.token, new_password=data.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    await db.commit()  # see the note in register() — a used token must be unredeemable immediately
    return MessageResponse(message="Password has been reset successfully.")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserResponse)
async def get_profile(current_user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_profile(data: UserUpdate, db: DbSession, current_user: CurrentUser) -> UserResponse:
    user, email_taken = await users_service.update_profile(
        db, current_user, display_name=data.display_name, email=data.email
    )
    if email_taken:
        raise HTTPException(status_code=409, detail="Email already registered")
    await db.commit()  # see the note in register() — a follow-up GET /me must see this immediately
    return UserResponse.model_validate(user)


@router.post("/me/password", status_code=204)
async def change_password(data: PasswordChange, db: DbSession, current_user: CurrentUser) -> None:
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    await users_service.set_password(db, current_user, new_password=data.new_password)
    await db.commit()  # see the note in register() — a follow-up login must see the new password


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


@router.post("/me/tokens", response_model=TokenCreateResponse, status_code=201)
async def create_api_token(
    data: TokenCreate, request: Request, db: DbSession, bg: BackgroundTasks, current_user: CurrentUser
) -> TokenCreateResponse:
    """Create a token for the current session. Unlike POST /api/users/{id}/tokens
    (the pre-login bootstrap path), this needs no password — the caller is
    already authenticated. Limit: 20 active tokens per user."""
    active = await api_tokens_service.count_active_tokens(db, user_id=current_user.id)
    if active >= api_tokens_service.MAX_ACTIVE_TOKENS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum of {api_tokens_service.MAX_ACTIVE_TOKENS_PER_USER} active tokens allowed",
        )
    token, raw = await api_tokens_service.issue_api_token(
        db, user_id=current_user.id, name=data.name, expires_in_days=data.expires_in_days
    )
    # See the comment in login() — commit before add_task, not after, so a
    # fast-following request can't race ahead of get_db's deferred auto-commit.
    await db.commit()
    bg.add_task(
        log_action,
        action="api_token.create",
        resource_type="api_token",
        resource_id=token.id,
        user_id=current_user.id,
        details={"name": data.name},
        request=request,
    )
    return TokenCreateResponse(
        id=token.id, name=token.name, token=raw, token_prefix=token.token_prefix,
        expires_at=token.expires_at, created_at=token.created_at,
    )


@router.get("/me/tokens", response_model=TokenListResponse)
async def list_api_tokens(
    db: DbSession,
    current_user: CurrentUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> TokenListResponse:
    tokens, total = await api_tokens_service.list_api_tokens(db, user_id=current_user.id, offset=offset, limit=limit)
    return TokenListResponse(
        items=[TokenListItem.model_validate(t) for t in tokens], total=total, offset=offset, limit=limit
    )


@router.delete("/me/tokens/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: uuid.UUID, request: Request, db: DbSession, bg: BackgroundTasks, current_user: CurrentUser
) -> None:
    """404 whether the token is absent or someone else's — either way, a
    caller learns nothing about a token id that isn't theirs."""
    ok = await api_tokens_service.revoke_api_token(db, user_id=current_user.id, token_id=token_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Token not found")
    # See the comment in login() — commit before add_task, not after.
    await db.commit()
    bg.add_task(
        log_action,
        action="api_token.revoke",
        resource_type="api_token",
        resource_id=token_id,
        user_id=current_user.id,
        request=request,
    )
