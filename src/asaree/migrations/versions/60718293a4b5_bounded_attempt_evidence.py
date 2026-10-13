"""Bound selected attempt previews; exact evidence remains in chunked reads/exports."""

from alembic import op

from asaree.migrations import guards
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "60718293a4b5"
down_revision = "5f60718293a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('ix_protocol_runs_usage_pending', table_name='protocol_runs', if_exists=True)
    op.create_index('ix_protocol_runs_usage_pending', 'protocol_runs', ['created_at', 'id'],
        postgresql_where=sa.text("status IN ('completed','failed','cancelled','limit_reached') AND NOT is_test_run "
            "AND target_node_id IS NULL AND (usage_summary IS NULL OR usage_summary='null'::jsonb OR usage_summary->>'settled'='false')"), if_not_exists=True)
    op.add_column("registered_datasets", sa.Column("row_schema", JSONB, nullable=True), if_not_exists=True)
    op.create_index("ix_registered_datasets_row_schema_pending", "registered_datasets", ["updated_at"],
        postgresql_where=sa.text("row_schema->>'status'='pending'"), if_not_exists=True)
    op.execute("CREATE INDEX IF NOT EXISTS ix_protocol_runs_head_page ON protocol_runs (protocol_id, created_at DESC, id DESC) "
        "WHERE NOT is_test_run AND target_node_id IS NULL")
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_node_preview(nodes jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
      SELECT COALESCE(jsonb_object_agg(key, jsonb_build_object(
        'status',value->'status','run_id',value->'run_id','bypassed',value->'bypassed',
        'output_text',left(value->>'output_text',4096),'error',left(value->>'error',2048),
        'unresolved_references',value->'unresolved_references','truncation',value->'truncation'
      )), '{}'::jsonb) FROM jsonb_each(CASE WHEN jsonb_typeof(nodes)='object' THEN nodes ELSE '{}'::jsonb END);
    $$;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_attempt_preview(doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
      SELECT jsonb_build_object(
        'metric_values', CASE WHEN length((doc->'metric_values')::text)<=16384 THEN doc->'metric_values' ELSE '{}'::jsonb END,
        'metric_values_deferred', COALESCE(length((doc->'metric_values')::text)>16384,false),
        'metric_evaluation', doc->'metric_evaluation',
        'measurement', jsonb_build_object(
          'observations', COALESCE((SELECT jsonb_agg(
            (o - 'value' - 'input_provenance' - 'producer') || jsonb_build_object(
              'value', CASE WHEN length((o->'value')::text)<=16384 THEN o->'value' ELSE 'null'::jsonb END,
              'value_truncated', COALESCE(length((o->'value')::text)>16384,false),
              'value_preview', CASE WHEN length((o->'value')::text)>16384 THEN left((o->'value')::text,16384) ELSE NULL END,
              'producer', (o->'producer') - 'binding_config'
            )) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(doc#>'{measurement,observations}')='array'
              THEN doc#>'{measurement,observations}' ELSE '[]'::jsonb END) o), '[]'::jsonb),
          'artifacts', COALESCE((SELECT jsonb_agg(
            (a - 'payload' - 'input_provenance' - 'producer') || jsonb_build_object(
              'payload', CASE WHEN length((a->'payload')::text)<=16384 THEN a->'payload' ELSE 'null'::jsonb END,
              'payload_truncated', COALESCE(length((a->'payload')::text)>16384,false),
              'payload_preview', CASE WHEN length((a->'payload')::text)>16384 THEN left((a->'payload')::text,16384) ELSE NULL END,
              'producer', (a->'producer') - 'binding_config'
            )) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(doc#>'{measurement,artifacts}')='array'
              THEN doc#>'{measurement,artifacts}' ELSE '[]'::jsonb END) a), '[]'::jsonb)
        )
      );
    $$;
    """)
    op.execute("UPDATE result_refreshes SET generation=generation+1")


def downgrade() -> None:
    op.drop_index('ix_protocol_runs_usage_pending', table_name='protocol_runs', if_exists=True)
    op.create_index('ix_protocol_runs_usage_pending', 'protocol_runs', ['created_at', 'id'],
        postgresql_where=sa.text("status IN ('completed','failed','cancelled','limit_reached') AND NOT is_test_run "
            "AND target_node_id IS NULL AND (usage_summary IS NULL OR usage_summary->>'settled'='false')"), if_not_exists=True)
    op.drop_index("ix_registered_datasets_row_schema_pending", table_name="registered_datasets", if_exists=True)
    guards.drop_column("registered_datasets", "row_schema")
    op.drop_index("ix_protocol_runs_head_page", table_name="protocol_runs", if_exists=True)
    op.execute("DROP FUNCTION IF EXISTS asaree_attempt_preview(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS asaree_node_preview(jsonb)")
