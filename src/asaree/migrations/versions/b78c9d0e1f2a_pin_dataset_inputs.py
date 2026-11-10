"""Pin publication and attempt dataset inputs; leave legacy evidence unknown."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
from asaree.migrations.guards import drop_column

revision = "b78c9d0e1f2a"
down_revision = "a67b8c9d0e1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("protocol_revisions", "protocol_runs"):
        op.add_column(table, sa.Column("dataset_inputs", postgresql.JSONB(), nullable=True), if_not_exists=True)
    op.create_table(
        "dataset_input_references",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("publication_id", sa.UUID(), sa.ForeignKey("protocol_revisions.id", ondelete="CASCADE")),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("protocol_runs.id", ondelete="CASCADE")),
        sa.Column("dataset_id", sa.UUID(), sa.ForeignKey("registered_datasets.id", name="fk_pinned_input_dataset"), nullable=False),
        sa.Column("split_id", sa.UUID(), sa.ForeignKey("dataset_splits.id")),
        sa.Column("artifact_id", sa.UUID(), sa.ForeignKey("dataset_artifacts.id")),
        if_not_exists=True,
    )
    for column in ("publication_id", "run_id", "dataset_id", "split_id", "artifact_id"):
        op.create_index(f"ix_dataset_input_references_{column}", "dataset_input_references", [column], if_not_exists=True)


def downgrade() -> None:
    op.drop_table("dataset_input_references", if_exists=True)
    for table in ("protocol_runs", "protocol_revisions"):
        drop_column(table, "dataset_inputs")
