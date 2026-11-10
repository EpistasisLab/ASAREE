"""Import command and durable typed provenance; source evidence stays on disk."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class PackageImport(Base, TimestampMixin):
    __tablename__ = "package_imports"
    __table_args__ = (UniqueConstraint("owner_id", "request_key", name="uq_package_import_request"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="uploading", index=True)
    sha256: Mapped[str | None] = mapped_column(String(64))
    preflight: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_experiments.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer, default=0)
    # Explicit local configuration acknowledgement is separate from source facts.
    configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportedIdentity(Base):
    __tablename__ = "imported_identities"
    import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("package_imports.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    local_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
