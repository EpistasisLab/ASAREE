"""Immutable dataset partitions and the managed files that identify their bytes."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asaree.models.base import Base, generate_uuid


class DatasetArtifact(Base):
    __tablename__ = "dataset_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registered_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    namespace: Mapped[str] = mapped_column(String(32), nullable=False, default="datasets")
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    @property
    def path(self) -> str:
        from asaree.config import get_settings

        if self.namespace != "datasets":
            raise ValueError(f"Unknown dataset artifact namespace: {self.namespace}")
        root = Path(get_settings().dataset_storage_dir).resolve()
        path = (root / self.storage_key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Dataset artifact key escapes managed storage")
        return str(path)


class DatasetSplit(Base):
    __tablename__ = "dataset_splits"
    __table_args__ = (UniqueConstraint("id", "dataset_id", name="uq_dataset_splits_id_dataset"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registered_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    train_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_artifacts.id")
    )
    test_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_artifacts.id")
    )
    train_artifact: Mapped[DatasetArtifact | None] = relationship(foreign_keys=[train_artifact_id], lazy="joined")
    test_artifact: Mapped[DatasetArtifact | None] = relationship(foreign_keys=[test_artifact_id], lazy="joined")
    target_column: Mapped[str | None] = mapped_column(String(255))
    method: Mapped[str | None] = mapped_column(String(16))
    strategy: Mapped[str | None] = mapped_column(String(32))
    group_column: Mapped[str | None] = mapped_column(String(255))
    stratify_column: Mapped[str | None] = mapped_column(String(255))
    test_size: Mapped[float | None] = mapped_column(Float)
    seed: Mapped[int | None] = mapped_column(Integer)
    # For legacy records this is the time of preservation, not an invented split date.
    legacy: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
