"""Per-user LLM provider credentials — the thing agentic-core's credentials
module explicitly leaves to the product: "core has no users, and with a
separate product database it could not have a foreign key to one." ASAREE
does have a real users table, so this one's FK is enforced.

Exactly one row per (user, provider) — a second PUT for the same provider
replaces it (see the service layer), not accumulates alongside it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class UserLLMSetting(Base, TimestampMixin):
    __tablename__ = "user_llm_settings"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_llm_settings_user_provider"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A string, not agentic_core.schemas.agent.LLMProvider directly — this
    # table shouldn't need a migration if core adds a provider core doesn't
    # know about yet is exactly the failure mode a shared enum would risk.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_base: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["UserLLMSetting"]
