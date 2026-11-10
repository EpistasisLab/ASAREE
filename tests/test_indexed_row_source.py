"""Exact CSV fidelity, bounded reads, transactional ingestion and legacy jobs."""

import asyncio
import hashlib
import io
import os
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_result_reads import results_db as _results_db

from asaree.models.dataset import RegisteredDataset
from asaree.models.dataset_row_index import DatasetIndexedRow, DatasetRowIndex
from asaree.models.experiment import ResearchExperiment
from asaree.models.protocol import Protocol
from asaree.models.result_job import ResultJob
from asaree.models.user import User
from asaree.services import indexed_row_source as indexing
from asaree.services.dataset_row_csv import DatasetRowCsvError, project_row, read_row_source
from asaree.services.indexed_row_source import (
    BATCH_SIZE,
    PARSER_VERSION,
    IndexedRowSource,
    LoadedRows,
    csv_records,
    decode_strings,
    encode_strings,
    read_indexed_row_source,
    verification_scope,
    verify_original,
)
from asaree.services.result_jobs import restored_source
from asaree.services.row_schema_reads import schema_columns, schema_payload

results_db = _results_db


def test_parser_and_binary_payloads_preserve_nul_unicode_multiline_and_empty_strings():
    raw = '\ufeffq\x00,回答,note\r\n"two\nlines",001,"x\x00y"\r\n,0,\r\n\r\n'.encode()
    records = list(csv_records(io.BytesIO(raw)))
    assert records == [('q\x00', '回答', 'note'), ('two\nlines', '001', 'x\x00y'), ('', '0', '')]
    for record in records:
        assert decode_strings(encode_strings(record)) == record
    source = IndexedRowSource(str(uuid.uuid4()), 'a' * 64, records[0], LoadedRows(2), uuid.uuid4(), uuid.uuid4())
    cache = schema_payload(source)
    assert 'columns' not in cache and '\x00' not in str(cache)
    assert schema_columns(cache) == list(records[0])
    assert source.descriptor() == dict(dataset_id=source.dataset_id, sha256='a' * 64,
                                       parser_version=PARSER_VERSION, count=2)


