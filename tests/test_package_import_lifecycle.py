"""Real cross-database crash/retry and cancel/cleanup boundaries; CI PostgreSQL."""
import io
import os
import shutil
import tarfile
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from motoro.models.mcp_server import MCPServerConfig
from motoro.models.skill import Skill, SkillFile
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asaree.api import package_imports as api
from asaree.config import AsareeSettings
from asaree.models.experiment import ResearchExperiment
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.user import User
from asaree.services import import_visibility
from asaree.services import package_imports as imports
from asaree.services.experiment_packages import PackageWriter
from asaree.services.experiments import get_experiment
from asaree.services.package_reconstruction import Reconstruction, identity
from asaree.services.package_validation import Limits, digest, inspect


@pytest_asyncio.fixture
async def import_databases():
    urls = [os.environ.get('ASAREE_RESULTS_TEST_DATABASE_URL'), os.environ.get('ASAREE_DATABASE_URL')]
    if not all(urls):
        pytest.skip('Existing ASAREE and Motoro PostgreSQL required')
    engines, factories, schemas = [], [], []
    try:
        for number, url in enumerate(urls):
            schema = 'package_lifecycle_' + uuid.uuid4().hex
            engine = create_async_engine(url, connect_args={'server_settings': {
                'search_path': schema, 'lock_timeout': '5s', 'statement_timeout': '30s',
            }})
            engines.append(engine)
            schemas.append(schema)
            async with engine.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                def migrate(connection, number=number):
                    if number == 0:
                        scripts = ScriptDirectory(str(Path(__file__).parents[1] / 'src/asaree/migrations'))
                        with Operations.context(MigrationContext.configure(connection)):
                            for revision in reversed(list(scripts.walk_revisions())):
                                revision.module.upgrade()
                    else:
                        Skill.metadata.create_all(connection, tables=[Skill.__table__, SkillFile.__table__,
                                                                      MCPServerConfig.__table__])
                await conn.run_sync(migrate)
            factories.append(async_sessionmaker(engine, expire_on_commit=False))
        yield factories
    finally:
        for engine, schema in zip(engines, schemas, strict=True):
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()


def write_archive(job, root, *, version=2):
    root.mkdir(parents=True)
    stage = root / 'writer'
    stage.mkdir()
    skill_id = str(uuid.uuid4())
    writer = PackageWriter(root / 'archive.tar.gz', stage)
    writer.json('publication/canvas.json', {'nodes': [
        {'id': 'skill', 'type': 'skill', 'data': {'config': {'skill_id': skill_id}}}], 'edges': []})
    writer.json('publication/settings.json', {'design_spec': {'factors': []}})
    writer.json('experiment-metadata.json', {'name': 'Interrupted import'})
    if version >= 2:
        writer.json(f'resources/skill-metadata/{skill_id}.json', {
            'id': skill_id, 'name': 'retained-skill', 'description': 'Source discovery', 'body': 'Never execute',
            'frontmatter': {}, 'source_filename': None})
    raw = stage / 'skill.md'
    raw.write_text('---\nname: retained-skill\ndescription: Source discovery\n---\nNever execute')
    writer.add(raw, f'resources/skills/{skill_id}/SKILL.md', identity=skill_id, provenance='captured_at_download')
    resource_id = str(uuid.uuid4())
    raw.write_bytes(b'\x00\xffnot JSON\r\n')
    resource_path = f'resources/skills/{skill_id}/references/SKILL.md'
    writer.add(raw, resource_path, identity=resource_id, provenance='captured_at_download')
    if version >= 2:
        writer.dependency(resource_id, 'skill_file', 'captured_at_download', skill_id=skill_id,
                          file=resource_path, relative_path='references/SKILL.md', encoding='base64', position=9)
    writer.dependency(skill_id, 'skill', 'captured_at_download', directory=f'resources/skills/{skill_id}')
    writer.finish({'format': 'asaree.experiment-package', 'schema_version': version,
                   'experiment_id': str(uuid.uuid4()), 'protocol_id': str(uuid.uuid4()),
                   'publication': {'id': str(uuid.uuid4()), 'version': 9}, 'results_included': False})


