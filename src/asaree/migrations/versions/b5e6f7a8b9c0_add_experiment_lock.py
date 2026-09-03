"""add reproducible experiment lock snapshots

Revision ID: b5e6f7a8b9c0
Revises: a7b8c9d0e1f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5e6f7a8b9c0"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_experiments", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True), if_not_exists=True)
    op.add_column("research_experiments", sa.Column("locked_protocol_revision_id", postgresql.UUID(as_uuid=True), nullable=True), if_not_exists=True)
    op.add_column("research_experiments", sa.Column("locked_design_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True), if_not_exists=True)


def downgrade() -> None:
    op.drop_column("research_experiments", "locked_design_spec")
    op.drop_column("research_experiments", "locked_protocol_revision_id")
    op.drop_column("research_experiments", "locked_at")
