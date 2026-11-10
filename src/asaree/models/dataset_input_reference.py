"""Retention edges for immutable publication and attempt input manifests."""

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, generate_uuid


class DatasetInputReference(Base):
    __tablename__ = "dataset_input_references"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    publication_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("protocol_revisions.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protocol_runs.id", ondelete="CASCADE"), index=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("registered_datasets.id", name="fk_pinned_input_dataset"), index=True
    )
    split_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dataset_splits.id"), index=True)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dataset_artifacts.id"), index=True)
