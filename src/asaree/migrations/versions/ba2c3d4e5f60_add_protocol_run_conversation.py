"""add persisted agent-to-agent conversation transcript

Revision ID: ba2c3d4e5f60
Revises: b8c9d0e1f2a3
Create Date: 2026-09-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ba2c3d4e5f60"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("protocol_runs", sa.Column("conversation", postgresql.JSONB(), nullable=True), if_not_exists=True)


def downgrade() -> None:
    op.drop_column("protocol_runs", "conversation", if_exists=True)
