"""Forgot/reset-password flow: issue a time-limited token, redeem it once."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.password_reset_token import PasswordResetToken
from asaree.models.user import User
from asaree.security.password_reset import generate_reset_token
from asaree.services.users import get_user_by_email, set_password

RESET_TOKEN_EXPIRY_HOURS = 1
MAX_RESET_REQUESTS_PER_HOUR = 3


async def request_password_reset(db: AsyncSession, *, email: str) -> None:
    """Issue a reset token for *email*, if it belongs to an account and that
    account hasn't hit the hourly request cap. Always succeeds regardless —
    the caller (the API layer) reports success either way, so this never
    reveals whether the email exists."""
    user = await get_user_by_email(db, email)
    if user is None:
        return

    recent = (
        await db.execute(
            select(func.count())
            .select_from(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.created_at >= datetime.now(UTC) - timedelta(hours=1),
            )
        )
    ).scalar_one()
    if recent >= MAX_RESET_REQUESTS_PER_HOUR:
        return

    _raw, token_hash = generate_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(hours=RESET_TOKEN_EXPIRY_HOURS),
        )
    )
    await db.flush()
    # The raw token would be emailed here — ASAREE has no email delivery yet
    # (see api/auth.py's forgot_password docstring); logging it is a deliberate
    # dev-only stand-in, not a design decision to keep. WARNING, not INFO: the
    # root logger defaults to WARNING, and a stand-in nobody can see is useless.
    logging.getLogger(__name__).warning("password_reset_token_issued email=%s token=%s", user.email, _raw)


async def redeem_password_reset(db: AsyncSession, *, raw_token: str, new_password: str) -> bool:
    """Validate and consume a reset token, setting the new password. Returns
    False (no user lookup performed, nothing changed) if the token is
    missing, already used, or expired."""
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    result = await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))
    reset_token = result.scalar_one_or_none()
    if reset_token is None or reset_token.is_used or reset_token.expires_at < datetime.now(UTC):
        return False

    user = (await db.execute(select(User).where(User.id == reset_token.user_id))).scalar_one_or_none()
    if user is None:
        return False

    await set_password(db, user, new_password=new_password)
    reset_token.is_used = True
    reset_token.used_at = datetime.now(UTC)
    await db.flush()
    return True
