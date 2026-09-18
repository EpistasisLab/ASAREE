"""add experiment measurement plan snapshots

Revision ID: c6d7e8f9a0b1
Revises: ba2c3d4e5f60
Create Date: 2026-09-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6d7e8f9a0b1"
down_revision: str | None = "ba2c3d4e5f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_experiments",
        sa.Column("measurement_plan", postgresql.JSONB(), nullable=True),
        if_not_exists=True,
    )
    op.add_column(
        "research_experiments",
        sa.Column("locked_measurement_plan", postgresql.JSONB(), nullable=True),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_column("research_experiments", "locked_measurement_plan", if_exists=True)
    op.drop_column("research_experiments", "measurement_plan", if_exists=True)
