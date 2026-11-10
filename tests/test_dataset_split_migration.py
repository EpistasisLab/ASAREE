"""Legacy evidence checks independent of a running database."""

import asyncio
import importlib
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from asaree.migrations.dataset_splits import legacy_artifact
from asaree.security.hashing import sha256_file


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_split_migration_supports_operations_without_environment_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, direction: str
) -> None:
    """Result-test schema setup runs revisions with only an Operations context."""
    from asaree import config
    from asaree.migrations import dataset_splits

    migration = importlib.import_module("asaree.migrations.versions.94b6c8d0e2f4_immutable_dataset_splits")
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(dataset_storage_dir=str(tmp_path)))
    monkeypatch.setattr(migration, "has_column", lambda *args: True)
    monkeypatch.setattr(migration, "has_table", lambda *args: True)
    monkeypatch.setattr(migration, "create_foreign_key", lambda *args: None)
    monkeypatch.setattr(migration, "drop_column", lambda *args: None)
    for name in ("create_table", "create_index", "add_column", "drop_constraint", "drop_table"):
        monkeypatch.setattr(Operations, name, lambda *args, **kwargs: None)
    preserved = []
    restored = []
    monkeypatch.setattr(dataset_splits, "preserve_legacy_splits", lambda bind, root: preserved.append(root))
    monkeypatch.setattr(dataset_splits, "restore_legacy_paths", lambda bind, root: restored.append(root))
    with Operations.context(MigrationContext.configure(dialect_name="postgresql")):
        getattr(migration, direction)()
    assert preserved == ([tmp_path] if direction == "upgrade" else [])
    assert restored == ([tmp_path] if direction == "downgrade" else [])


def test_legacy_artifact_preserves_bytes_and_computes_only_missing_hash(tmp_path: Path) -> None:
    path = tmp_path / "train.parquet"
    original = b"legacy parquet bytes\x00\xff"
    path.write_bytes(original)
    artifact = legacy_artifact(
        dataset_id=uuid.uuid4(), path=str(path), expected_hash=None, format="parquet", root=tmp_path
    )
    assert artifact["sha256"] == sha256_file(path)
    assert artifact["status"] == "ready"
    assert artifact["storage_key"] == "train.parquet"
    assert artifact["byte_size"] == len(original)
    assert path.read_bytes() == original


