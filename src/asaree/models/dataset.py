"""One immutable source registration and its optional current split selection."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asaree.models.base import Base, TimestampMixin, generate_uuid
from asaree.models.dataset_split import DatasetArtifact, DatasetSplit


class RegisteredDataset(Base, TimestampMixin):
    __tablename__ = "registered_datasets"
    __table_args__ = (
        Index("uq_registered_datasets_owner_name", "owner_id", "name", unique=True),
        ForeignKeyConstraint(
            ["current_split_id", "id"], ["dataset_splits.id", "dataset_splits.dataset_id"],
            name="fk_dataset_current_split", use_alter=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Kept for pre-artifact registrations; new registrations persist a relative artifact key.
    _legacy_raw_path: Mapped[str | None] = mapped_column("raw_path", Text)
    raw_sha256: Mapped[str | None] = mapped_column(String(64))
    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_artifacts.id", use_alter=True, name="fk_dataset_raw_artifact")
    )
    raw_artifact: Mapped[DatasetArtifact | None] = relationship(foreign_keys=[raw_artifact_id], lazy="joined")
    current_split_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    current_split: Mapped[DatasetSplit | None] = relationship(
        primaryjoin="RegisteredDataset.current_split_id == DatasetSplit.id",
        foreign_keys=[current_split_id], lazy="joined", post_update=True,
    )
    row_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    _target_column: Mapped[str | None] = mapped_column("target_column", String(255))
    description: Mapped[str | None] = mapped_column(Text)
    dictionary_json: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    @property
    def raw_path(self) -> str | None:
        return self.raw_artifact.path if self.raw_artifact else self._legacy_raw_path

    @raw_path.setter
    def raw_path(self, value: str | None) -> None:
        self.raw_artifact = None
        self._legacy_raw_path = value

    @property
    def target_column(self) -> str | None:
        if self.current_split and not self.current_split.legacy:
            return self.current_split.target_column
        return self._target_column

    @target_column.setter
    def target_column(self, value: str | None) -> None:
        self._target_column = value

    @property
    def train_path(self) -> str | None:
        artifact = self.current_split.train_artifact if self.current_split else None
        return artifact.path if artifact else None

    @property
    def test_path(self) -> str | None:
        artifact = self.current_split.test_artifact if self.current_split else None
        return artifact.path if artifact else None

    @property
    def train_sha256(self) -> str | None:
        artifact = self.current_split.train_artifact if self.current_split else None
        return artifact.sha256 if artifact else None

    @property
    def test_sha256(self) -> str | None:
        artifact = self.current_split.test_artifact if self.current_split else None
        return artifact.sha256 if artifact else None

    @property
    def split_method(self) -> str | None:
        return self.current_split.method if self.current_split else None

    @property
    def split_group_column(self) -> str | None:
        return self.current_split.group_column if self.current_split else None

    @property
    def split_test_size(self) -> float | None:
        return self.current_split.test_size if self.current_split else None

    @property
    def split_seed(self) -> int | None:
        return self.current_split.seed if self.current_split else None


__all__ = ["RegisteredDataset"]
