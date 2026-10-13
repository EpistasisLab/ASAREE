"""Mark each result scope dirty at most once per transaction.

The row-level result triggers call asaree_dirty_results once per changed row.
Re-updating the same result_refreshes row inside one transaction leaves a chain
of dead versions that every later upsert walks, so bulk writes (design
generation, batch planning, Stop all) slowed quadratically: 15k rows took ~10s,
100k took many minutes. One generation bump per transaction is all the worker
needs; a transaction-local setting remembers which scopes were already bumped.
Rolled-back savepoints revert that setting too, so a lost bump is re-applied.
"""

from alembic import op

revision = "718293a4b5c6"
down_revision = "60718293a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_dirty_results(p uuid, v uuid, d uuid) RETURNS void LANGUAGE plpgsql AS $$
    DECLARE k text; actual_design uuid; seen text;
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM protocols WHERE id=p AND experiment_id IS NOT NULL) THEN RETURN; END IF;
      SELECT COALESCE(d, (SELECT design_revision_id FROM protocol_revisions WHERE id=v),
        (SELECT id FROM experiment_design_revisions WHERE experiment_id=(SELECT experiment_id FROM protocols WHERE id=p)
         AND superseded_at IS NULL)) INTO actual_design;
      k := p::text || ':' || COALESCE(v::text, 'legacy') || ':' || COALESCE(actual_design::text, 'none');
      seen := 'asaree_dirty.s' || md5(k);
      IF current_setting(seen, true) = '1' THEN RETURN; END IF;
      INSERT INTO result_refreshes(scope_key, protocol_id, protocol_revision_id, design_revision_id)
        VALUES(k,p,v,actual_design)
        ON CONFLICT(scope_key) DO UPDATE SET generation=result_refreshes.generation+1, updated_at=now();
      PERFORM set_config(seen, '1', true);
    END $$;
    """)


def downgrade() -> None:
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
