"""add cell_label and factor_values to protocol_runs

Revision ID: 083bbfcb4692
Revises: 76d90f83e471
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '083bbfcb4692'
down_revision: str | None = '76d90f83e471'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('protocol_runs', sa.Column('cell_label', sa.String(length=255), nullable=True))
    op.add_column('protocol_runs', sa.Column('factor_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('protocol_runs', 'factor_values')
    op.drop_column('protocol_runs', 'cell_label')
