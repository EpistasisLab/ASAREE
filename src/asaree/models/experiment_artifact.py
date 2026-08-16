"""An experiment-level (not per-cell) durable artifact.

``POST /experiments/{id}/analyze`` (``services.factorial_analysis``) already
computes real cross-cell statistics, but only ever returns them inline —
nothing persists the result. The asaree-spinal-use-case notebook's own
post-sweep step similarly never lands anywhere durable: it flattens every
cell into a plain CSV and writes it to local disk, with a comment calling
that file "the deliverable." This table is the missing landing spot for
either (an ``analyze`` snapshot, a CSV export, or anything else a use case
wants to keep) -- opaque to ASAREE, exactly like ``dictionary_json`` already
is, since a CSV export and a JSON-encoded ``analyze`` result serialize
completely differently and neither is something ASAREE itself interprets.

No ``owner_id`` column, unlike ``RegisteredDataset`` -- access is mediated
entirely through ``experiment_id -> research_experiments.owner_id`` (the
same shape ``FactorialCellResult`` already uses, which also has no
``owner_id`` of its own). No unique constraint on ``(experiment_id, name)``
either: an artifact is create-once/append-style, never an upsert target the
way a cell is -- re-running ``analyze`` after adding more cells should
produce a NEW row, not silently overwrite the last one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class ExperimentArtifact(Base, TimestampMixin):
    __tablename__ = "experiment_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Freeform, e.g. "analyze_result"/"csv_export" -- not a closed enum, the
    # same reasoning as every other "what kind of opaque thing is this"
    # field in this codebase (McpToolNodeConfig.tool_names, output_contract
    # field types, ...).
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = ["ExperimentArtifact"]
