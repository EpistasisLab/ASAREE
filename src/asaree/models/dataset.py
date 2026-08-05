"""A registered dataset — paths and hashes, never file content.

Mirrors ARES's ``RegisteredDataset`` shape, with one deliberate addition:
``file_sha256``/``train_sha256``/``test_sha256``. ARES's original stored paths
only, with no content hash anywhere on this table — this one carries hashes
from the start, matching the standard this design doc holds the *workspace*
lineage table to (see ``dataset_workspace_event.py``). A dataset row without a
hash would be the one place in the chain that couldn't prove what it actually
holds.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class RegisteredDataset(Base, TimestampMixin):
    __tablename__ = "registered_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    train_path: Mapped[str] = mapped_column(Text, nullable=False)
    test_path: Mapped[str] = mapped_column(Text, nullable=False)
    train_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    test_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    target_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Opaque attribution — same pattern as agentic-core's Agent.owner_id, but
    # ASAREE has a real users table, so this one is an enforced FK.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


__all__ = ["RegisteredDataset"]
