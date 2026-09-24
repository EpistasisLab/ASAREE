"""A protocol run -- one execution of a Protocol's graph.

Mirrors Motoro's AgentRun/RunStatus pattern (the only lifecycle/
state-machine precedent in this codebase) rather than inventing a new one:
a plain string status ("pending"/"running"/"finalizing"/"completed"/"failed" -- not a DB
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
    # A canvas Test Run is retained only through its experiment's
    # latest_test_run_id. Production attempts and node previews stay false.
    is_test_run: Mapped[bool] = mapped_column(default=False, nullable=False)
    # {node_id: {"status": "pending"|"running"|"completed"|"failed"|"skipped",
    #            "run_id": "<AgentRun uuid, agent nodes only>",
    #            "output_text": str|None, "error": str|None,
    #            "started_at": iso|None, "completed_at": iso|None}}
    # One opaque JSONB blob, not a normalized NodeRun table -- nothing in
    # ASAREE queries into per-node status structurally, only the polling
    # endpoint and the canvas ever read it, and both read it whole. Same
    # reasoning as Protocol.graph itself.
    node_runs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Immutable result facts produced by THIS execution attempt, rather than
    # the mutable latest-attempt projection on FactorialReplicateResult. This
    # makes a re-run safe: its replacement result can become current without
    # erasing the scores/evaluation state a user may inspect on an older run.
    # Shape: {"metric_values": {...}, "metric_evaluation": {...},
    #         "measurement": {"observations": [...], "artifacts": [...]},
    #         "evaluation_state": "running"|"completed",
    #         "evaluation_claimed_at": iso,
    #         "task_completed_at": iso, "evaluation_started_at": iso,
    #         "evaluation_completed_at": iso}.
    attempt_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # The agent-to-agent transcript, present only once a run's agents actually
    # communicate -- including a pipeline Agent delegating to a Sub-Agent.
    # Shape: {"state": <A2A TaskState>, "messages": [{"message_id", "sequence",
    #         "from_agent_id", "to_agent_id", "parts": [...], "created_at"}, ...]}
    # Kept alongside node_runs and for the same reason: it is an append-only,
    # run-scoped document that only the polling endpoint and the canvas read,
    # and both read it whole. A messages table would add joins without
    # supporting a query the product needs. `sequence` is stored rather than
    # inferred from list position so an entry keeps its identity if the
    # document is ever paged or filtered.
    conversation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Protocol-level failure (e.g. a cycle rejected at validation time, or an
    # unhandled executor exception) -- distinct from any one node's own error
    # already recorded inside node_runs.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Attempt lifecycle boundaries. Unlike created_at/updated_at these exclude
    # time spent pending in the worker queue and are never repurposed as a
    # heartbeat. The task/evaluation split is recorded in attempt_result;
    # completed_at is the terminal lifecycle transition for compatibility.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Both null for a plain graph run. Set together only when this run was
    # created by "run all cells" (services.protocol_execution.plan_cell_runs)
    # for one FactorialReplicateResult under the protocol's experiment --
    # factor_values is that cell's own factor_values, substituted into the
    # graph's factor_bindings-tagged fields before execution
    # (apply_factor_bindings), and replicate_label is where the result gets
    # written back.
    replicate_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    factor_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    replicate_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factorial_replicate_results.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Which design revision this run's cell belonged to when it was planned
    # (null for a plain graph run, same as replicate_label). Without it, a result
    # arriving after the user regenerated the design would be written against
    # whatever design is current *then* -- landing on a different design's
    # cell, or minting a spurious one if the combination no longer exists.
    # SET NULL rather than CASCADE: deleting a design's results shouldn't
    # erase the record that the run happened.
    design_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiment_design_revisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The immutable published canvas revision this execution uses.  Null is
    # retained only for runs created before protocol revisions existed.
    protocol_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protocol_revisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Set only for a canvas "Play" run on one Agent node in isolation (the
    # node's own hover-toolbar icon, not the top-level Run button) -- null
    # for both a plain graph run and a "run all cells" run. run_protocol
    # branches on this at the very top: present, run just this one node
    # (which must have no upstream input -- see
    # protocol_execution.validate_single_node_runnable); absent, the
    # existing full topological walk, unchanged.
    target_node_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Set by services.protocol_runs.request_protocol_run_cancellation (the
    # cancel endpoint), from a request outside the executor's own task/
    # process. Pending runs become cancelled immediately because no executor
    # exists to observe a flag while they wait in the queue. Running work polls
    # this flag and safely stops itself, retaining completed work.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["ProtocolRun"]
