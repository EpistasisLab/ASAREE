"""normalize factorial cells and replicate results

A cell is one unique factor combination and owns all of its replicates. The
old ``factorial_cell_results`` table stored one row per replicate, so its name
encoded the wrong cardinality. This migration introduces the cell parent,
renames the old rows to replicate results, and preserves every result UUID.

Revision ID: a7b8c9d0e1f2
Revises: f9a0b1c2d3e4
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("factorial_cell_results", "factorial_replicate_results")
    op.alter_column("factorial_replicate_results", "cell_label", new_column_name="replicate_label")

    op.create_table(
        "factorial_cells",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("design_revision_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cell_label", sa.String(length=255), nullable=False),
        sa.Column("factor_values", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["research_experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["design_revision_id"], ["experiment_design_revisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("design_revision_id", "cell_label", name="uq_factorial_cells_revision_cell"),
    )
    op.create_index("ix_factorial_cells_experiment_id", "factorial_cells", ["experiment_id"])
    op.create_index("ix_factorial_cells_design_revision_id", "factorial_cells", ["design_revision_id"])

    # Bookkeeping keys historically embedded by some notebook clients are
    # replicate metadata, not factors. They are removed from the parent cell's
    # canonical factor combination during normalization.
    op.execute(
        """
        INSERT INTO factorial_cells (
            id, experiment_id, design_revision_id, cell_label, factor_values, created_at, updated_at
        )
        SELECT DISTINCT ON (design_revision_id, combination_key)
            gen_random_uuid(), experiment_id, design_revision_id, base_label,
            normalized_factors,
            created_at, updated_at
        FROM (
            SELECT normalized.*,
                   CASE
                       WHEN normalized_factors IS NULL OR normalized_factors = '{}'::jsonb
                       THEN 'label:' || base_label
                       ELSE 'factors:' || normalized_factors::text
                   END AS combination_key
            FROM (
                SELECT r.*,
                       regexp_replace(r.replicate_label, '__rep[0-9]+$', '') AS base_label,
                       r.factor_values - ARRAY['replicate', 'seed', 'rep', 'trial', 'iteration']::text[]
                           AS normalized_factors
                FROM factorial_replicate_results AS r
            ) AS normalized
        ) AS legacy
        ORDER BY design_revision_id, combination_key, normalized_factors IS NULL, created_at, id
        """
    )
    op.create_index(
        "uq_factorial_cells_revision_factors",
        "factorial_cells",
        ["design_revision_id", "factor_values"],
        unique=True,
        postgresql_where=sa.text("factor_values IS NOT NULL AND factor_values <> '{}'::jsonb"),
    )

    op.add_column(
        "factorial_replicate_results",
        sa.Column("cell_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("factorial_replicate_results", sa.Column("replicate_number", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE factorial_replicate_results AS r
        SET cell_id = c.id,
            replicate_number = CASE
                WHEN r.replicate_label ~ '__rep[0-9]+$'
                THEN substring(r.replicate_label FROM '__rep([0-9]+)$')::integer
                ELSE 1
            END
        FROM factorial_cells AS c
        WHERE c.design_revision_id = r.design_revision_id
          AND (
              (
                  r.factor_values - ARRAY['replicate', 'seed', 'rep', 'trial', 'iteration']::text[]
                      IS NOT NULL
                  AND r.factor_values - ARRAY['replicate', 'seed', 'rep', 'trial', 'iteration']::text[]
                      <> '{}'::jsonb
                  AND c.factor_values =
                      r.factor_values - ARRAY['replicate', 'seed', 'rep', 'trial', 'iteration']::text[]
              )
              OR (
                  (r.factor_values IS NULL OR
                   r.factor_values - ARRAY['replicate', 'seed', 'rep', 'trial', 'iteration']::text[] = '{}'::jsonb)
                  AND c.cell_label = regexp_replace(r.replicate_label, '__rep[0-9]+$', '')
              )
          )
        """
    )
    op.alter_column("factorial_replicate_results", "cell_id", nullable=False)
    op.alter_column("factorial_replicate_results", "replicate_number", nullable=False)

    op.drop_constraint(
        "uq_factorial_cell_results_revision_cell", "factorial_replicate_results", type_="unique"
    )
    op.drop_constraint(
        "factorial_cell_results_experiment_id_fkey", "factorial_replicate_results", type_="foreignkey"
    )
    op.drop_constraint(
        "factorial_cell_results_design_revision_id_fkey", "factorial_replicate_results", type_="foreignkey"
    )
    op.drop_index("ix_factorial_cell_results_experiment_id", table_name="factorial_replicate_results")
    op.drop_index("ix_factorial_cell_results_design_revision_id", table_name="factorial_replicate_results")
    op.drop_index("ix_factorial_cell_results_run_id", table_name="factorial_replicate_results")
    op.drop_column("factorial_replicate_results", "experiment_id")
    op.drop_column("factorial_replicate_results", "design_revision_id")
    op.drop_column("factorial_replicate_results", "factor_values")
    op.create_foreign_key(
        "factorial_replicate_results_cell_id_fkey",
        "factorial_replicate_results",
        "factorial_cells",
        ["cell_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_factorial_replicate_results_cell_id", "factorial_replicate_results", ["cell_id"])
    op.create_index("ix_factorial_replicate_results_run_id", "factorial_replicate_results", ["run_id"])
    op.create_unique_constraint(
        "uq_factorial_replicate_results_cell_number",
        "factorial_replicate_results",
        ["cell_id", "replicate_number"],
    )
    op.create_unique_constraint(
        "uq_factorial_replicate_results_cell_label",
        "factorial_replicate_results",
        ["cell_id", "replicate_label"],
    )

    op.alter_column("protocol_runs", "cell_label", new_column_name="replicate_label")
    op.add_column(
        "protocol_runs",
        sa.Column("replicate_result_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE protocol_runs AS p
        SET replicate_result_id = r.id
        FROM factorial_replicate_results AS r
        JOIN factorial_cells AS c ON c.id = r.cell_id
        WHERE p.design_revision_id = c.design_revision_id
          AND p.replicate_label = r.replicate_label
        """
    )
    op.create_foreign_key(
        "protocol_runs_replicate_result_id_fkey",
        "protocol_runs",
        "factorial_replicate_results",
        ["replicate_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_protocol_runs_replicate_result_id", "protocol_runs", ["replicate_result_id"])


def downgrade() -> None:
    op.drop_index("ix_protocol_runs_replicate_result_id", table_name="protocol_runs")
    op.drop_constraint("protocol_runs_replicate_result_id_fkey", "protocol_runs", type_="foreignkey")
    op.drop_column("protocol_runs", "replicate_result_id")
    op.alter_column("protocol_runs", "replicate_label", new_column_name="cell_label")

    op.add_column(
        "factorial_replicate_results",
        sa.Column("experiment_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "factorial_replicate_results",
        sa.Column("design_revision_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "factorial_replicate_results", sa.Column("factor_values", sa.dialects.postgresql.JSONB(), nullable=True)
    )
    op.execute(
        """
        UPDATE factorial_replicate_results AS r
        SET experiment_id = c.experiment_id,
            design_revision_id = c.design_revision_id,
            factor_values = c.factor_values
        FROM factorial_cells AS c
        WHERE c.id = r.cell_id
        """
    )
    op.alter_column("factorial_replicate_results", "experiment_id", nullable=False)
    op.alter_column("factorial_replicate_results", "design_revision_id", nullable=False)
    op.create_foreign_key(
        "factorial_cell_results_experiment_id_fkey",
        "factorial_replicate_results",
        "research_experiments",
        ["experiment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "factorial_cell_results_design_revision_id_fkey",
        "factorial_replicate_results",
        "experiment_design_revisions",
        ["design_revision_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_factorial_replicate_results_cell_number", "factorial_replicate_results", type_="unique"
    )
    op.drop_constraint(
        "uq_factorial_replicate_results_cell_label", "factorial_replicate_results", type_="unique"
    )
    op.drop_constraint(
        "factorial_replicate_results_cell_id_fkey", "factorial_replicate_results", type_="foreignkey"
    )
    op.drop_index("ix_factorial_replicate_results_cell_id", table_name="factorial_replicate_results")
    op.drop_index("ix_factorial_replicate_results_run_id", table_name="factorial_replicate_results")
    op.drop_column("factorial_replicate_results", "cell_id")
    op.drop_column("factorial_replicate_results", "replicate_number")
    op.alter_column("factorial_replicate_results", "replicate_label", new_column_name="cell_label")
    op.rename_table("factorial_replicate_results", "factorial_cell_results")
    op.create_index("ix_factorial_cell_results_experiment_id", "factorial_cell_results", ["experiment_id"])
    op.create_index(
        "ix_factorial_cell_results_design_revision_id", "factorial_cell_results", ["design_revision_id"]
    )
    op.create_index("ix_factorial_cell_results_run_id", "factorial_cell_results", ["run_id"])
    op.create_unique_constraint(
        "uq_factorial_cell_results_revision_cell",
        "factorial_cell_results",
        ["design_revision_id", "cell_label"],
    )
    op.drop_index("ix_factorial_cells_design_revision_id", table_name="factorial_cells")
    op.drop_index("ix_factorial_cells_experiment_id", table_name="factorial_cells")
    op.drop_index("uq_factorial_cells_revision_factors", table_name="factorial_cells")
    op.drop_table("factorial_cells")