def test_legacy_artifact_reports_missing_and_mismatched_files(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "test.parquet"
    expected = "0" * 64
    missing = legacy_artifact(
        dataset_id=uuid.uuid4(), path=str(path), expected_hash=expected, format="parquet", root=tmp_path
    )
    assert missing["status"] == "missing" and missing["sha256"] == expected
    path.write_bytes(b"changed")
    mismatch = legacy_artifact(
        dataset_id=uuid.uuid4(), path=str(path), expected_hash=expected, format="parquet", root=tmp_path
    )
    assert mismatch["status"] == "hash_mismatch" and mismatch["sha256"] == expected
    assert "missing" in caplog.text and "hash_mismatch" in caplog.text


def test_legacy_artifact_rejects_unmanaged_path(tmp_path: Path) -> None:
    (tmp_path / "raw.csv").write_bytes(b"x\n1\n")
    with pytest.raises(ValueError, match="outside dataset storage"):
        legacy_artifact(dataset_id=uuid.uuid4(), path=str(tmp_path / "raw.csv"), expected_hash=None,
                        format="csv", root=tmp_path / "managed")


def test_missing_external_legacy_artifact_retains_evidence(tmp_path: Path) -> None:
    missing = tmp_path / "pytest-380" / "test_parent_selection_expands_0" / "batch.csv"
    artifact = legacy_artifact(
        dataset_id=uuid.uuid4(), path=str(missing), expected_hash="0" * 64,
        format="csv", root=tmp_path / "managed",
    )
    assert artifact["status"] == "missing"
    assert artifact["sha256"] == "0" * 64
    assert artifact["legacy_path"] == str(missing)
    assert not Path(artifact["storage_key"]).is_absolute()


@pytest.mark.skipif(
    not os.environ.get("ASAREE_MIGRATIONS_TEST_DATABASE_URL"), reason="Migration PostgreSQL not configured"
)
@pytest.mark.parametrize("external_source", [False, True])
async def test_legacy_split_upgrade_rerun_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, external_source: bool
) -> None:
    from asaree import config
    from asaree.migrations import downgrade, stamp, upgrade

    admin_url = os.environ["ASAREE_MIGRATIONS_TEST_DATABASE_URL"]
    db_name = f"split_migration_{uuid.uuid4().hex}"
    url = f"{admin_url.rpartition('/')[0]}/{db_name}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    engine = create_async_engine(url)
    settings = config.AsareeSettings(_env_file=None, dataset_storage_dir=str(tmp_path))
    monkeypatch.setattr(config, "_instance", settings)
    dataset_id, owner_id = uuid.uuid4(), uuid.uuid4()
    source, train, test = (tmp_path / name for name in ("raw.csv", "train.parquet", "test.parquet"))
    if external_source:
        source = tmp_path.parent / f"pytest-gone-{uuid.uuid4().hex}" / "batch.csv"
    else:
        source.write_bytes(b"x\n1\n2\n")
    train.write_bytes(b"unchanged train bytes")
    # A missing test file is preserved as missing evidence, with its recorded hash.
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await asyncio.to_thread(upgrade, url, "a4b5c6d7e8f0")
        async with engine.begin() as conn:
            await conn.execute(text("""INSERT INTO users (id, email, hashed_password, display_name)
                VALUES (:id, 'migration@example.com', 'unused', 'Migration')"""), {"id": owner_id})
            await conn.execute(text("""INSERT INTO registered_datasets
                (id, owner_id, name, raw_path, train_path, test_path, test_sha256,
                 split_method, split_group_column, split_test_size, split_seed, target_column)
                VALUES (:id, :owner, 'legacy', :raw, :train, :test, :hash, 'quick', 'cohort', 0.25, 7, 'label')"""),
                dict(id=dataset_id, owner=owner_id, raw=str(source), train=str(train), test=str(test), hash="0" * 64))
        await asyncio.to_thread(upgrade, url, "head")
        async with engine.connect() as conn:
            split = (await conn.execute(text("SELECT * FROM dataset_splits"))).mappings().one()
            assert split["dataset_id"] == dataset_id and split["legacy"]
            assert split["source_sha256"] == (None if external_source else sha256_file(source))
            assert split["method"] == "quick" and split["seed"] == 7 and split["group_column"] == "cohort"
            assert split["target_column"] is None and split["strategy"] is None and split["stratify_column"] is None
            artifacts = (await conn.execute(text("SELECT * FROM dataset_artifacts"))).mappings().all()
            assert len(artifacts) == 3
            if external_source:
                original = next(a for a in artifacts if a["legacy_path"] == str(source))
                assert original["status"] == "missing" and original["sha256"] is None
                assert not Path(original["storage_key"]).is_absolute()
            missing = next(a for a in artifacts if a["storage_key"] == "test.parquet")
            assert missing["status"] == "missing" and missing["sha256"] == "0" * 64
            selected = (await conn.execute(text("SELECT current_split_id FROM registered_datasets"))).scalar_one()
            assert selected == split["id"]
        # A lost stamp must neither duplicate records nor require removed legacy columns.
        await asyncio.to_thread(stamp, url, "a4b5c6d7e8f0")
        await asyncio.to_thread(upgrade, url, "head")
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM dataset_splits"))).scalar_one() == 1
            assert (await conn.execute(text("SELECT count(*) FROM dataset_artifacts"))).scalar_one() == 3
        await asyncio.to_thread(downgrade, url, "a4b5c6d7e8f0")
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT * FROM registered_datasets"))).mappings().one()
            assert row["raw_path"] == str(source) and row["train_path"] == str(train) and row["test_path"] == str(test)
            assert row["split_seed"] == 7
        await asyncio.to_thread(stamp, url, "94b6c8d0e2f4")
        await asyncio.to_thread(downgrade, url, "a4b5c6d7e8f0")
        if not external_source:
            assert source.read_bytes() == b"x\n1\n2\n"
        else:
            assert not source.exists()
        assert train.read_bytes() == b"unchanged train bytes"
        assert not test.exists()
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        await admin.dispose()


