"""an experiment's datasets become a many-to-many

``research_experiments.dataset_id`` encoded "an experiment has exactly one
dataset". That stopped being true once an agent's Dataset connector was
uncapped (several Dataset nodes can wire into one agent, the same shape Skill
and Knowledge already had), so the column is replaced by an
``experiment_datasets`` join table carrying the canvas's own wiring order.

Every existing non-null ``dataset_id`` is backfilled as that experiment's
single dataset at position 0, so nothing is lost and no experiment changes
meaning; the column is only dropped after the copy. The downgrade re-adds the
column and takes each experiment's position-0 dataset back into it -- lossy by
nature (an experiment with several datasets keeps only the first), which is
the honest inverse of collapsing a list into a scalar.

Both FKs CASCADE, unlike the SET NULL the old column used: that was protecting
the experiment row from a dataset delete, whereas a link row has nothing left
to mean once either end is gone.

Revision ID: d5a3b90c71e4
Revises: c4e8f1a70d92
Create Date: 2026-08-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

from asaree.migrations.guards import create_foreign_key, drop_column

revision: str = 'd5a3b90c71e4'
down_revision: str | None = 'c4e8f1a70d92'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_datasets",
        sa.Column("experiment_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["research_experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["registered_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("experiment_id", "dataset_id"),
        if_not_exists=True,
    )
    op.create_index("ix_experiment_datasets_dataset_id", "experiment_datasets", ["dataset_id"], if_not_exists=True)

    op.execute(
        """
        INSERT INTO experiment_datasets (experiment_id, dataset_id, position)
        SELECT id, dataset_id, 0 FROM research_experiments WHERE dataset_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    drop_column("research_experiments", "dataset_id")


def downgrade() -> None:
    op.add_column(
        "research_experiments",
        sa.Column("dataset_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        if_not_exists=True,
    )
    create_foreign_key(
        "research_experiments_dataset_id_fkey",
        "research_experiments",
        "registered_datasets",
        ["dataset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_research_experiments_dataset_id", "research_experiments", ["dataset_id"], if_not_exists=True)
    # Lossy on purpose: only the first-wired dataset survives a collapse back
    # to a scalar column.
    op.execute(
        """
        UPDATE research_experiments AS e
        SET dataset_id = d.dataset_id
        FROM (
            SELECT DISTINCT ON (experiment_id) experiment_id, dataset_id
            FROM experiment_datasets
            ORDER BY experiment_id, position
        ) AS d
        WHERE d.experiment_id = e.id
        """
    )
    op.drop_index("ix_experiment_datasets_dataset_id", table_name="experiment_datasets", if_exists=True)
    op.drop_table("experiment_datasets", if_exists=True)
