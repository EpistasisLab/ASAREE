"""add protocols

Revision ID: 609fbd06fd0b
Revises: a1b2c3d4e5f6
Create Date: 2026-08-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '609fbd06fd0b'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'protocols',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('graph', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('experiment_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['experiment_id'], ['research_experiments.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_protocols_owner_id'), 'protocols', ['owner_id'], unique=False)
    op.create_index(op.f('ix_protocols_experiment_id'), 'protocols', ['experiment_id'], unique=False)
    op.create_index('uq_protocols_owner_name', 'protocols', ['owner_id', 'name'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_protocols_owner_name', table_name='protocols')
    op.drop_index(op.f('ix_protocols_experiment_id'), table_name='protocols')
    op.drop_index(op.f('ix_protocols_owner_id'), table_name='protocols')
    op.drop_table('protocols')