@pytest.mark.skipif(
    not os.environ.get("ASAREE_MIGRATIONS_TEST_DATABASE_URL"), reason="Migration PostgreSQL not configured"
)
async def test_migrated_split_can_be_published_and_executed_without_rewriting_provenance(tmp_path, monkeypatch):
    import pandas as pd
    from sqlalchemy.ext.asyncio import AsyncSession

    from asaree import config
    from asaree.migrations import upgrade
    from asaree.models.dataset import RegisteredDataset
    from asaree.services import protocol_execution as execution
    from asaree.services.dataset_inputs import attempt_workspace_id, prepare_attempt_graph
    from asaree.services.dataset_workspaces import head_data_locator
    from asaree.services.protocol_revisions import publish_protocol
    from asaree.services.protocol_runs import create_protocol_run
    from asaree.services.protocols import create_protocol

    admin_url = os.environ["ASAREE_MIGRATIONS_TEST_DATABASE_URL"]
    db_name = f"split_publication_{uuid.uuid4().hex}"
    url = f"{admin_url.rpartition('/')[0]}/{db_name}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    engine = create_async_engine(url)
    settings = config.AsareeSettings(_env_file=None, dataset_storage_dir=str(tmp_path))
    monkeypatch.setattr(config, "_instance", settings)
    monkeypatch.setattr("asaree_workspace_core.workspace.WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    dataset_id, owner_id = uuid.uuid4(), uuid.uuid4()
    raw, train, test = (tmp_path / name for name in ("raw.csv", "train.parquet", "test.parquet"))
    raw.write_bytes(b"x,y\n1,0\n2,1\n")
    for path in (train, test):
        pd.DataFrame({"x": [1, 2], "y": [0, 1]}).to_parquet(path)
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{db_name}"'))
        await asyncio.to_thread(upgrade, url, "a4b5c6d7e8f0")
        async with engine.begin() as connection:
            await connection.execute(text("""INSERT INTO users (id, email, hashed_password, display_name)
                VALUES (:id, 'migration@example.com', 'unused', 'Migration')"""), {"id": owner_id})
            await connection.execute(text("""INSERT INTO registered_datasets
                (id, owner_id, name, raw_path, train_path, test_path, target_column)
                VALUES (:id, :owner, 'legacy', :raw, :train, :test, 'y')"""),
                dict(id=dataset_id, owner=owner_id, raw=str(raw), train=str(train), test=str(test)))
        await asyncio.to_thread(upgrade, url, "head")
        async with AsyncSession(engine, expire_on_commit=False) as db:
            dataset = await db.get(RegisteredDataset, dataset_id)
            split = dataset.current_split
            assert split.legacy and split.target_column is None
            graph = {"nodes": [
                {"id": "data", "type": "dataset", "data": {"config": {
                    "dataset_id": str(dataset_id), "dataset_name": "legacy",
                }}},
                {"id": "agent", "type": "agent", "data": {}},
            ], "edges": [{"source": "data", "target": "agent", "targetHandle": "dataset"}]}
            protocol = await create_protocol(db, name="Migrated publication", owner_id=owner_id, graph=graph)
            publication = await publish_protocol(db, protocol, owner_id=owner_id)
            run = await create_protocol_run(db, protocol_id=protocol.id, owner_id=owner_id,
                                            protocol_revision_id=publication.id)
            dataset.target_column = "later"
            await db.flush()
            workspace = attempt_workspace_id(None, run.id)
            for _ in range(2):
                prepared = await prepare_attempt_graph(db, run, publication.graph)
                opened = await execution._resolve_node_dataset(prepared, "agent", workspace, owner_id)
                assert opened.seeded_names == ("legacy",)
                assert head_data_locator(workspace)[1] == "y"
            assert publication.dataset_inputs["inputs"][0]["target_column"] == "y"
            await db.refresh(split)
            assert split.legacy and split.target_column is None and split.method is None and split.seed is None
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        await admin.dispose()
