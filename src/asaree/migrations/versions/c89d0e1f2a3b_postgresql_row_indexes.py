"""Dataset-owned derived CSV indexes; filesystem inputs remain authoritative."""

import sqlalchemy as sa
from alembic import op

revision = "c89d0e1f2a3b"
down_revision = "b78c9d0e1f2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_row_indexes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("dataset_id", sa.UUID(), sa.ForeignKey("registered_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("columns_data", sa.LargeBinary(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dataset_id", "raw_sha256", "parser_version", name="uq_dataset_row_index_source"),
        sa.CheckConstraint("row_count >= 0", name="ck_dataset_row_index_count"),
        if_not_exists=True,
    )
    op.create_table(
        "dataset_indexed_rows",
        sa.Column("index_id", sa.UUID(), sa.ForeignKey("dataset_row_indexes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("row_index", sa.BigInteger(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("row_index >= 0", name="ck_dataset_indexed_row_position"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("dataset_indexed_rows", if_exists=True)
    op.drop_table("dataset_row_indexes", if_exists=True)
