"""A protocol run -- one execution of a Protocol's graph.

Mirrors agentic-core's AgentRun/RunStatus pattern (the only lifecycle/
state-machine precedent in this codebase) rather than inventing a new one:
a plain string status ("pending"/"running"/"completed"/"failed" -- not a DB
enum, same reasoning as ResearchExperiment.design_type) plus a heartbeat
column for the same staleness-detection convention AgentRun uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class ProtocolRun(Base, TimestampMixin):
    __tablename__ = "protocol_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    protocol_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocols.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RESTRICT, not CASCADE: same reasoning as every other owner_id in this
    # codebase -- deleting a user shouldn't silently discard their run history.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # {node_id: {"status": "pending"|"running"|"completed"|"failed"|"skipped",
    #            "run_id": "<AgentRun uuid, agent nodes only>",
    #            "output_text": str|None, "error": str|None,
    #            "started_at": iso|None, "completed_at": iso|None}}
    # One opaque JSONB blob, not a normalized NodeRun table -- nothing in
    # ASAREE queries into per-node status structurally, only the polling
    # endpoint and the canvas ever read it, and both read it whole. Same
    # reasoning as Protocol.graph itself.
    node_runs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Protocol-level failure (e.g. a cycle rejected at validation time, or an
    # unhandled executor exception) -- distinct from any one node's own error
    # already recorded inside node_runs.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Both null for a plain graph run. Set together only when this run was
    # created by "run all cells" (services.protocol_execution.plan_cell_runs)
    # for one FactorialCellResult under the protocol's own experiment_id --
    # factor_values is that cell's own factor_values, substituted into the
    # graph's factor_bindings-tagged fields before execution
    # (apply_factor_bindings), and cell_label is where the result gets
    # written back to via upsert_cell.
    cell_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    factor_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Set only for a canvas "Play" run on one Agent node in isolation (the
    # node's own hover-toolbar icon, not the top-level Run button) -- null
    # for both a plain graph run and a "run all cells" run. run_protocol
    # branches on this at the very top: present, run just this one node
    # (which must have no upstream input -- see
    # protocol_execution.validate_single_node_runnable); absent, the
    # existing full topological walk, unchanged.
    target_node_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


__all__ = ["ProtocolRun"]
