"""add protocol run wall-clock boundaries

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-09-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "protocol_runs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True), if_not_exists=True
    )
    op.add_column(
        "protocol_runs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True), if_not_exists=True
    )


def downgrade() -> None:
    op.drop_column("protocol_runs", "completed_at", if_exists=True)
    op.drop_column("protocol_runs", "started_at", if_exists=True)
