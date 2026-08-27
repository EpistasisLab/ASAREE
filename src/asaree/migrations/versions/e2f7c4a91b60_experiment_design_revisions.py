"""cells belong to a design revision, not straight to the experiment

``factorial_cell_results`` hung directly off ``research_experiments``, which
asserted that an experiment has exactly one design forever. It doesn't:
regenerating after a factor change left the previous design's cells in place
with nothing to tell them apart, so they kept counting toward the scored
tally and kept being picked up by "run all cells". Shrinking a design from 6
cells to 2 still showed 0/6 and still ran 6.

``experiment_design_revisions`` gives each generation of a design its own row
(with a snapshot of the ``design_spec`` that produced it), and every cell now
points at one. The current design is the revision with ``superseded_at IS
NULL``, enforced one-per-experiment by a partial unique index.

Backfill: every experiment that has at least one cell gets revision 1,
carrying its current ``design_spec``, and all of its existing cells are
attached to it. That is the honest reading of the old data -- one
undifferentiated pile of cells is exactly one design as far as anything
recorded goes. Experiments with no cells get no revision; one is created
lazily the first time a cell is written.

The unique constraint moves from ``(experiment_id, cell_label)`` to
``(design_revision_id, cell_label)``. It has to: the same label legitimately
recurs once per design that generated it, which is what lets a superseded
design keep its results while the current one holds its own row for the same
combination.

The downgrade collapses back to one design per experiment, keeping only the
current revision's cells. Lossy by nature -- that's the honest inverse of
flattening a history into a single pile, and the alternative (keeping all of
them) would violate the unique constraint it has to restore.

Revision ID: e2f7c4a91b60
Revises: d5a3b90c71e4
Create Date: 2026-08-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from asaree.migrations.guards import create_foreign_key, drop_column

revision: str = 'e2f7c4a91b60'
down_revision: str | None = 'd5a3b90c71e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_design_revisions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("design_spec", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["research_experiments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "revision", name="uq_experiment_design_revisions_experiment_revision"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_experiment_design_revisions_experiment_id",
        "experiment_design_revisions",
        ["experiment_id"],
        if_not_exists=True,
    )
    op.create_index(
        "uq_experiment_design_revisions_one_current",
        "experiment_design_revisions",
        ["experiment_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        if_not_exists=True,
    )

    # One revision 1 per experiment that actually has cells. Experiments with
    # none are left alone -- a revision is created lazily on first write, so
    # inventing an empty one here would only add rows nothing points at.
    op.execute(
        """
        INSERT INTO experiment_design_revisions (id, experiment_id, revision, design_spec, superseded_at)
        SELECT gen_random_uuid(), e.id, 1, e.design_spec, NULL
        FROM research_experiments AS e
        WHERE EXISTS (SELECT 1 FROM factorial_cell_results AS c WHERE c.experiment_id = e.id)
        ON CONFLICT DO NOTHING
        """
    )

    op.add_column(
        "factorial_cell_results",
        sa.Column("design_revision_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        if_not_exists=True,
    )
    op.execute(
        """
        UPDATE factorial_cell_results AS c
        SET design_revision_id = r.id
        FROM experiment_design_revisions AS r
        WHERE r.experiment_id = c.experiment_id AND r.revision = 1 AND c.design_revision_id IS NULL
        """
    )
    # Only safe because the backfill above covers every experiment that has a
    # cell -- any row still null here would mean a cell pointing at an
    # experiment that doesn't exist, which the existing FK already rules out.
    op.alter_column("factorial_cell_results", "design_revision_id", nullable=False)
    create_foreign_key(
        "factorial_cell_results_design_revision_id_fkey",
        "factorial_cell_results",
        "experiment_design_revisions",
        ["design_revision_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_factorial_cell_results_design_revision_id",
        "factorial_cell_results",
        ["design_revision_id"],
        if_not_exists=True,
    )

    op.drop_constraint(
        "uq_factorial_cell_results_experiment_cell",
        "factorial_cell_results",
        type_="unique",
        if_exists=True,
    )
    op.create_unique_constraint(
        "uq_factorial_cell_results_revision_cell",
        "factorial_cell_results",
        ["design_revision_id", "cell_label"],
    )

    # A cell run remembers the design it was planned under, so a result that
    # lands after the user has regenerated still writes back to its own
    # design's cell rather than to whatever is current by then. Nullable: a
    # plain (non-cell) graph run has no design revision, same as cell_label.
    # SET NULL, not CASCADE -- deleting a design's results shouldn't erase the
    # record that the run happened.
    op.add_column(
        "protocol_runs",
        sa.Column("design_revision_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        if_not_exists=True,
    )
    create_foreign_key(
        "protocol_runs_design_revision_id_fkey",
        "protocol_runs",
        "experiment_design_revisions",
        ["design_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_protocol_runs_design_revision_id", "protocol_runs", ["design_revision_id"], if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index("ix_protocol_runs_design_revision_id", table_name="protocol_runs", if_exists=True)
    drop_column("protocol_runs", "design_revision_id")

    # Lossy on purpose: one design per experiment can only keep one design's
    # cells, and keeping more would break the unique constraint restored below.
    op.execute(
        """
        DELETE FROM factorial_cell_results AS c
        USING experiment_design_revisions AS r
        WHERE c.design_revision_id = r.id AND r.superseded_at IS NOT NULL
        """
    )
    op.drop_constraint(
        "uq_factorial_cell_results_revision_cell",
        "factorial_cell_results",
        type_="unique",
        if_exists=True,
    )
    op.drop_index(
        "ix_factorial_cell_results_design_revision_id", table_name="factorial_cell_results", if_exists=True
    )
    drop_column("factorial_cell_results", "design_revision_id")
    op.create_unique_constraint(
        "uq_factorial_cell_results_experiment_cell",
        "factorial_cell_results",
        ["experiment_id", "cell_label"],
    )

    op.drop_index(
        "uq_experiment_design_revisions_one_current", table_name="experiment_design_revisions", if_exists=True
    )
    op.drop_index(
        "ix_experiment_design_revisions_experiment_id", table_name="experiment_design_revisions", if_exists=True
    )
    op.drop_table("experiment_design_revisions", if_exists=True)
