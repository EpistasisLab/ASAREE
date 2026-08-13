"""add protocol_runs

Revision ID: 76d90f83e471
Revises: 609fbd06fd0b
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '76d90f83e471'
down_revision: str | None = '609fbd06fd0b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'protocol_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('protocol_id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('node_runs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['protocol_id'], ['protocols.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_protocol_runs_protocol_id'), 'protocol_runs', ['protocol_id'], unique=False)
    op.create_index(op.f('ix_protocol_runs_owner_id'), 'protocol_runs', ['owner_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_protocol_runs_owner_id'), table_name='protocol_runs')
    op.drop_index(op.f('ix_protocol_runs_protocol_id'), table_name='protocol_runs')
    op.drop_table('protocol_runs')
