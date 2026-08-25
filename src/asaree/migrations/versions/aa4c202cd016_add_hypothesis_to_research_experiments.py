"""add hypothesis to research_experiments

Revision ID: aa4c202cd016
Revises: f8923593fe4b
Create Date: 2026-08-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from asaree.migrations.guards import drop_column

revision: str = 'aa4c202cd016'
down_revision: str | None = 'f8923593fe4b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('research_experiments', sa.Column('hypothesis', sa.Text(), nullable=True), if_not_exists=True)


def downgrade() -> None:
    drop_column('research_experiments', 'hypothesis')
