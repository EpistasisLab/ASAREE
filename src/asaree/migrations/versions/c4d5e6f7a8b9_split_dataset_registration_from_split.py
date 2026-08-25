"""split dataset registration from split

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-16 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from asaree.migrations.guards import drop_column

revision: str = 'c4d5e6f7a8b9'
down_revision: str | None = 'b3c4d5e6f7a8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('registered_datasets', sa.Column('raw_path', sa.Text(), nullable=True), if_not_exists=True)
    op.add_column('registered_datasets', sa.Column('raw_sha256', sa.String(length=64), nullable=True), if_not_exists=True)
    op.alter_column('registered_datasets', 'train_path', existing_type=sa.Text(), nullable=True)
    op.alter_column('registered_datasets', 'test_path', existing_type=sa.Text(), nullable=True)
    op.alter_column('registered_datasets', 'train_sha256', existing_type=sa.String(length=64), nullable=True)
    op.alter_column('registered_datasets', 'test_sha256', existing_type=sa.String(length=64), nullable=True)


def downgrade() -> None:
    op.alter_column('registered_datasets', 'test_sha256', existing_type=sa.String(length=64), nullable=False)
    op.alter_column('registered_datasets', 'train_sha256', existing_type=sa.String(length=64), nullable=False)
    op.alter_column('registered_datasets', 'test_path', existing_type=sa.Text(), nullable=False)
    op.alter_column('registered_datasets', 'train_path', existing_type=sa.Text(), nullable=False)
    drop_column('registered_datasets', 'raw_sha256')
    drop_column('registered_datasets', 'raw_path')
