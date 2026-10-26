"""Long prompt factors must not be stored directly in a B-tree index."""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import uuid

import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateIndex

from asaree.migrations import make_config
from asaree.models.factorial_cell import FactorialCell

_INDEX = "uq_factorial_cells_revision_factors"
_MIGRATION = "asaree.migrations.versions.93a4b5c6d7e8_hash_factorial_cell_factors"
_DB_URL = os.environ.get("ASAREE_MIGRATIONS_TEST_DATABASE_URL", "")


def _model_index_sql() -> str:
    index = next(index for index in FactorialCell.__table__.indexes if index.name == _INDEX)
    return str(CreateIndex(index).compile(dialect=postgresql.dialect()))


def test_factor_uniqueness_indexes_a_digest_instead_of_prompt_contents() -> None:
    sql = _model_index_sql()
    assert "(design_revision_id, md5(factor_values::text))" in sql
    assert "CREATE UNIQUE INDEX" in sql
    assert "WHERE factor_values IS NOT NULL AND factor_values <> '{}'::jsonb" in sql


def test_migration_creates_the_same_index_as_model_metadata() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        importlib.import_module(_MIGRATION).upgrade()
    assert f"DROP INDEX IF EXISTS {_INDEX};" in output.getvalue()
    assert _model_index_sql() + ";" in output.getvalue()


def test_standard_upgrade_from_main_runs_one_verified_migration() -> None:
    output = io.StringIO()
    config = make_config("postgresql+asyncpg://localhost/unused")
    config.output_buffer = output
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["93a4b5c6d7e8"]
    assert [revision.revision for revision in script.iterate_revisions("head", "8293a4b5c6d7")] == [
        "93a4b5c6d7e8"
    ]
    command.upgrade(config, "8293a4b5c6d7:head", sql=True)
    assert f"DROP INDEX IF EXISTS {_INDEX};" in output.getvalue()
    assert _model_index_sql() + ";" in output.getvalue()
    assert "pg_get_indexdef(indexrelid)" in output.getvalue()
    assert "UPDATE alembic_version SET version_num='93a4b5c6d7e8'" in output.getvalue()
    assert output.getvalue().count(f"DROP INDEX IF EXISTS {_INDEX};") == 1


@pytest.mark.skipif(not _DB_URL, reason="ASAREE_MIGRATIONS_TEST_DATABASE_URL is not set")
async def test_long_factors_remain_unique_after_migrating_existing_cells() -> None:
    """Temporary tables isolate this regression from the configured database's data."""
    engine = create_async_engine(_DB_URL)
    revision_id = uuid.uuid4()
    # Unlike repeated filler, this is incompressible enough to exceed the old
    # B-tree limit even when PostgreSQL compresses the JSONB value.
    prompt = "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(128))
    factors = {"model": "small", "prompt": prompt}
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TEMPORARY TABLE factorial_cells (
                    id uuid PRIMARY KEY, design_revision_id uuid NOT NULL, factor_values jsonb
                ) ON COMMIT DROP
            """))
            await conn.execute(text(f"""
                CREATE UNIQUE INDEX {_INDEX} ON factorial_cells (design_revision_id, factor_values)
                WHERE factor_values IS NOT NULL AND factor_values <> '{{}}'::jsonb
            """))

            async def insert(values: dict | None, revision: uuid.UUID = revision_id) -> None:
                await conn.execute(
                    text("INSERT INTO factorial_cells VALUES (:id, :revision, CAST(:factors AS jsonb))"),
                    {
                        "id": uuid.uuid4(),
                        "revision": revision,
                        "factors": json.dumps(values) if values is not None else None,
                    },
                )

            await insert({"model": "existing"})
            with pytest.raises(DBAPIError, match="index row"):
                async with conn.begin_nested():
                    await insert(factors)

            def migrate(sync_conn, direction: str = "upgrade") -> None:
                with Operations.context(MigrationContext.configure(sync_conn)):
                    getattr(importlib.import_module(_MIGRATION), direction)()

            # Upgrade and downgrade are safe to replay and restore main's
            # index without changing the existing cells.
            await conn.run_sync(migrate)
            await conn.run_sync(migrate)
            await conn.run_sync(migrate, "downgrade")
            await conn.run_sync(migrate, "downgrade")
            with pytest.raises(DBAPIError, match="index row"):
                async with conn.begin_nested():
                    await insert(factors)
            await conn.run_sync(migrate)
            await insert(factors)
            # Replaying an upgrade must also work with long factors stored.
            await conn.run_sync(migrate)
            stored = await conn.scalar(text("SELECT factor_values FROM factorial_cells WHERE factor_values ? 'prompt'"))
            assert stored == factors
            # JSON object key order cannot circumvent uniqueness.
            with pytest.raises(IntegrityError):
                async with conn.begin_nested():
                    await insert(dict(reversed(list(factors.items()))))
            await insert({**factors, "model": "large"})
            await insert(factors, uuid.uuid4())
            for _ in range(2):
                await insert(None)
                await insert({})
    finally:
        await engine.dispose()
