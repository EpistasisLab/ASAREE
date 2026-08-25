"""add experiment artifacts

Revision ID: b3c4d5e6f7a8
Revises: 086b8b3d635b
Create Date: 2026-08-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'b3c4d5e6f7a8'
down_revision: str | None = '086b8b3d635b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'experiment_artifacts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('experiment_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('kind', sa.String(length=64), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['experiment_id'], ['research_experiments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        if_not_exists=True,
    )
    op.create_index(
        op.f('ix_experiment_artifacts_experiment_id'), 'experiment_artifacts', ['experiment_id'], unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_experiment_artifacts_experiment_id'), table_name='experiment_artifacts', if_exists=True)
    op.drop_table('experiment_artifacts', if_exists=True)
