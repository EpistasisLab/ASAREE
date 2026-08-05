"""The fix from project_plan/core_asaree_use_case.md §9: a DB-tracked record of
what happened to a dataset's derived workspace versions.

Append-only by design — this is a lineage log, not a mirror of current state.
"What's the currently accepted version of stage X" is a derived query (the
latest ``accepted`` event for that ``workspace_id``/``stage``), not something
this table stores redundantly. The MCP tools that actually write the parquet
files (``workspace.py``/``staging.py``, unchanged) report each commit/accept
here immediately afterward — this table never writes bytes itself, only
records that a write happened and what its hash was.

``dataset_id`` is a real FK, not an opaque UUID (contrast with agentic-core's
``owner_id`` pattern) — ASAREE owns both tables, so nothing stops it from
being enforced, and enforcing it is the entire point: this is what makes a
dataset delete actually cascade, which is the gap that motivated this table.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class WorkspaceEventType(enum.StrEnum):
    COMMITTED = "committed"
    ACCEPTED = "accepted"
    DISCARDED = "discarded"


class DatasetWorkspaceEvent(Base, TimestampMixin):
    __tablename__ = "dataset_workspace_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registered_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "{experiment_id}/{cell_label}" by the existing MCP-side convention —
    # opaque to ASAREE, which has no experiment model yet (see design doc §5).
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[WorkspaceEventType] = mapped_column(
        Enum(
            WorkspaceEventType,
            name="workspace_event_type",
            create_constraint=True,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # Present for `committed`, absent for `accepted`/`discarded` — those refer
    # back to whichever commit they act on rather than carrying their own.
    sha256_train: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256_test: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = ["DatasetWorkspaceEvent", "WorkspaceEventType"]
