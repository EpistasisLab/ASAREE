"""Migration graph checks that require no database server."""

import io
import warnings

from alembic import command
from alembic.script import ScriptDirectory

from asaree.migrations import MIGRATIONS_DIR, make_config


def test_migration_revisions_are_unique_and_have_one_head() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        script = ScriptDirectory(str(MIGRATIONS_DIR))
        assert script.get_current_head() is not None
        revisions = list(script.walk_revisions())
    assert len({revision.revision for revision in revisions}) == len(revisions)


def test_upgrade_from_factor_index_revision_creates_stats_tables() -> None:
    output = io.StringIO()
    config = make_config("postgresql+asyncpg://localhost/unused")
    config.output_buffer = output
    command.upgrade(config, "93a4b5c6d7e8:head", sql=True)
    # The table and index may already exist from the original, colliding ID.
    assert "CREATE TABLE IF NOT EXISTS stats_analyses" in output.getvalue()
    assert "CREATE INDEX IF NOT EXISTS ix_stats_analyses_experiment_id" in output.getvalue()


def test_custom_metric_preview_migration_keeps_numeric_filter_and_defers_backfill() -> None:
    output = io.StringIO()
    config = make_config("postgresql+asyncpg://localhost/unused")
    config.output_buffer = output
    command.upgrade(config, "8de692fb1681:head", sql=True)
    sql = output.getvalue()
    assert "'metric_projection_version', 2" in sql
    assert "jsonb_typeof(o->'value') IN ('number', 'boolean')" in sql
    assert "o#>>'{producer,producer_id}' IN" not in sql
    assert "ix_protocol_runs_metric_preview_pending" in sql
    assert "UPDATE protocol_runs" not in sql
