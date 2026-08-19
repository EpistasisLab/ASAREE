"""ASAREE's declarative base — its own metadata, its own migration chain.

Deliberately not Motoro's ``Base``. Two databases, two schemas, two
Alembic chains — nothing here is ever created in core's database, and core's
tables never appear in ASAREE's ``Base.metadata``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def generate_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    """Declarative base for every ASAREE ORM model."""


class TimestampMixin:
    """``created_at``/``updated_at``, server-side, on every ASAREE table."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


__all__ = ["Base", "TimestampMixin", "generate_uuid"]
