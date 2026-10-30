"""Keep only automatic statistics reports; drop the prototype backend column."""

import sqlalchemy as sa
from alembic import op

from asaree.migrations.guards import drop_column

revision = "8de692fb1681"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Manual prototype analyses have no remaining page or endpoint to show them.
    # A lost stamp can replay this after backend has already been dropped.
    # Keep the guard in SQL so offline migration generation works too.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'stats_analyses' AND column_name = 'backend'
            ) THEN
                DELETE FROM stats_analyses
                WHERE backend <> 'stats_sm' OR request #>> '{config,family}' IS DISTINCT FROM 'auto';
            END IF;
        END $$;
    """)
    drop_column("stats_analyses", "backend")


def downgrade() -> None:
    op.add_column(
        "stats_analyses",
        sa.Column("backend", sa.String(16), nullable=False, server_default="stats_sm"),
        if_not_exists=True,
    )
    op.alter_column("stats_analyses", "backend", server_default=None)
