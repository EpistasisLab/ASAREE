"""The user model. Exists in ASAREE's own database — core has no user table."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # Bcrypt output is a fixed-length ASCII string; 255 leaves headroom
    # without inviting arbitrary-length input.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


__all__ = ["User"]