def test_verification_lease_avoids_rescans_only_within_one_job_tick(tmp_path, monkeypatch):
    path = tmp_path / 'source.csv'
    raw = b'x\n001\n'
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    original = Path.open
    reads = []
    def recording(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', recording)
    monkeypatch.setattr(indexing.tempfile, 'TemporaryFile', lambda: pytest.fail('ready verification must not copy'))
    with verification_scope():
        verify_original(str(path), digest, copy=False)
        verify_original(str(path), digest, copy=False)
        assert len(reads) == 1
        path.write_bytes(b'x\n002\n')
        with pytest.raises(DatasetRowCsvError, match='source_hash_mismatch'):
            verify_original(str(path), digest, copy=False)
    path.write_bytes(raw)
    with verification_scope():
        verify_original(str(path), digest, copy=False)
    verify_original(str(path), digest, copy=False)
    assert len(reads) == 6  # Four reads and two explicit writes.


async def test_sparse_batch_has_no_hidden_io_and_enforces_bounded_reads():
    source = IndexedRowSource(str(uuid.uuid4()), 'a' * 64, ('x',), LoadedRows(1000000), uuid.uuid4(), uuid.uuid4())
    db = AsyncMock()
    with pytest.raises(DatasetRowCsvError, match='asynchronous batch'):
        source.rows[5]
    with pytest.raises(ValueError, match='at most'):
        await source.fetch_rows(db, range(BATCH_SIZE + 1))
    db.execute.assert_not_called()
    db.execute.return_value = [(5, encode_strings(['001'])), (6, encode_strings(['002']))]
    window = await source.fetch_rows(db, [6, 5])
    assert window.rows[5] == ('001',) and window.rows[6] == ('002',)
    assert len(window.rows) == 1000000 and len(window.rows.values) == 2
    await window.fetch_rows(db, [5])
    assert db.execute.await_count == 1


async def catalog(db, user, tmp_path, raw=b'x,y\n001,a\n002,b\n'):
    path = tmp_path / f'{uuid.uuid4()}.csv'
    path.write_bytes(raw)
    dataset = RegisteredDataset(name=str(uuid.uuid4()), owner_id=user.id, raw_path=str(path),
                                raw_sha256=hashlib.sha256(raw).hexdigest())
    db.add(dataset)
    await db.flush()
    return dataset, path


async def test_postgres_fidelity_scoped_reads_and_source_changes(results_db, tmp_path, monkeypatch):
    db, _, _, user = results_db
    raw = '\ufeffq\x00,回答,note\n"two\nlines",001,"x\x00y"\n,0,\nlast,yes,🙂\n'.encode()
    dataset, path = await catalog(db, user, tmp_path, raw)
    source = await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=user.id)
    original = read_row_source(dataset_id=str(dataset.id), raw_path=str(path), raw_sha256=dataset.raw_sha256)
    loaded = await source.fetch_rows(db, [2, 0, 1])
    assert loaded.columns == original.columns
    assert [loaded.rows[i] for i in range(3)] == list(original.rows)
    assert project_row(loaded, row_index=0, columns=['note', 'q\x00'])['values']['note'] == 'x\x00y'
    dataset.row_schema = schema_payload(source)
    await db.flush()
    await db.refresh(dataset)
    assert schema_columns(dataset.row_schema) == list(source.columns)
    with monkeypatch.context() as patched:
        patched.setattr(indexing.tempfile, 'TemporaryFile', lambda: pytest.fail('ready reuse must not copy'))
        reused = await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=user.id)
    assert reused.index_id == source.index_id
    # Descriptor forgery cannot cross the registration/owner join.
    with pytest.raises(DatasetRowCsvError, match='inaccessible'):
        await replace(source, owner_id=uuid.uuid4()).fetch_rows(db, [0])
    with pytest.raises(DatasetRowCsvError, match='unavailable'):
        await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=uuid.uuid4())
    duplicate, _ = await catalog(db, user, tmp_path, raw)
    other = await read_indexed_row_source(db, dataset_id=duplicate.id, owner_id=user.id)
    assert other.index_id != source.index_id
    path.write_bytes(b'q,x\nchanged,1\n')
    with pytest.raises(DatasetRowCsvError, match='source_hash_mismatch'):
        await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=user.id)


async def test_bulk_ingestion_and_batch_reads_are_bounded(results_db, tmp_path, monkeypatch):
    db, _, _, user = results_db
    dataset, _ = await catalog(db, user, tmp_path, b'x,y\n' + b'prompt,001\n' * 2503)
    sizes = []
    connection = await db.connection()
    def observe(_connection, _cursor, statement, parameters, _context, executemany):
        if statement.startswith('INSERT INTO dataset_indexed_rows'):
            sizes.append(len(parameters) if executemany and isinstance(parameters[0], (tuple, list, dict))
                         else len(parameters) // 3)
    event.listen(connection.sync_connection, 'before_cursor_execute', observe)
    monkeypatch.setattr(Path, 'read_bytes', lambda *_: pytest.fail('must stream original bytes'))
    try:
        source = await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=user.id)
    finally:
        event.remove(connection.sync_connection, 'before_cursor_execute', observe)
    assert sizes == [1000, 1000, 503]
    loaded = await source.fetch_range(db, 2500, 2503)
    assert len(loaded.rows) == 2503 and len(loaded.rows.values) == 3 and loaded.rows[2502] == ('prompt', '001')


async def test_failed_ingestion_rolls_back_and_retry_rebuilds(results_db, tmp_path, monkeypatch):
    db, _, _, user = results_db
    dataset, _ = await catalog(db, user, tmp_path, b'x,y\n' + b'prompt,001\n' * 1001)
    original = indexing.next_batch
    calls = 0
    def interrupted(records, limit=BATCH_SIZE):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('interrupted after first bulk write')
        return original(records, limit)
    monkeypatch.setattr(indexing, 'next_batch', interrupted)
    with pytest.raises(RuntimeError, match='interrupted'):
        await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=user.id)
    assert await db.scalar(select(func.count()).select_from(DatasetRowIndex)) == 0
    assert await db.scalar(select(func.count()).select_from(DatasetIndexedRow)) == 0
    monkeypatch.setattr(indexing, 'next_batch', original)
    source = await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=user.id)
    assert len(source.rows) == 1001
    await db.execute(delete(DatasetIndexedRow).where(DatasetIndexedRow.index_id == source.index_id,
                                                    DatasetIndexedRow.row_index == 1000))
    with pytest.raises(DatasetRowCsvError, match='incomplete'):
        await source.fetch_rows(db, [1000])


