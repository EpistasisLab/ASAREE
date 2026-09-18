"""add metric recommendation lifecycle metadata

Revision ID: 9e4a7b2c1d30
Revises: f1a2b3c4d5e6
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9e4a7b2c1d30"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows deliberately remain null: null identifies measurement
    # plans that predate the versioned baseline invitation.
    op.add_column(
        "research_experiments",
        sa.Column("metric_recommendations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_experiments", "metric_recommendations")
