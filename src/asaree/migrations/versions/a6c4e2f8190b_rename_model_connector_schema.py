"""rename Model connector schema identifiers

Revision ID: a6c4e2f8190b
Revises: 9e4a7b2c1d30
Create Date: 2026-09-23 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a6c4e2f8190b"
down_revision: str | None = "9e4a7b2c1d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rewrite_node_types(table: str, column: str, mapping: dict[str, str]) -> None:
    cases = " ".join(f"WHEN n->>'type' = '{old}' THEN '{new}'" for old, new in mapping.items())
    old_values = ", ".join(f"'{value}'" for value in mapping)
    op.execute(
        f"""
        UPDATE {table} AS target_row
        SET {column} = jsonb_set(
            target_row.{column},
            '{{nodes}}',
            (
                SELECT jsonb_agg(
                    CASE
                        WHEN n->>'type' IN ({old_values})
                        THEN jsonb_set(n, '{{type}}', to_jsonb(CASE {cases} ELSE n->>'type' END))
                        ELSE n
                    END
                    ORDER BY ord
                )
                FROM jsonb_array_elements(target_row.{column}->'nodes') WITH ORDINALITY AS items(n, ord)
            )
        )
        WHERE jsonb_typeof(target_row.{column}->'nodes') = 'array'
          AND jsonb_array_length(target_row.{column}->'nodes') > 0
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(target_row.{column}->'nodes') AS n
              WHERE n->>'type' IN ({old_values})
          )
        """
    )


def _rewrite_handles(table: str, column: str, old_values: tuple[str, ...], new_value: str) -> None:
    old_sql = ", ".join(f"'{value}'" for value in old_values)
    for field in ("sourceHandle", "targetHandle"):
        op.execute(
            f"""
            UPDATE {table} AS target_row
            SET {column} = jsonb_set(
                target_row.{column},
                '{{edges}}',
                (
                    SELECT jsonb_agg(
                        CASE
                            WHEN edge->>'{field}' IN ({old_sql})
                            THEN jsonb_set(edge, '{{{field}}}', '"{new_value}"')
                            ELSE edge
                        END
                        ORDER BY ord
                    )
                    FROM jsonb_array_elements(target_row.{column}->'edges') WITH ORDINALITY AS items(edge, ord)
                )
            )
            WHERE jsonb_typeof(target_row.{column}->'edges') = 'array'
              AND jsonb_array_length(target_row.{column}->'edges') > 0
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(target_row.{column}->'edges') AS edge
                  WHERE edge->>'{field}' IN ({old_sql})
              )
            """
        )


def _rewrite_factor_level_type(table: str, column: str, old_value: str, new_value: str) -> None:
    op.execute(
        f"""
        UPDATE {table} AS target_row
        SET {column} = jsonb_set(
            target_row.{column},
            '{{factors}}',
            (
                SELECT jsonb_agg(
                    CASE
                        WHEN factor->>'level_type' = '{old_value}'
                        THEN jsonb_set(factor, '{{level_type}}', '"{new_value}"')
                        ELSE factor
                    END
                    ORDER BY ord
                )
                FROM jsonb_array_elements(target_row.{column}->'factors') WITH ORDINALITY AS items(factor, ord)
            )
        )
        WHERE jsonb_typeof(target_row.{column}->'factors') = 'array'
          AND jsonb_array_length(target_row.{column}->'factors') > 0
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(target_row.{column}->'factors') AS factor
              WHERE factor->>'level_type' = '{old_value}'
          )
        """
    )


def _rewrite_graphs(node_types: dict[str, str], handles: tuple[str, ...], new_handle: str) -> None:
    for table in ("protocols", "protocol_revisions"):
        _rewrite_node_types(table, "graph", node_types)
        _rewrite_handles(table, "graph", handles, new_handle)


def _rewrite_design_specs(old_value: str, new_value: str) -> None:
    for table, column in (
        ("research_experiments", "design_spec"),
        ("research_experiments", "locked_design_spec"),
        ("experiment_design_revisions", "design_spec"),
    ):
        _rewrite_factor_level_type(table, column, old_value, new_value)


def upgrade() -> None:
    _rewrite_graphs(
        {
            "llm_anthropic": "model_anthropic",
            "llm_openai": "model_openai",
            "llm_azure_foundry": "model_azure_foundry",
            "llm_openrouter": "model_openrouter",
            "llm_local": "model_local",
        },
        ("ai", "llm"),
        "model",
    )
    _rewrite_design_specs("llm_config", "model_config")


def downgrade() -> None:
    _rewrite_graphs(
        {
            "model_anthropic": "llm_anthropic",
            "model_openai": "llm_openai",
            "model_azure_foundry": "llm_azure_foundry",
            "model_openrouter": "llm_openrouter",
            "model_local": "llm_local",
        },
        ("model",),
        "ai",
    )
    _rewrite_design_specs("model_config", "llm_config")