async def test_durable_crash_retry_cancel_cleanup_and_ownership(import_databases, tmp_path, monkeypatch):
    product, resources = import_databases
    settings = AsareeSettings(_env_file=None, data_root=str(tmp_path / 'storage'))
    monkeypatch.setattr('asaree.config.get_settings', lambda: settings)

    @asynccontextmanager
    async def product_session():
        async with product() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    class WorkerKilled(BaseException):
        pass

    resource_commits = 0
    kill_after_resource_commit = None

    @asynccontextmanager
    async def resource_session(**kwargs):
        nonlocal resource_commits
        async with resources.begin() as db:
            yield db
        resource_commits += 1
        if resource_commits == kill_after_resource_commit:
            raise WorkerKilled()

    monkeypatch.setattr(imports, 'get_session', product_session)
    monkeypatch.setattr(imports, 'system_session', resource_session)
    monkeypatch.setattr(import_visibility, 'get_session', product_session)
    async with product.begin() as db:
        owner = User(email='importer@example.test', hashed_password='test', display_name='Importer')
        foreign = User(email='foreign@example.test', hashed_password='test', display_name='Foreign')
        db.add_all([owner, foreign])
    async with product.begin() as db:
        job = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key='request-one',
            status='validating', preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
        db.add(job)
    write_archive(job, imports.directory(job.id))
    await imports.execute_package_import({}, str(job.id))
    async with product.begin() as db:
        checked = await db.get(PackageImport, job.id)
        assert checked.status == 'awaiting_confirmation'
        assert await db.scalar(select(func.count()).select_from(ResearchExperiment)) == 0
        with pytest.raises(Exception) as denied:
            await api.confirm(job.id, db, foreign)
        assert denied.value.status_code == 404
        await api.confirm(job.id, db, owner)
    kill_after_resource_commit = resource_commits + 1
    with pytest.raises(WorkerKilled):
        await imports.execute_package_import({}, str(job.id))
    async with product.begin() as db:
        assert await db.scalar(select(func.count()).select_from(ResearchExperiment)) == 0
        assert (await db.get(PackageImport, job.id)).status == 'queued'
    async with resources.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Skill)) == 1
    kill_after_resource_commit = None
    await imports.execute_package_import({}, str(job.id))
    await imports.execute_package_import({}, str(job.id))
    async with product.begin() as db:
        done = await db.get(PackageImport, job.id)
        assert done.status == 'completed', done.error
        assert await db.scalar(select(func.count()).select_from(ResearchExperiment)) == 1
        assert await db.scalar(select(func.count()).select_from(ImportedIdentity).where(
            ImportedIdentity.kind == 'skill')) == 1
    async with resources.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Skill)) == 1
    # A second independent import of the exact same bytes has fresh identities.
    # Simulate death after the visibility flip but before marking ASAREE complete.
    async with product.begin() as db:
        repeated = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key='same-bytes-new-request',
            status='validating', preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
        db.add(repeated)
    imports.directory(repeated.id).mkdir(parents=True)
    shutil.copyfile(imports.directory(job.id) / 'archive.tar.gz',
                    imports.directory(repeated.id) / 'archive.tar.gz')
    await imports.execute_package_import({}, str(repeated.id))
    async with product.begin() as db:
        await api.confirm(repeated.id, db, owner)
    kill_after_resource_commit = resource_commits + 2
    with pytest.raises(WorkerKilled):
        await imports.execute_package_import({}, str(repeated.id))
    async with product.begin() as db:
        interrupted = await db.get(PackageImport, repeated.id)
        assert interrupted.status == 'activating'
        assert await get_experiment(db, interrupted.experiment_id) is None
        local_skill_id = await db.scalar(select(ImportedIdentity.local_id).where(
            ImportedIdentity.import_id == repeated.id, ImportedIdentity.kind == 'skill'))
    async with resources.begin() as db:
        hidden = await db.get(Skill, local_skill_id)
        assert await import_visibility.active_resources([hidden]) == []
    kill_after_resource_commit = None
    await imports.execute_package_import({}, str(repeated.id))
    async with product.begin() as db:
        repeated_done = await db.get(PackageImport, repeated.id)
        original_done = await db.get(PackageImport, job.id)
        assert repeated_done.status == 'completed'
        assert repeated_done.experiment_id != original_done.experiment_id
        assert repeated_done.sha256 == original_done.sha256
    async with product.begin() as db:
        cancelled = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key='request-two',
            status='validating', preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
        db.add(cancelled)
    write_archive(cancelled, imports.directory(cancelled.id))
    await imports.execute_package_import({}, str(cancelled.id))
    async with product.begin() as db:
        await api.confirm(cancelled.id, db, owner)
    original_checkpoint = imports.checkpoint

    async def cancel_at_activation(identifier, phase, **progress):
        if phase == 'activating':
            async with product.begin() as db:
                await api.cancel(identifier, db, owner)
        await original_checkpoint(identifier, phase, **progress)

    monkeypatch.setattr(imports, 'checkpoint', cancel_at_activation)
    await imports.execute_package_import({}, str(cancelled.id))
    async with product.begin() as db:
        assert (await db.get(PackageImport, cancelled.id)).status == 'cancelled'
        assert await db.scalar(select(func.count()).select_from(ResearchExperiment)) == 2
    await imports.relay_imports({})
    assert not imports.directory(cancelled.id).exists()
    async with resources.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Skill)) == 2


