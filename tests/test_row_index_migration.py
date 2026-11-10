"""Offline row-index comparison, retirement and legacy retention contracts."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager, closing
from dataclasses import replace

import pytest
from test_indexed_row_source import catalog
from test_result_reads import results_db as _results_db

from asaree import row_index_migration as migration
from asaree.models.dataset import RegisteredDataset
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.result_job import ResultJob
from asaree.services.dataset_row_retention import required_by_job
from asaree.services.indexed_row_source import read_indexed_row_source

results_db = _results_db


def legacy_index(directory, raw=b'x,y\n001,a\n002,b\n', rows=None):
    digest = hashlib.sha256(raw).hexdigest()
    path = directory / f'{digest}.sqlite'
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        connection.execute('CREATE TABLE rows(row_index INTEGER PRIMARY KEY, data TEXT NOT NULL)')
        rows = rows if rows is not None else [['001', 'a'], ['002', 'b']]
        connection.executemany('INSERT INTO metadata VALUES (?,?)', [
            ('sha256', digest), ('columns', json.dumps(['x', 'y'])), ('count', str(len(rows))),
        ])
        connection.executemany('INSERT INTO rows VALUES (?,?)', [(i, json.dumps(row)) for i, row in enumerate(rows)])
    return path


def test_inventory_preserves_unknowns_and_reports_inconsistent_metadata(tmp_path):
    path = legacy_index(tmp_path)
    lock = path.with_suffix('.lock')
    lock.write_bytes(b'')
    unknown = tmp_path / 'unknown.building'
    unknown.write_bytes(b'partial')
    before = {file: file.read_bytes() for file in (path, lock, unknown)}
    files = migration.inventory_files([tmp_path])
    assert [entry['status'] for entry in files].count('indexed') == 1
    assert [entry['status'] for entry in files].count('retained_unknown') == 2
    assert all(file.read_bytes() == content for file, content in before.items())
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE metadata SET value='wrong' WHERE key='sha256'")
    entry = next(entry for entry in migration.inventory_files([tmp_path]) if entry['path'] == str(path))
    assert entry['status'] == 'inconsistent'


def test_retirement_preflights_all_conflicts_and_renames_only_verified_indexes(tmp_path):
    first = legacy_index(tmp_path)
    second = legacy_index(tmp_path, b'x,y\n003,c\n')
    report = dict(files=migration.inventory_files([tmp_path]), problems=[], sources=[
        dict(status='verified', sha256=first.stem), dict(status='verified', sha256=second.stem),
    ])
    second.with_suffix('.sqlite.retired').touch()
    with pytest.raises(ValueError, match='already exists'):
        migration.retire_verified(report)
    assert first.exists() and second.exists()
    second.with_suffix('.sqlite.retired').unlink()
    first.write_bytes(first.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='changed after verification'):
        migration.retire_verified(report)
    assert first.exists() and second.exists()


def test_duplicate_legacy_directories_are_deduplicated_before_retirement(tmp_path):
    path = legacy_index(tmp_path)
    files = migration.inventory_files([tmp_path, tmp_path, tmp_path / '.'])
    assert len(files) == 1
    report = dict(files=files * 2, problems=[], sources=[dict(status='verified', sha256=path.stem)])
    with pytest.raises(ValueError, match='Duplicate'):
        migration.retire_verified(report)
    assert path.exists() and not path.with_suffix('.sqlite.retired').exists()
    report['files'] = files
    migration.retire_verified(report)
    assert not path.exists() and path.with_suffix('.sqlite.retired').exists()


async def test_rebuild_compares_all_rows_and_migrates_active_job_progress(results_db, tmp_path, monkeypatch):
    db, scope, _, user = results_db
    originals = tmp_path / 'originals'
    originals.mkdir()
    dataset, _ = await catalog(db, user, originals)
    indexes = tmp_path / 'indexes'
    indexes.mkdir()
    legacy = legacy_index(indexes)
    legacy.with_suffix('.lock').touch()
    # Unrelated unavailable registration must not be selected by a None hash
    # from a lock file or a historical terminal job.
    unrelated = RegisteredDataset(name=str(uuid.uuid4()), owner_id=user.id, raw_sha256=None)
    db.add(unrelated)
    old = dict(dataset_id=str(dataset.id), sha256=dataset.raw_sha256, columns=['x', 'y'], count=2)
    job = ResultJob(owner_id=user.id, experiment_id=scope.experiment_id, protocol_id=scope.protocol_id,
                    protocol_revision_id=scope.protocol_revision_id, design_revision_id=scope.design_revision_id,
                    kind='plan', status='queued', request_key='migration', params={}, progress={'source': old})
    db.add(job)
    await db.flush()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(migration, 'get_session', session)
    async def job_scope(_db, _job):
        return replace(scope, row_plan={'driver_dataset_id': str(dataset.id)})
    from asaree.services import result_jobs
    monkeypatch.setattr(result_jobs, 'job_scope', job_scope)
    inventory = await migration.run_migration('inventory', [indexes])
    assert len(inventory['sources']) == 1 and inventory['sources'][0]['status'] == 'original_verified'
    assert job.progress['source'] == old
    rebuilt = await migration.run_migration('rebuild', [indexes])
    assert not rebuilt['problems'] and rebuilt['sources'][0]['status'] == 'verified'
    assert 'columns' not in job.progress['source'] and job.progress['source']['parser_version']
    assert legacy.exists()
    report = await migration.run_migration('retire', [indexes])
    assert not report['problems'] and legacy.with_suffix('.sqlite.retired').exists()


async def test_inconsistent_sqlite_is_reported_without_retirement(results_db, tmp_path):
    db, _, _, user = results_db
    dataset, _ = await catalog(db, user, tmp_path)
    legacy = legacy_index(tmp_path, rows=[['changed', 'a'], ['002', 'b']])
    entries = migration.inventory_files([tmp_path])
    entries = [entry for entry in entries if entry['path'] == str(legacy)]
    result = await migration.verify_source(db, dataset, entries, postgres=True)
    assert result['status'] == 'unavailable' and 'values disagree' in result['error']
    assert legacy.exists()


@pytest.mark.parametrize('status,protected', [
    ('pending', True), ('queued', True), ('awaiting_confirmation', True),
    ('cancelled', False), ('expired', False), ('completed', False), ('failed', False),
])
@pytest.mark.parametrize('saved', [False, True])
async def test_only_resumable_legacy_jobs_protect_dataset(results_db, tmp_path, status, protected, saved):
    db, scope, _, user = results_db
    dataset, _ = await catalog(db, user, tmp_path)
    publication = await db.get(ProtocolRevision, scope.protocol_revision_id)
    publication.graph = {'nodes': [
        {'id': 'source', 'type': 'dataset', 'data': {'config': {'dataset_id': str(dataset.id)}}},
        {'id': 'agent', 'type': 'agent', 'data': {}},
    ], 'edges': [{'source': 'source', 'target': 'agent', 'targetHandle': 'dataset',
                  'data': {'dataset_input': {'mode': 'per_row', 'columns': ['x']}}}]}
    progress = {'source': (await read_indexed_row_source(
        db, dataset_id=dataset.id, owner_id=user.id,
    )).descriptor()} if saved else {}
    db.add(ResultJob(owner_id=user.id, experiment_id=scope.experiment_id, protocol_id=scope.protocol_id,
                     protocol_revision_id=scope.protocol_revision_id, design_revision_id=scope.design_revision_id,
                     kind='plan', status=status, request_key='retention', params={}, progress=progress))
    await db.flush()
    assert await required_by_job(db, dataset.id, user.id) is protected
