"""persist stable row slots and optional attempt row snapshots

Revision ID: 0a1b2c3d4e5f
Revises: b2c7e4d91a60
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0a1b2c3d4e5f"
down_revision: str | None = "b2c7e4d91a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "factorial_row_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("replicate_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protocol_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workspace_id", sa.String(length=255), nullable=True),
        sa.Column("metric_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("artifacts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("row_index >= 0", name="ck_factorial_row_results_row_index_nonnegative"),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["registered_datasets.id"], name="fk_factorial_row_results_dataset", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["protocol_revision_id"], ["protocol_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["replicate_result_id"], ["factorial_replicate_results.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "replicate_result_id",
            "protocol_revision_id",
            "dataset_id",
            "raw_sha256",
            "row_index",
            name="uq_factorial_row_results_row_identity",
        ),
    )
    op.create_index("ix_factorial_row_results_dataset_id", "factorial_row_results", ["dataset_id"])
    op.create_index(
        "ix_factorial_row_results_protocol_revision_id", "factorial_row_results", ["protocol_revision_id"]
    )
    op.create_index(
        "ix_factorial_row_results_replicate_result_id", "factorial_row_results", ["replicate_result_id"]
    )
    op.create_index("ix_factorial_row_results_run_id", "factorial_row_results", ["run_id"])
    op.add_column("protocol_runs", sa.Column("row_result_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("protocol_runs", sa.Column("dataset_row", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_foreign_key(
        "fk_protocol_runs_row_result_id_factorial_row_results",
        "protocol_runs",
        "factorial_row_results",
        ["row_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_protocol_runs_row_result_id", "protocol_runs", ["row_result_id"])


def downgrade() -> None:
    op.drop_index("ix_protocol_runs_row_result_id", table_name="protocol_runs")
    op.drop_constraint("fk_protocol_runs_row_result_id_factorial_row_results", "protocol_runs", type_="foreignkey")
    op.drop_column("protocol_runs", "dataset_row")
    op.drop_column("protocol_runs", "row_result_id")
    op.drop_index("ix_factorial_row_results_run_id", table_name="factorial_row_results")
    op.drop_index("ix_factorial_row_results_replicate_result_id", table_name="factorial_row_results")
    op.drop_index("ix_factorial_row_results_protocol_revision_id", table_name="factorial_row_results")
    op.drop_index("ix_factorial_row_results_dataset_id", table_name="factorial_row_results")
    op.drop_table("factorial_row_results")