async def test_expiry_isolates_malformed_upload_and_compensation_failure_from_pending_import(
    import_databases, tmp_path, monkeypatch,
):
    product, resources = import_databases
    settings = AsareeSettings(_env_file=None, data_root=str(tmp_path / 'storage'))
    monkeypatch.setattr('asaree.config.get_settings', lambda: settings)

    @asynccontextmanager
    async def product_session():
        async with product.begin() as db:
            yield db

    @asynccontextmanager
    async def resource_session(**kwargs):
        async with resources.begin() as db:
            yield db

    monkeypatch.setattr(imports, 'get_session', product_session)
    monkeypatch.setattr(imports, 'system_session', resource_session)
    async with product.begin() as db:
        owner = User(email='relay@example.test', hashed_password='test', display_name='Importer')
        db.add(owner)
    jobs = []
    async with product.begin() as db:
        for key in ('malformed', 'compensation-fails', 'staged-cancelled', 'pending-valid'):
            job = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key=key,
                status='validating', preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
            jobs.append(job)
            db.add(job)
    bad, failing, staged, pending = jobs
    root = imports.directory(bad.id)
    root.mkdir(parents=True)
    with tarfile.open(root / 'archive.tar.gz', 'w:gz') as output:
        for name, data in [('manifest.json', b'{}'), ('dependencies.jsonl', b'{not-json\n')]:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))
    await imports.execute_package_import({}, str(bad.id))
    assert (root / 'payload/dependencies.jsonl').is_file()
    staged_ids = []
    staged_experiments = {}
    for job in (failing, staged):
        job_root = imports.directory(job.id)
        write_archive(job, job_root)
        preflight = inspect(job_root / 'archive.tar.gz', job_root / 'payload', Limits(), threading.Event())
        source_skill = next((job_root / 'payload/resources/skills').iterdir()).name
        local_id = identity(job, 'skill', source_skill)
        staged_ids.append(local_id)
        async with product.begin() as db:
            current = await db.get(PackageImport, job.id)
            current.preflight = preflight
            current.sha256 = digest(job_root / 'archive.tar.gz')
            async with resources.begin() as motoro:
                reconstruction = Reconstruction(db, current, job_root / 'payload', Limits())
                current.experiment_id = await reconstruction.activate(motoro)
            staged_experiments[job.id] = current.experiment_id
            current.status = 'cancelled'
    write_archive(pending, imports.directory(pending.id))
    async with product.begin() as db:
        current = await db.get(PackageImport, bad.id)
        assert current.status == 'failed'
        current.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    original_remove = imports.remove_staged_product
    async def interrupted_compensation(db, job):
        await original_remove(db, job)
        if job.id == failing.id:
            raise RuntimeError('Disposable compensation failure')
    monkeypatch.setattr(imports, 'remove_staged_product', interrupted_compensation)
    from asaree.worker import enqueue
    async def run_enqueued(function, job_id, **kwargs):
        await imports.execute_package_import({}, job_id)
    pool = SimpleNamespace(enqueue_job=AsyncMock(side_effect=run_enqueued))
    monkeypatch.setattr(enqueue, '_get_pool', AsyncMock(return_value=pool))
    await imports.relay_imports({})
    async with product.begin() as db:
        assert (await db.get(PackageImport, bad.id)).status == 'expired'
        assert (await db.get(PackageImport, staged.id)).status == 'expired'
        retained = await db.get(PackageImport, failing.id)
        assert retained.status == 'cancelled'
        assert retained.progress['phase'] == 'compensation_required'
        assert retained.experiment_id == staged_experiments[failing.id]
        assert await db.get(ResearchExperiment, retained.experiment_id) is not None
        assert await db.get(ResearchExperiment, staged_experiments[staged.id]) is None
        assert (await db.get(PackageImport, pending.id)).status == 'awaiting_confirmation'
    assert not root.exists()
    assert not imports.directory(staged.id).exists()
    assert imports.directory(failing.id).exists()
    pool.enqueue_job.assert_awaited_once()
    async with resources.begin() as db:
        assert all([await db.get(Skill, identifier) is None for identifier in staged_ids])
    # A failure rolls back only its product transaction; cleanup remains retryable.
    monkeypatch.setattr(imports, 'remove_staged_product', original_remove)
    await imports.relay_imports({})
    async with product.begin() as db:
        assert (await db.get(PackageImport, failing.id)).status == 'expired'
        assert await db.get(ResearchExperiment, staged_experiments[failing.id]) is None
    assert not imports.directory(failing.id).exists()
