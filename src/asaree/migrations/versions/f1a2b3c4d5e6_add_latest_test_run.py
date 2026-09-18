"""add the experiment-owned latest Test Run

Revision ID: f1a2b3c4d5e6
Revises: d7e8f9a0b1c2
Create Date: 2026-09-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "protocol_runs", sa.Column("is_test_run", sa.Boolean(), nullable=False, server_default=sa.false()), if_not_exists=True
    )
    op.add_column(
        "research_experiments", sa.Column("latest_test_run_id", sa.UUID(), nullable=True), if_not_exists=True
    )
    op.create_foreign_key(
        "fk_research_experiments_latest_test_run",
        "research_experiments",
        "protocol_runs",
        ["latest_test_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column("protocol_runs", "is_test_run", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_research_experiments_latest_test_run", "research_experiments", type_="foreignkey")
    op.drop_column("research_experiments", "latest_test_run_id")
    op.drop_column("protocol_runs", "is_test_run")
