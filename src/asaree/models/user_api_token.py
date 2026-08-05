"""API tokens — how the SDK/driver scripts actually authenticate.

Only the hash is stored, matching the raw token's owner having the one and
only chance to see it, at creation time — the same guarantee GitHub PATs and
similar give. A lost token means issuing a new one, not recovering the old.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class UserApiToken(Base, TimestampMixin):
    __tablename__ = "user_api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # SHA-256 hex digest of the raw token — fixed length, no salt needed since
    # the input is already a high-entropy random token, not a user-chosen
    # secret vulnerable to a dictionary attack.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["UserApiToken"]
