"""add cancel_requested_at to protocol_runs

Revision ID: d4e5f6a7b8c9
Revises: c4d5e6f7a8b9
Create Date: 2026-08-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from asaree.migrations.guards import drop_column

revision: str = 'd4e5f6a7b8c9'
down_revision: str | None = 'c4d5e6f7a8b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('protocol_runs', sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True), if_not_exists=True)


def downgrade() -> None:
    drop_column('protocol_runs', 'cancel_requested_at')