async def test_saved_sqlite_job_restores_stable_identity_and_rejects_conflicts(results_db, tmp_path):
    db, scope, _, user = results_db
    dataset, path = await catalog(db, user, tmp_path)
    scope = replace(scope, row_plan={'driver_dataset_id': str(dataset.id)})
    old = dict(dataset_id=str(dataset.id), sha256=dataset.raw_sha256, columns=['x', 'y'], count=2)
    progress = {'source': old, 'validated': True, 'row': 1}
    restored = await restored_source(db, progress, scope, user.id)
    assert progress['source'] == restored.descriptor() and 'columns' not in progress['source']
    assert progress['row'] == 1 and progress['validated'] is True
    with pytest.raises(ValueError, match='inconsistent'):
        await restored_source(db, {'source': {**old, 'count': 3}}, scope, user.id)
    with pytest.raises(DatasetRowCsvError, match='unsupported'):
        await restored_source(db, {'source': {**old, 'parser_version': 'future'}}, scope, user.id)
    path.unlink()
    with pytest.raises(DatasetRowCsvError, match='cannot be read'):
        await restored_source(db, progress, scope, user.id)


async def test_concurrent_preparation_exposes_only_one_complete_committed_index(tmp_path, monkeypatch):
    url = os.environ.get('ASAREE_RESULTS_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set ASAREE_RESULTS_TEST_DATABASE_URL for PostgreSQL concurrency check')
    schema = 'row_index_test_' + uuid.uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        def migrate(connection):
            scripts = ScriptDirectory(str(Path(__file__).parents[1] / 'src/asaree/migrations'))
            with Operations.context(MigrationContext.configure(connection)):
                for revision in reversed(list(scripts.walk_revisions())):
                    revision.module.upgrade()
        await connection.run_sync(migrate)
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    gate, started = threading.Event(), threading.Event()
    original = indexing.next_batch
    calls = 0
    def paused(records, limit=BATCH_SIZE):
        nonlocal calls
        calls += 1
        if calls == 2:  # First thousand rows have been inserted, not committed.
            started.set()
            if not gate.wait(10):
                raise RuntimeError('concurrency test timed out')
        return original(records, limit)
    tasks = []
    try:
        async with sessions.begin() as db:
            user = User(email='row-index@example.test', hashed_password='x', display_name='Index test')
            db.add(user)
            await db.flush()
            dataset, _ = await catalog(db, user, tmp_path, b'x,y\n' + b'prompt,001\n' * 1001)
        monkeypatch.setattr(indexing, 'next_batch', paused)
        async def build():
            async with sessions.begin() as db:
                return await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=user.id)
        tasks.append(asyncio.create_task(build()))
        assert await asyncio.to_thread(started.wait, 10)
        tasks.append(asyncio.create_task(build()))
        await asyncio.sleep(0.05)
        assert not tasks[1].done()
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(DatasetRowIndex)) == 0
            assert await db.scalar(select(func.count()).select_from(DatasetIndexedRow)) == 0
        gate.set()
        first, second = await asyncio.gather(*tasks)
        assert first.index_id == second.index_id and len(second.rows) == 1001
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(DatasetRowIndex)) == 1
            assert await db.scalar(select(func.count()).select_from(DatasetIndexedRow)) == 1001
        # Pending preparation must not autoflush attached progress and hold a
        # job row lock while cancellation is trying to update that same job.
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        from asaree.services import result_jobs

        async with sessions.begin() as db:
            experiment = ResearchExperiment(name='cancel preview', owner_id=user.id, design_type='factorial')
            db.add(experiment)
            await db.flush()
            protocol = Protocol(name='cancel preview', graph={}, owner_id=user.id, experiment_id=experiment.id)
            db.add(protocol)
            await db.flush()
            job = ResultJob(owner_id=user.id, experiment_id=experiment.id, protocol_id=protocol.id,
                            kind='plan', status='pending', request_key='cancel', params={},
                            progress={'source': {**first.descriptor(), 'columns': list(first.columns)}})
            db.add(job)
        @asynccontextmanager
        async def session():
            async with sessions.begin() as db:
                yield db
        async def scope(*_):
            return SimpleNamespace(row_plan={'driver_dataset_id': str(dataset.id)})
        preparing, release = asyncio.Event(), asyncio.Event()
        async def prepare(db, job, _scope, source):
            assert not db.is_modified(job), 'prefetch dirtied the attached job before pending-step reads'
            await db.scalar(select(1))  # A real ORM query permits autoflush.
            preparing.set()
            await release.wait()
            job.progress = {'source': source.descriptor(), 'validated': True}
        monkeypatch.setattr(result_jobs, 'get_session', session)
        monkeypatch.setattr(result_jobs, 'job_scope', scope)
        monkeypatch.setattr(result_jobs, 'prepare_step', prepare)
        advancing = asyncio.create_task(result_jobs._advance_command(job.id))
        try:
            await asyncio.wait_for(preparing.wait(), 5)
            async def cancel():
                from sqlalchemy import update

                async with sessions.begin() as db:
                    await db.execute(update(ResultJob).where(ResultJob.id == job.id).values(status='cancelled'))
            await asyncio.wait_for(cancel(), 2)
        finally:
            release.set()
            await advancing
        async with sessions() as db:
            assert (await db.get(ResultJob, job.id)).status == 'cancelled'
        # The registration lock covers jobs created before source progress or
        # publication input-reference edges existed.
        from datetime import UTC, datetime

        from test_result_reads import scope as result_scope

        from asaree.models.protocol_revision import ProtocolRevision
        from asaree.services import datasets

        graph = {'nodes': [
            {'id': 'source', 'type': 'dataset', 'data': {'config': {'dataset_id': str(dataset.id)}}},
            {'id': 'agent', 'type': 'agent', 'data': {}},
        ], 'edges': [{'source': 'source', 'target': 'agent', 'targetHandle': 'dataset',
                      'data': {'dataset_input': {'mode': 'per_row', 'columns': ['x']}}}]}
        async with sessions.begin() as db:
            publication = ProtocolRevision(protocol_id=protocol.id, revision=1, graph=graph,
                                           published_at=datetime.now(UTC))
            db.add(publication)
        selected = result_scope(experiment_id=experiment.id, protocol_id=protocol.id,
                                protocol_revision_id=publication.id, design_revision_id=None,
                                graph=graph, row_plan={'driver_dataset_id': str(dataset.id)})
        recorded, commit_job = asyncio.Event(), asyncio.Event()
        async def record():
            async with sessions.begin() as db:
                intent = await result_jobs.create_job(db, selected, user.id, kind='plan',
                                                      request_key='race-delete', params={})
                recorded.set()
                await commit_job.wait()
                return intent.id
        async def delete_source():
            async with sessions.begin() as db:
                return await datasets.delete_dataset(db, dataset.id)
        recording = asyncio.create_task(record())
        await asyncio.wait_for(recorded.wait(), 5)
        deleting = asyncio.create_task(delete_source())
        try:
            await asyncio.sleep(0.05)
            assert not deleting.done(), 'deletion must wait for intent registration lock'
        finally:
            commit_job.set()
            intent_id = await recording
        with pytest.raises(datasets.DatasetValidationError, match='retained result job'):
            await deleting
        async with sessions.begin() as db:
            from sqlalchemy import update

            await db.execute(update(ResultJob).where(ResultJob.id == intent_id).values(status='cancelled'))
        # Terminal plans no longer protect the source. Avoid filesystem cleanup
        # in this database-only concurrency check.
        monkeypatch.setattr(datasets.shutil, 'rmtree', lambda *args, **kwargs: None)
        from asaree.services import managed_storage

        monkeypatch.setattr(managed_storage, 'workspace_references', lambda *_: False)
        assert await delete_source()
        async with sessions.begin() as db:
            with pytest.raises(ValueError, match='unavailable'):
                await result_jobs.create_job(db, selected, user.id, kind='plan', request_key='after-delete', params={})
    finally:
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
