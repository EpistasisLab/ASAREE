"""add immutable protocol-run attempt results

Revision ID: b8c9d0e1f2a3
Revises: b5e6f7a8b9c0
Create Date: 2026-09-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "b5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "protocol_runs", sa.Column("attempt_result", sa.dialects.postgresql.JSONB(), nullable=True), if_not_exists=True
    )
    # Existing latest projections may already point at a run made against an
    # older published canvas. Preserve their inspectable facts on that run,
    # then clear the mutable projection so legacy obsolete scores cannot leak
    # into new Results/CSV/statistical analysis after this upgrade.
    op.execute(
        """
        WITH stale AS (
            SELECT r.id AS replicate_id, pr.id AS protocol_run_id,
                   r.metric_values, r.artifacts
            FROM factorial_replicate_results AS r
            JOIN protocol_runs AS pr ON pr.id = r.run_id
            JOIN protocols AS p ON p.id = pr.protocol_id
            LEFT JOIN protocol_revisions AS current_revision
                ON current_revision.id = p.published_revision_id
            WHERE p.published_revision_id IS NOT NULL
              AND (
                  (pr.protocol_revision_id IS NOT NULL
                   AND pr.protocol_revision_id <> p.published_revision_id)
                  OR (pr.protocol_revision_id IS NULL
                      AND current_revision.published_at IS NOT NULL
                      AND pr.created_at < current_revision.published_at)
              )
        )
        UPDATE protocol_runs AS pr
        SET attempt_result = COALESCE(pr.attempt_result, '{}'::jsonb)
            || jsonb_strip_nulls(jsonb_build_object(
                'metric_values', stale.metric_values,
                'metric_evaluation', stale.artifacts->'metric_evaluation'
            ))
        FROM stale
        WHERE pr.id = stale.protocol_run_id
        """
    )
    op.execute(
        """
        UPDATE factorial_replicate_results AS r
        SET metric_values = NULL, artifacts = NULL
        FROM protocol_runs AS pr
        JOIN protocols AS p ON p.id = pr.protocol_id
        LEFT JOIN protocol_revisions AS current_revision
            ON current_revision.id = p.published_revision_id
        WHERE pr.id = r.run_id
          AND p.published_revision_id IS NOT NULL
          AND (
              (pr.protocol_revision_id IS NOT NULL
               AND pr.protocol_revision_id <> p.published_revision_id)
              OR (pr.protocol_revision_id IS NULL
                  AND current_revision.published_at IS NOT NULL
                  AND pr.created_at < current_revision.published_at)
          )
        """
    )


def downgrade() -> None:
    op.drop_column("protocol_runs", "attempt_result", if_exists=True)
