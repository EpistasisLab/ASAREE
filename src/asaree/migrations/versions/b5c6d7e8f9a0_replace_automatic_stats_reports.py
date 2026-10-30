"""Remove superseded automatic reports within each experiment version."""

from alembic import op

revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY experiment_id,
                             request #>> '{source,protocol_revision_id}',
                             request #>> '{config,metric}'
                ORDER BY created_at DESC, id DESC
            ) AS position
            FROM stats_analyses
            WHERE backend = 'stats_sm'
              AND request #>> '{config,family}' = 'auto'
              AND request #>> '{source,protocol_revision_id}' IS NOT NULL
        )
        DELETE FROM stats_analyses
        USING ranked
        WHERE stats_analyses.id = ranked.id AND ranked.position > 1
    """)


def downgrade() -> None:
    # Superseded reports were intentionally deleted; they cannot be restored.
    pass
