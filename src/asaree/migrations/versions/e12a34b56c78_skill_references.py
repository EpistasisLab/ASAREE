"""Retain registered skills across ASAREE drafts, publications, designs and runs.

The ledger is database-maintained so imports and cascade deletion obey the same
rules as interactive writes. No skill content is copied into ASAREE.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "e12a34b56c78"
down_revision = "d90e1f2a3b4c"
branch_labels = None
depends_on = None

SCOPES = {"protocol": "protocols", "publication": "protocol_revisions", "run": "protocol_runs",
          "experiment": "research_experiments", "design": "experiment_design_revisions"}


def upgrade() -> None:
    op.create_table("skill_references",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column("skill_id", pg.UUID(), nullable=False),
        *(sa.Column(f"{scope}_id", pg.UUID(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE"))
          for scope, table in SCOPES.items()),
        sa.CheckConstraint("num_nonnulls(protocol_id, publication_id, run_id, experiment_id, design_id) = 1",
                           name="ck_skill_reference_scope"), if_not_exists=True)
    op.create_index("ix_skill_references_skill_id", "skill_references", ["skill_id"], if_not_exists=True)
    for scope in SCOPES:
        op.create_index(f"uq_skill_reference_{scope}", "skill_references", [f"{scope}_id", "skill_id"],
                        unique=True, if_not_exists=True)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_skill_uuid(value text) RETURNS uuid
    LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN
      RETURN value::uuid;
    EXCEPTION WHEN invalid_text_representation THEN RETURN NULL; END $$
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_skill_array(value jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
      SELECT CASE WHEN jsonb_typeof(value)='array' THEN value ELSE '[]'::jsonb END
    $$
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_skill_design(current_spec jsonb, locked_spec jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
      SELECT jsonb_build_object('factors', asaree_skill_array(current_spec->'factors') ||
                                         asaree_skill_array(locked_spec->'factors'))
    $$
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_skill_ids(graph jsonb, design jsonb) RETURNS uuid[]
    LANGUAGE sql IMMUTABLE AS $$
      WITH nodes AS (SELECT value AS n FROM jsonb_array_elements(asaree_skill_array(graph->'nodes'))),
      bindings AS (
        SELECT n#>>'{data,factor_bindings,config}' AS name FROM nodes WHERE n->>'type'='skill'
        UNION SELECT n#>>'{data,factor_bindings,config.skill_id}' FROM nodes WHERE n->>'type'='skill'
        UNION SELECT n#>>'{data,factor_bindings,skill_selection}' FROM nodes
      ), levels AS (
        SELECT level FROM jsonb_array_elements(asaree_skill_array(design->'factors')) AS factor,
          LATERAL jsonb_array_elements(asaree_skill_array(factor->'levels')) AS level
        WHERE factor->>'level_type' IN ('skill_selection','skill_toggle','skill_config')
           OR factor->>'name' IN (SELECT name FROM bindings WHERE name IS NOT NULL)
      ), candidates AS (
        SELECT n#>>'{data,config,skill_id}' AS value FROM nodes WHERE n->>'type'='skill'
        UNION SELECT choice#>>'{}' FROM nodes,
          LATERAL jsonb_array_elements(asaree_skill_array(n#>'{data,skill_selection}')) choice
        UNION SELECT CASE WHEN jsonb_typeof(level)='string' THEN level#>>'{}'
                          WHEN jsonb_typeof(level)='object' THEN level->>'skill_id' END FROM levels
        UNION SELECT choice#>>'{}' FROM levels,
          LATERAL jsonb_array_elements(asaree_skill_array(level)) choice
      ) SELECT coalesce(array_agg(DISTINCT asaree_skill_uuid(value))
               FILTER (WHERE asaree_skill_uuid(value) IS NOT NULL), ARRAY[]::uuid[]) FROM candidates
    $$
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_skill_factor_graph(graph jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
      SELECT jsonb_build_object('nodes', coalesce(jsonb_agg(jsonb_build_object(
        'type', n->'type', 'data', jsonb_build_object('factor_bindings', n#>'{data,factor_bindings}'))), '[]'::jsonb))
      FROM jsonb_array_elements(asaree_skill_array(graph->'nodes')) n
    $$
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_skill_run_ids(graph jsonb, design jsonb, choices jsonb) RETURNS uuid[]
    LANGUAGE sql IMMUTABLE AS $$
      SELECT asaree_skill_ids(graph, jsonb_build_object('factors',
        asaree_skill_array(design->'factors') || coalesce((
          SELECT jsonb_agg(jsonb_build_object('name', key, 'levels', jsonb_build_array(value)))
          FROM jsonb_each(CASE WHEN jsonb_typeof(choices)='object' THEN choices ELSE '{}'::jsonb END)
        ), '[]'::jsonb)))
    $$
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_skill_projection(graph jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
      SELECT coalesce(jsonb_agg(jsonb_build_array(n->>'id', n->>'type',
        CASE WHEN n->>'type'='skill' THEN n#>'{data,config,skill_id}' END,
        n#>'{data,skill_selection}', n#>'{data,factor_bindings,config}',
        n#>'{data,factor_bindings,config.skill_id}', n#>'{data,factor_bindings,skill_selection}')
        ORDER BY n->>'id'), '[]'::jsonb)
      FROM jsonb_array_elements(asaree_skill_array(graph->'nodes')) n
      WHERE n->>'type'='skill' OR n#>'{data,skill_selection}' IS NOT NULL
        OR n#>'{data,factor_bindings,skill_selection}' IS NOT NULL
    $$
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_retain_skill_ids(scope text, parent uuid, desired uuid[]) RETURNS void
    LANGUAGE plpgsql AS $$ DECLARE identifier uuid; existing uuid[]; column_name text;
    BEGIN
      IF scope NOT IN ('protocol','publication','run','experiment','design') THEN
        RAISE EXCEPTION 'Invalid skill reference scope';
      END IF;
      column_name := scope || '_id';
      EXECUTE format('SELECT coalesce(array_agg(skill_id), ARRAY[]::uuid[]) FROM skill_references WHERE %I=$1',
                      column_name) INTO existing USING parent;
      -- Attachment and ASAREE deletion use the same transaction locks in this
      -- database, even though the registered payload lives in Motoro's database.
      FOR identifier IN SELECT value FROM unnest(desired) value
                        WHERE NOT value=ANY(existing) ORDER BY value LOOP
        PERFORM pg_advisory_xact_lock(1936419180, hashtext(identifier::text));
      END LOOP;
      EXECUTE format('INSERT INTO skill_references(id, skill_id, %I)
        SELECT gen_random_uuid(), value, $1 FROM unnest($2::uuid[]) value ON CONFLICT DO NOTHING', column_name)
        USING parent, desired;
      EXECUTE format('DELETE FROM skill_references WHERE %I=$1 AND NOT skill_id=ANY($2::uuid[])', column_name)
        USING parent, desired;
    END $$
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION asaree_refresh_skill_references() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE graph jsonb := '{}'::jsonb; design jsonb := '{}'::jsonb; item record;
    BEGIN
      IF TG_TABLE_NAME='protocols' THEN
        IF TG_OP='UPDATE' AND NEW.experiment_id IS NOT DISTINCT FROM OLD.experiment_id
           AND asaree_skill_projection(NEW.graph)=asaree_skill_projection(OLD.graph) THEN RETURN NEW; END IF;
        -- Serialize legacy bindings against edits to their experiment's levels.
        SELECT asaree_skill_design(design_spec, locked_design_spec) INTO design
          FROM research_experiments WHERE id=NEW.experiment_id FOR NO KEY UPDATE;
        PERFORM asaree_retain_skill_ids('protocol', NEW.id, asaree_skill_ids(NEW.graph, design));
      ELSIF TG_TABLE_NAME='research_experiments' THEN
        IF TG_OP='UPDATE' AND NEW.design_spec IS NOT DISTINCT FROM OLD.design_spec
           AND NEW.locked_design_spec IS NOT DISTINCT FROM OLD.locked_design_spec THEN RETURN NEW; END IF;
        design := asaree_skill_design(NEW.design_spec, NEW.locked_design_spec);
        PERFORM asaree_retain_skill_ids('experiment', NEW.id, asaree_skill_ids(graph, design));
        FOR item IN SELECT id, p.graph FROM protocols p WHERE experiment_id=NEW.id LOOP
          PERFORM asaree_retain_skill_ids('protocol', item.id, asaree_skill_ids(item.graph, design));
        END LOOP;
      ELSIF TG_TABLE_NAME='protocol_revisions' THEN
        PERFORM asaree_retain_skill_ids('publication', NEW.id,
          asaree_skill_ids(NEW.graph, coalesce(NEW.experiment_snapshot->'design_spec',
            (SELECT design_spec FROM experiment_design_revisions WHERE id=NEW.design_revision_id))));
      ELSIF TG_TABLE_NAME='experiment_design_revisions' THEN
        PERFORM id FROM research_experiments WHERE id=NEW.experiment_id FOR NO KEY UPDATE;
        SELECT jsonb_build_object('nodes', coalesce(jsonb_agg(n), '[]'::jsonb)) INTO graph
          FROM protocols p, LATERAL jsonb_array_elements(asaree_skill_array(p.graph->'nodes')) n
          WHERE p.experiment_id=NEW.experiment_id;
        PERFORM asaree_retain_skill_ids('design', NEW.id,
          asaree_skill_ids(asaree_skill_factor_graph(graph), NEW.design_spec));
      ELSIF TG_TABLE_NAME='protocol_runs' THEN
        SELECT p.graph, coalesce(p.experiment_snapshot->'design_spec', d.design_spec) INTO graph, design
          FROM protocol_revisions p LEFT JOIN experiment_design_revisions d ON d.id=p.design_revision_id
          WHERE p.id=NEW.protocol_revision_id;
        IF graph IS NULL THEN
          SELECT p.graph, asaree_skill_design(e.design_spec, e.locked_design_spec) INTO graph, design FROM protocols p
            LEFT JOIN research_experiments e ON e.id=p.experiment_id WHERE p.id=NEW.protocol_id;
        END IF;
        PERFORM asaree_retain_skill_ids('run', NEW.id, asaree_skill_run_ids(graph, design, NEW.factor_values));
      END IF;
      RETURN NEW;
    END $$
    """)
    for table, updates in (("protocols", "graph, experiment_id"),
                           ("research_experiments", "design_spec, locked_design_spec"),
                           ("experiment_design_revisions", "design_spec")):
        op.execute(f"DROP TRIGGER IF EXISTS retain_registered_skills ON {table}")
        op.execute(f"CREATE TRIGGER retain_registered_skills AFTER INSERT OR UPDATE OF {updates} ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION asaree_refresh_skill_references()")
    for table in ("protocol_revisions", "protocol_runs"):
        op.execute(f"DROP TRIGGER IF EXISTS retain_registered_skills ON {table}")
        op.execute(f"CREATE TRIGGER retain_registered_skills AFTER INSERT ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION asaree_refresh_skill_references()")
    # Backfill identities even when a source has already been deleted. Audit
    # against Motoro separately; never invent or rewrite historical content.
    op.execute("""SELECT asaree_retain_skill_ids('experiment', id,
                    asaree_skill_ids('{}', asaree_skill_design(design_spec, locked_design_spec)))
                  FROM research_experiments""")
    op.execute("""SELECT asaree_retain_skill_ids('protocol', p.id,
                    asaree_skill_ids(p.graph, asaree_skill_design(e.design_spec, e.locked_design_spec)))
                  FROM protocols p LEFT JOIN research_experiments e ON e.id=p.experiment_id""")
    op.execute("""SELECT asaree_retain_skill_ids('publication', id,
                    asaree_skill_ids(graph, coalesce(experiment_snapshot->'design_spec',
                      (SELECT design_spec FROM experiment_design_revisions WHERE id=design_revision_id))))
                  FROM protocol_revisions""")
    op.execute("""SELECT asaree_retain_skill_ids('design', d.id, asaree_skill_ids(
                    asaree_skill_factor_graph((SELECT jsonb_build_object('nodes',
                      coalesce(jsonb_agg(n), '[]'::jsonb)) FROM (
                        SELECT p.graph FROM protocols p WHERE p.experiment_id=d.experiment_id
                        UNION ALL SELECT v.graph FROM protocol_revisions v WHERE v.design_revision_id=d.id
                      ) sources, LATERAL jsonb_array_elements(asaree_skill_array(sources.graph->'nodes')) n)),
                    d.design_spec)) FROM experiment_design_revisions d""")
    op.execute("""SELECT asaree_retain_skill_ids('run', r.id,
                    asaree_skill_run_ids(coalesce(v.graph,p.graph),
                      CASE WHEN v.id IS NOT NULL THEN coalesce(v.experiment_snapshot->'design_spec', d.design_spec)
                           ELSE asaree_skill_design(e.design_spec, e.locked_design_spec) END, r.factor_values))
                  FROM protocol_runs r LEFT JOIN protocol_revisions v ON v.id=r.protocol_revision_id
                  LEFT JOIN experiment_design_revisions d ON d.id=v.design_revision_id
                  JOIN protocols p ON p.id=r.protocol_id LEFT JOIN research_experiments e ON e.id=p.experiment_id""")


def downgrade() -> None:
    for table in SCOPES.values():
        op.execute(f"DROP TRIGGER IF EXISTS retain_registered_skills ON {table}")
    op.execute("DROP FUNCTION IF EXISTS asaree_refresh_skill_references()")
    op.execute("DROP FUNCTION IF EXISTS asaree_retain_skill_ids(text, uuid, uuid[])")
    op.execute("DROP FUNCTION IF EXISTS asaree_skill_projection(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS asaree_skill_run_ids(jsonb, jsonb, jsonb)")
    op.execute("DROP FUNCTION IF EXISTS asaree_skill_factor_graph(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS asaree_skill_ids(jsonb, jsonb)")
    op.execute("DROP FUNCTION IF EXISTS asaree_skill_design(jsonb, jsonb)")
    op.execute("DROP FUNCTION IF EXISTS asaree_skill_array(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS asaree_skill_uuid(text)")
    op.drop_table("skill_references", if_exists=True)
