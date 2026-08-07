"""user auth and api token lifecycle

Revision ID: ce86ecab27a1
Revises: cf66556232af
Create Date: 2026-08-07 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ce86ecab27a1'
down_revision: str | None = 'cf66556232af'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- users: display_name (backfilled from email, then NOT NULL), is_admin, last_login_at ---
    op.add_column('users', sa.Column('display_name', sa.String(length=100), nullable=True))
    op.execute("UPDATE users SET display_name = email WHERE display_name IS NULL")
    op.alter_column('users', 'display_name', nullable=False)
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True))

    # --- user_api_tokens: token_prefix (nullable -- legacy tokens have none), expires_at, is_revoked ---
    op.add_column('user_api_tokens', sa.Column('token_prefix', sa.String(length=16), nullable=True))
    op.add_column('user_api_tokens', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'user_api_tokens', sa.Column('is_revoked', sa.Boolean(), server_default='false', nullable=False)
    )

    # --- password_reset_tokens ---
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_used', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_password_reset_tokens_token_hash'), 'password_reset_tokens', ['token_hash'], unique=True
    )
    op.create_index(
        op.f('ix_password_reset_tokens_user_id'), 'password_reset_tokens', ['user_id'], unique=False
    )

    # --- audit_log_entries ---
    op.create_table(
        'audit_log_entries',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('resource_type', sa.String(length=100), nullable=False),
        sa.Column('resource_id', sa.UUID(), nullable=True),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_audit_log_entries_action'), 'audit_log_entries', ['action'], unique=False)
    op.create_index(op.f('ix_audit_log_entries_user_id'), 'audit_log_entries', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_audit_log_entries_user_id'), table_name='audit_log_entries')
    op.drop_index(op.f('ix_audit_log_entries_action'), table_name='audit_log_entries')
    op.drop_table('audit_log_entries')

    op.drop_index(op.f('ix_password_reset_tokens_user_id'), table_name='password_reset_tokens')
    op.drop_index(op.f('ix_password_reset_tokens_token_hash'), table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')

    op.drop_column('user_api_tokens', 'is_revoked')
    op.drop_column('user_api_tokens', 'expires_at')
    op.drop_column('user_api_tokens', 'token_prefix')

    op.drop_column('users', 'last_login_at')
    op.drop_column('users', 'is_admin')
    op.drop_column('users', 'display_name')
