"""Keep only automatic statistics reports; drop the prototype backend column."""

import sqlalchemy as sa
from alembic import op

revision = "8de692fb1681"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Manual prototype analyses have no remaining page or endpoint to show them.
    op.execute("""
        DELETE FROM stats_analyses
        WHERE backend <> 'stats_sm' OR request #>> '{config,family}' IS DISTINCT FROM 'auto'
    """)
    op.drop_column("stats_analyses", "backend")


def downgrade() -> None:
    op.add_column(
        "stats_analyses", sa.Column("backend", sa.String(16), nullable=False, server_default="stats_sm")
    )
    op.alter_column("stats_analyses", "backend", server_default=None)
