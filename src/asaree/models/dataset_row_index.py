"""Regenerable parsed original rows, scoped to one immutable registration."""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class DatasetRowIndex(Base, TimestampMixin):
    __tablename__ = "dataset_row_indexes"
    __table_args__ = (
        UniqueConstraint("dataset_id", "raw_sha256", "parser_version", name="uq_dataset_row_index_source"),
        CheckConstraint("row_count >= 0", name="ck_dataset_row_index_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registered_datasets.id", ondelete="CASCADE"), nullable=False
    )
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # UTF-8 JSON bytes, deliberately not JSONB/text: CSV strings can contain NUL.
    columns_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DatasetIndexedRow(Base):
    __tablename__ = "dataset_indexed_rows"
    __table_args__ = (
        CheckConstraint("row_index >= 0", name="ck_dataset_indexed_row_position"),
    )

    index_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_row_indexes.id", ondelete="CASCADE"), primary_key=True
    )
    row_index: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
