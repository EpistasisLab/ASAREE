"""add dictionary_json to registered_datasets

Revision ID: 0eda81db4d05
Revises: 38faecf95a18
Create Date: 2026-08-05 11:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from asaree.migrations.guards import drop_column

revision: str = '0eda81db4d05'
down_revision: str | None = '38faecf95a18'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('registered_datasets', sa.Column('dictionary_json', sa.Text(), nullable=True), if_not_exists=True)


def downgrade() -> None:
    drop_column('registered_datasets', 'dictionary_json')
