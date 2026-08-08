"""add dataset_id to research_experiments

Revision ID: a1b2c3d4e5f6
Revises: ce86ecab27a1
Create Date: 2026-08-07 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'ce86ecab27a1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable and SET NULL on delete: an experiment created before this
    # column existed (or whose dataset was later removed) simply has no
    # dataset attached -- not an error condition, not a reason to lose the
    # experiment or its cell results.
    op.add_column('research_experiments', sa.Column('dataset_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'research_experiments_dataset_id_fkey',
        'research_experiments',
        'registered_datasets',
        ['dataset_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        op.f('ix_research_experiments_dataset_id'), 'research_experiments', ['dataset_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_research_experiments_dataset_id'), table_name='research_experiments')
    op.drop_constraint('research_experiments_dataset_id_fkey', 'research_experiments', type_='foreignkey')
    op.drop_column('research_experiments', 'dataset_id')
