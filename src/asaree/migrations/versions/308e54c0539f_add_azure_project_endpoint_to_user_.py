"""add azure_project_endpoint to user_llm_settings

Revision ID: 308e54c0539f
Revises: aa4c202cd016
Create Date: 2026-08-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from asaree.migrations.guards import drop_column

revision: str = '308e54c0539f'
down_revision: str | None = 'aa4c202cd016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('user_llm_settings', sa.Column('azure_project_endpoint', sa.Text(), nullable=True), if_not_exists=True)


def downgrade() -> None:
    drop_column('user_llm_settings', 'azure_project_endpoint')
