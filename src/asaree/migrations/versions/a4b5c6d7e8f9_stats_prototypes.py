"""Store isolated statistics prototype snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4b5c6d7e8f9"
down_revision = "93a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This schema may already exist from before the duplicate revision ID was
    # corrected. Preserve its snapshots while advancing the migration stamp.
    op.create_table(
        "stats_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("backend", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["research_experiments.id"], ondelete="CASCADE"),
        if_not_exists=True,
    )
    op.create_index("ix_stats_analyses_experiment_id", "stats_analyses", ["experiment_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_table("stats_analyses", if_exists=True)
