"""Index deferred accounting work and protect compact previews from large configs."""

from alembic import op
import sqlalchemy as sa

revision = "4e5f60718293"
down_revision = "3d4e5f607182"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_result_cell_metric_rank_desc ON result_cell_metrics "
               "(scope_key, metric_key, value DESC, cell_id)")
    op.create_index(
        "ix_protocol_runs_usage_pending",
        "protocol_runs",
        ["created_at", "id"],
        postgresql_where=sa.text(
            "status IN ('completed','failed','cancelled','limit_reached') AND NOT is_test_run "
            "AND target_node_id IS NULL AND (usage_summary IS NULL OR usage_summary->>'settled'='false')"
        ),
    )
    # A producer binding may contain a long rubric/config. List reads need only
    # producer identity, never its full configuration document.
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_result_preview(doc jsonb, nodes jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
      SELECT jsonb_build_object(
        'observations', COALESCE((SELECT jsonb_agg(jsonb_build_object(
          'metric_id', o->'metric_id', 'metric_name', o->'metric_name',
          'status', o->'status', 'value_type', o->'value_type',
          'preview', left(COALESCE(o->'value', 'null'::jsonb)::text, 512),
          'preview_truncated', length(COALESCE(o->'value', 'null'::jsonb)::text) > 512,
          'producer', jsonb_build_object('binding_id', o#>'{producer,binding_id}',
             'producer_id', o#>'{producer,producer_id}', 'kind', o#>'{producer,kind}', 'version', o#>'{producer,version}')
        )) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(doc#>'{measurement,observations}') = 'array'
            THEN doc#>'{measurement,observations}' ELSE '[]'::jsonb END) o), '[]'::jsonb),
        'metrics', COALESCE((SELECT jsonb_object_agg(o->>'metric_id', CASE
            WHEN jsonb_typeof(o->'value') = 'boolean' THEN to_jsonb(CASE WHEN (o->>'value')::boolean THEN 1 ELSE 0 END)
            ELSE o->'value' END)
          FROM jsonb_array_elements(CASE WHEN jsonb_typeof(doc#>'{measurement,observations}') = 'array'
            THEN doc#>'{measurement,observations}' ELSE '[]'::jsonb END) o
          WHERE o->>'status' = 'measured' AND o#>>'{producer,producer_id}' IN ('asaree.runtime','asaree.node_runtime')
            AND o->>'metric_id' IS NOT NULL AND jsonb_typeof(o->'value') IN ('number', 'boolean')), '{}'::jsonb),
        'truncated', EXISTS (SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(nodes)='object'
            THEN nodes ELSE '{}'::jsonb END) n WHERE jsonb_typeof(n.value->'truncation')='object'),
        'evaluation_state', doc->'evaluation_state'
      );
    $$;
    """)


def downgrade() -> None:
    op.drop_index("ix_result_cell_metric_rank_desc", table_name="result_cell_metrics")
    op.drop_index("ix_protocol_runs_usage_pending", table_name="protocol_runs")
    # The bounded producer identity is backward compatible with the original preview contract.
