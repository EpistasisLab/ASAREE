"""A registered dataset — paths and hashes, never file content.

Mirrors ARES's ``RegisteredDataset`` shape, with two deliberate additions.
First, content hashes from the start (``raw_sha256``/``train_sha256``/
``test_sha256``) — ARES's original stored paths only, with no hash anywhere
on this table; a dataset row without one would be the one place in the chain
that couldn't prove what it actually holds. Second, and more load-bearing:
registration stores ONLY the raw uploaded file, immutably — it no longer
splits inline. A split (``train_path``/``test_path``/their hashes) is a
separate, later, optional action (``services.datasets.quick_split_dataset``/
``register_manual_split``), so ``train_path``/``test_path`` are nullable: a
freshly-registered dataset has a raw file and no split yet, same as an
experiment created before the ``dataset_id`` FK existed permanently has
``dataset_id: null`` (see CLAUDE.md's own Experiment data model section) —
not a bug to backfill, just a real, valid state. This split-off-registration
design is deliberate, not an oversight: scientific splitting needs vary
per experiment (stratified holdout, group-aware holdout, k-fold, time-based,
a custom cohort rule...), and baking exactly one strategy into registration,
irreversibly discarding the source, made every OTHER strategy unreachable
without re-uploading from scratch. ASAREE's own job is durable, hash-verified
storage of the raw file and of whichever split a user produces against it —
not being the one true implementation of every split a science project might
need. ``quick_split_dataset`` covers the common cases (stratified/group-aware
holdout) as a convenience; ``register_manual_split`` accepts an already-split
train/test pair computed however the user needs, mirroring the same
"bring your own code" precedent the Script node already established for
scoring.
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
    # The original uploaded file, verbatim -- never modified, never
    # re-derived. The one thing registration itself is responsible for.
    # Nullable purely for a dataset registered before this column existed
    # (this concept didn't exist yet, so there's nothing to backfill it
    # from -- same "permanent, valid null" reasoning CLAUDE.md's own
    # Experiment data model section gives for a pre-migration
    # dataset_id) -- every dataset registered from here on always has one.
    raw_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Null until a split is actually produced (quick or manual) -- see this
    # module's own docstring for why splitting isn't part of registration.
    train_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    train_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    test_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Opaque, matching ARES's own dictionary_json contract exactly: a JSON-encoded
    # string ASAREE never parses or queries into — per-column descriptions consumed
    # entirely downstream by a domain MCP server (e.g. ares-sklearn-eda's
    # get_data_dictionary), not structured data ASAREE's own code interprets.
    dictionary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Opaque attribution — same pattern as Motoro's Agent.owner_id, but
    # ASAREE has a real users table, so this one is an enforced FK.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


__all__ = ["RegisteredDataset"]
