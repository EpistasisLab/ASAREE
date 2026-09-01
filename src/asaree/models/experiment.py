"""A research experiment — the top-level grouping for a factorial sweep.

The `ResearchExperiment`-equivalent from project_plan/core_asaree_use_case.md
§5, factorial design only (design doc explicitly scopes out ab_experiments,
discoveries, and the rest of ARES's broader experiment types).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asaree.models.base import Base, TimestampMixin, generate_uuid


class ResearchExperiment(Base, TimestampMixin):
    __tablename__ = "research_experiments"
    # Unique per owner, not per installation — see uq_research_experiments_owner_name.
    __table_args__ = (Index("uq_research_experiments_owner_name", "owner_id", "name", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free text, same shape as description -- always-relevant, no structure
    # to validate, so a plain nullable column rather than a design_spec key.
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A plain string, not an enum — same reasoning as UserLLMSetting.provider:
    # this table shouldn't need a migration just because a new design type
    # gets added. Only "factorial" is meaningful today.
    design_type: Mapped[str] = mapped_column(String(32), nullable=False, default="factorial")
    # Kept for provenance — the notebook's task_brief dict, verbatim.
    task_brief: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # {"factors": [{"name": ..., "levels": [...]}, ...]} — what
    # services.design_generation.generate_design_cells reads to materialize
    # FactorialCell parents and their replicate children. A declaration, not a computed design — the
    # actual cross product is never stored here, only regenerated on demand.
    #
    # Also holds these optional keys (all absent = today's exact behavior,
    # additive/backward-compatible, no migration needed for JSONB):
    #   "replicates": int -- copies per factor-level combination (default 1
    #     when absent, see services.design_generation).
    #   "randomization_seed": int | None -- shuffles generated cells'
    #     execution order (not the combinations themselves) when set.
    #   "metrics": [{"name": str, "primary": bool, "direction": "maximize"|
    #     "minimize"}, ...] -- declared up front, unlike the frontend's
    #     purely-inferred availableMetricKeys, so a primary metric/direction
    #     exists before any cell has run (services.factorial_analysis reads
    #     this to default its own primary_metric/reference_condition params).
    #   "coordination_strategy": {"slug": str, "params": dict} -- "sequential"
    #     (default when absent -- today's exact existing DAG-handoff
    #     behavior, unchanged) or "critic_gate" (promotes the existing
    #     gated-pair mechanism to an explicit declaration; the canvas
    #     critic_gate node itself is unchanged) are real; the 6 ARES-derived
    #     coordination slugs (supervisor_architecture, swarm_architecture,
    #     task_bidding, supervision_tree_with_guarded_capabilities,
    #     event_driven_reactivity, multi_agent_planning) are named
    #     placeholders -- selectable and saveable, but
    #     services.protocol_execution rejects a run attempted with one of
    #     these active until the ARES->Motoro pattern migration lands
    #     (see COORDINATION_STRATEGIES).
    design_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # RESTRICT, not CASCADE: deleting a user shouldn't silently discard the
    # experiments they ran. Matches RegisteredDataset.owner_id.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Datasets are NOT a column here. They used to be a single nullable
    # `dataset_id` FK, which encoded "an experiment has exactly one dataset";
    # that stopped being true once an agent's Dataset connector was uncapped,
    # so the relationship moved to the experiment_datasets join table (see
    # models/experiment_dataset.py, migration d5a3b90c71e4). Read/write it
    # through services.experiments' get_experiment_dataset_ids/
    # set_experiment_datasets -- the API still exposes a scalar `dataset_id`
    # on top of that list for the SDK and notebook, but it's a view, not
    # storage.
    #
    # Null = active. A safer alternative to delete_experiment (which cascades
    # every FactorialCell and replicate result under it) -- archiving just hides the
    # experiment from the default list (see list_experiments's
    # include_archived kwarg), reversible by clearing this back to null. A
    # timestamp rather than a bool gives "when" for free, same reasoning as
    # ProtocolRun.last_heartbeat_at.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["ResearchExperiment"]
