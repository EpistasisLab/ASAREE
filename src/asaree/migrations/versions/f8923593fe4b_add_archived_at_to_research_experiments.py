"""add archived_at to research_experiments

Revision ID: f8923593fe4b
Revises: 083bbfcb4692
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'f8923593fe4b'
down_revision: str | None = '083bbfcb4692'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('research_experiments', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('research_experiments', 'archived_at')
