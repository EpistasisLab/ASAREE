"""add target_node_id to protocol_runs

Revision ID: 086b8b3d635b
Revises: 308e54c0539f
Create Date: 2026-08-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from asaree.migrations.guards import drop_column

revision: str = '086b8b3d635b'
down_revision: str | None = '308e54c0539f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('protocol_runs', sa.Column('target_node_id', sa.String(length=255), nullable=True), if_not_exists=True)


def downgrade() -> None:
    drop_column('protocol_runs', 'target_node_id')
