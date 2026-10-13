"""Compact result previews, durable refresh events and paginated read indexes."""

from alembic import op

from asaree.migrations.guards import drop_column
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "3d4e5f607182"
down_revision = "2c3d4e5f6071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("protocol_runs", sa.Column("result_summary", postgresql.JSONB(), nullable=False, server_default="{}"), if_not_exists=True)
    op.add_column("protocol_runs", sa.Column("usage_summary", postgresql.JSONB(), nullable=True), if_not_exists=True)
    op.create_table(
        "result_refreshes",
        sa.Column("scope_key", sa.String(120), primary_key=True),
        sa.Column("protocol_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("protocols.id", ondelete="CASCADE"), nullable=False),
        sa.Column("protocol_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("protocol_revisions.id", ondelete="CASCADE")),
        sa.Column("design_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("experiment_design_revisions.id", ondelete="CASCADE")),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("processed_generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB()), sa.Column("error", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), if_not_exists=True)
    op.create_index("ix_result_refreshes_protocol_id", "result_refreshes", ["protocol_id"], if_not_exists=True)
    op.create_table(
        "result_cell_rollups",
        sa.Column("scope_key", sa.String(120), sa.ForeignKey("result_refreshes.scope_key", ondelete="CASCADE"), primary_key=True),
        sa.Column("cell_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("factorial_cells.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("generation", sa.BigInteger(), nullable=False), sa.Column("data", postgresql.JSONB(), nullable=False), if_not_exists=True)
    op.create_table("result_cell_metrics",
        sa.Column("scope_key", sa.String(120), sa.ForeignKey("result_refreshes.scope_key", ondelete="CASCADE"), primary_key=True),
        sa.Column("metric_key", sa.String(255), primary_key=True),
        sa.Column("cell_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("factorial_cells.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("value", sa.Float()), sa.Column("generation", sa.BigInteger(), nullable=False), if_not_exists=True)
    op.create_index("ix_result_cell_metric_rank", "result_cell_metrics", ["scope_key", "metric_key", "value", "cell_id"], if_not_exists=True)
    op.create_index("ix_protocol_runs_slot_version_order", "protocol_runs",
                    ["protocol_id", "protocol_revision_id", "design_revision_id", "replicate_result_id", "created_at", "id"],
                    postgresql_where=sa.text("row_result_id IS NULL"), if_not_exists=True)
    op.create_index("ix_protocol_runs_row_version_order", "protocol_runs",
                    ["row_result_id", "protocol_revision_id", "created_at", "id"],
                    postgresql_where=sa.text("row_result_id IS NOT NULL"), if_not_exists=True)
    op.create_index("ix_factorial_cells_revision_page", "factorial_cells", ["experiment_id", "design_revision_id", "id"], if_not_exists=True)
    op.create_index("ix_factorial_rows_version_page", "factorial_row_results", ["protocol_revision_id", "replicate_result_id", "id"], if_not_exists=True)
    # Previews are derived at the persistence seam, including notebook/legacy writes.
    # Reported values stay opaque; only bounded textual previews leave list reads.
    # asyncpg rejects multi-statement strings, so each DDL statement executes on its own.
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_result_preview(doc jsonb, nodes jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
      SELECT jsonb_build_object(
        'observations', COALESCE((SELECT jsonb_agg(jsonb_build_object(
          'metric_id', o->'metric_id', 'metric_name', o->'metric_name',
          'status', o->'status', 'value_type', o->'value_type',
          'preview', left(COALESCE(o->'value', 'null'::jsonb)::text, 512),
          'preview_truncated', length(COALESCE(o->'value', 'null'::jsonb)::text) > 512,
          'producer', o->'producer'
        )) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(doc#>'{measurement,observations}') = 'array'
            THEN doc#>'{measurement,observations}' ELSE '[]'::jsonb END) o), '[]'::jsonb),
        'metrics', COALESCE((SELECT jsonb_object_agg(o->>'metric_name', CASE
            WHEN jsonb_typeof(o->'value') = 'boolean' THEN to_jsonb(CASE WHEN (o->>'value')::boolean THEN 1 ELSE 0 END)
            ELSE o->'value' END)
          FROM jsonb_array_elements(CASE WHEN jsonb_typeof(doc#>'{measurement,observations}') = 'array'
            THEN doc#>'{measurement,observations}' ELSE '[]'::jsonb END) o
          WHERE o->>'status' = 'measured' AND o#>>'{producer,kind}' NOT IN ('reported', 'legacy')
            AND o->>'metric_name' IS NOT NULL AND jsonb_typeof(o->'value') IN ('number', 'boolean')), '{}'::jsonb),
        'truncated', EXISTS (SELECT 1 FROM jsonb_each(COALESCE(nodes, '{}'::jsonb)) n
          WHERE jsonb_typeof(n.value->'truncation') = 'object'),
        'evaluation_state', doc->'evaluation_state'
      );
    $$;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_preview_run() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN NEW.result_summary := asaree_result_preview(NEW.attempt_result, NEW.node_runs); RETURN NEW; END $$;
    """)
    op.execute("DROP TRIGGER IF EXISTS result_preview ON protocol_runs")
    op.execute("""
    CREATE TRIGGER result_preview BEFORE INSERT OR UPDATE OF attempt_result, node_runs ON protocol_runs
      FOR EACH ROW EXECUTE FUNCTION asaree_preview_run();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_dirty_results(p uuid, v uuid, d uuid) RETURNS void LANGUAGE plpgsql AS $$
    DECLARE k text; actual_design uuid;
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM protocols WHERE id=p AND experiment_id IS NOT NULL) THEN RETURN; END IF;
      SELECT COALESCE(d, (SELECT design_revision_id FROM protocol_revisions WHERE id=v),
        (SELECT id FROM experiment_design_revisions WHERE experiment_id=(SELECT experiment_id FROM protocols WHERE id=p)
         AND superseded_at IS NULL)) INTO actual_design;
      k := p::text || ':' || COALESCE(v::text, 'legacy') || ':' || COALESCE(actual_design::text, 'none');
      INSERT INTO result_refreshes(scope_key, protocol_id, protocol_revision_id, design_revision_id)
        VALUES(k,p,v,actual_design)
        ON CONFLICT(scope_key) DO UPDATE SET generation=result_refreshes.generation+1, updated_at=now();
    END $$;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_run_results_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN PERFORM asaree_dirty_results(OLD.protocol_id,OLD.protocol_revision_id,OLD.design_revision_id); RETURN OLD; END IF;
      PERFORM asaree_dirty_results(NEW.protocol_id,NEW.protocol_revision_id,NEW.design_revision_id); RETURN NEW;
    END $$;
    """)
    op.execute("DROP TRIGGER IF EXISTS results_changed ON protocol_runs")
    op.execute("""
    CREATE TRIGGER results_changed AFTER INSERT OR DELETE OR UPDATE OF status, attempt_result, usage_summary ON protocol_runs
      FOR EACH ROW EXECUTE FUNCTION asaree_run_results_changed();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_design_results_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE e uuid; p record; d uuid;
    BEGIN
      IF TG_TABLE_NAME='protocols' THEN
        PERFORM asaree_dirty_results(NEW.id, NEW.published_revision_id, NULL); RETURN NEW;
      END IF;
      IF TG_TABLE_NAME='factorial_cells' THEN e:=COALESCE(NEW.experiment_id, OLD.experiment_id);
      ELSIF TG_TABLE_NAME='factorial_replicate_results' THEN
        SELECT experiment_id INTO e FROM factorial_cells WHERE id=COALESCE(NEW.cell_id, OLD.cell_id);
      ELSE
        SELECT c.experiment_id INTO e FROM factorial_cells c JOIN factorial_replicate_results r ON r.cell_id=c.id
          WHERE r.id=COALESCE(NEW.replicate_result_id, OLD.replicate_result_id);
      END IF;
      FOR p IN SELECT id,published_revision_id FROM protocols WHERE experiment_id=e LOOP
        PERFORM asaree_dirty_results(p.id,p.published_revision_id,NULL);
      END LOOP;
      RETURN COALESCE(NEW,OLD);
    END $$;
    """)
    op.execute("DROP TRIGGER IF EXISTS protocol_results_changed ON protocols")
    op.execute("""
    CREATE TRIGGER protocol_results_changed AFTER INSERT OR UPDATE OF published_revision_id ON protocols
      FOR EACH ROW EXECUTE FUNCTION asaree_design_results_changed();
    """)
    op.execute("DROP TRIGGER IF EXISTS cell_results_changed ON factorial_cells")
    op.execute("""
    CREATE TRIGGER cell_results_changed AFTER INSERT OR DELETE OR UPDATE ON factorial_cells
      FOR EACH ROW EXECUTE FUNCTION asaree_design_results_changed();
    """)
    op.execute("DROP TRIGGER IF EXISTS replicate_results_changed ON factorial_replicate_results")
    op.execute("""
    CREATE TRIGGER replicate_results_changed AFTER INSERT OR DELETE OR UPDATE ON factorial_replicate_results
      FOR EACH ROW EXECUTE FUNCTION asaree_design_results_changed();
    """)
    op.execute("DROP TRIGGER IF EXISTS row_results_changed ON factorial_row_results")
    op.execute("""
    CREATE TRIGGER row_results_changed AFTER INSERT OR DELETE OR UPDATE ON factorial_row_results
      FOR EACH ROW EXECUTE FUNCTION asaree_design_results_changed();
    """)
    # Seed only refresh work. Historical evidence/usage is backfilled in bounded worker batches.
    op.execute("SELECT asaree_dirty_results(id, published_revision_id, NULL) FROM protocols")


def downgrade() -> None:
    for table, trigger in [("factorial_row_results", "row_results_changed"),
                           ("factorial_replicate_results", "replicate_results_changed"),
                           ("factorial_cells", "cell_results_changed"), ("protocols", "protocol_results_changed"),
                           ("protocol_runs", "results_changed"), ("protocol_runs", "result_preview")]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for function in ["asaree_design_results_changed()", "asaree_run_results_changed()",
                     "asaree_preview_run()", "asaree_dirty_results(uuid, uuid, uuid)", "asaree_result_preview(jsonb, jsonb)"]:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    for table, index in [("protocol_runs", "ix_protocol_runs_slot_version_order"),
                         ("protocol_runs", "ix_protocol_runs_row_version_order"),
                         ("factorial_cells", "ix_factorial_cells_revision_page"),
                         ("factorial_row_results", "ix_factorial_rows_version_page")]:
        op.drop_index(index, table_name=table, if_exists=True)
    op.drop_table("result_cell_metrics", if_exists=True)
    op.drop_table("result_cell_rollups", if_exists=True)
    op.drop_table("result_refreshes", if_exists=True)
    drop_column("protocol_runs", "usage_summary")
    drop_column("protocol_runs", "result_summary")
