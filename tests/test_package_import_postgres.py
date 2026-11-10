"""Independent-owner reconstruction against both existing PostgreSQL databases."""
from __future__ import annotations

import base64
import hashlib
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from motoro.models.mcp_server import MCPServerConfig
from motoro.models.skill import Skill, SkillFile
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_result_reads import results_db as _results_db

from asaree.api import package_imports as api
from asaree.config import AsareeSettings
from asaree.models.dataset import RegisteredDataset
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.user import User
from asaree.services import experiment_packages as packages
from asaree.services import package_imports as imports
from asaree.services.dataset_inputs import freeze_dataset_inputs
from asaree.services.datasets import create_dataset, quick_split_dataset
from asaree.services.package_reconstruction import Reconstruction, require_configured
from asaree.services.package_validation import Limits, inspect
from asaree.services.protocol_revisions import publish_protocol

results_db = _results_db


@pytest_asyncio.fixture
async def resource_db():
    url = os.environ.get('ASAREE_DATABASE_URL')
    if not url:
        pytest.skip('Existing Motoro PostgreSQL required')
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        schema = 'package_test_' + uuid.uuid4().hex
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        def create_tables(conn):
            Skill.metadata.create_all(conn, tables=[Skill.__table__, SkillFile.__table__, MCPServerConfig.__table__])
        await connection.run_sync(create_tables)
        async with AsyncSession(bind=connection, expire_on_commit=False) as db:
            yield db
        await transaction.rollback()
    await engine.dispose()


@pytest.mark.parametrize('mode', ['whole_dataset', 'per_row'])
@pytest.mark.parametrize('include_results', [False, True])
async def test_export_import_independent_owner_and_reexport(
    results_db, resource_db, tmp_path, monkeypatch, mode, include_results,
):
    db, scope, cell, owner = results_db
    destination_owner = User(email=f'{uuid.uuid4()}@example.test', hashed_password='test', display_name='Destination')
    db.add(destination_owner)
    await db.flush()
    settings = AsareeSettings(_env_file=None, data_root=str(tmp_path / 'storage'))
    monkeypatch.setattr('asaree.config.get_settings', lambda: settings)
    monkeypatch.setattr('asaree.services.datasets.get_settings', lambda: settings)
    source = b'x,y\r\n01,0\r\n02,1\r\n03,0\r\n04,1\r\n05,0\r\n06,1\r\n07,0\r\n08,1\r\n'
    dataset = await create_dataset(db, name='Pinned original', csv_bytes=source, owner_id=owner.id, target_column='y')
    if mode == 'whole_dataset':
        await quick_split_dataset(db, dataset=dataset, seed=17)
    skill = Skill(id=uuid.uuid4(), name='binary-skill', description='Discovery', body='Opaque instructions',
                  frontmatter={'license': 'MIT', 'metadata': {'source': str(dataset.id)}},
                  source_filename='source.md', owner_id=owner.id)
    binary = b'\x00\xff\x80\r\nEXACT'
    resource_db.add(skill)
    await resource_db.flush()
    resource_db.add(SkillFile(skill_id=skill.id, path='assets/binary.bin', encoding='base64',
                              content=base64.b64encode(binary).decode(), position=7))
    await resource_db.flush()
    graph = {'nodes': [
        {'id': 'agent', 'type': 'agent', 'data': {'config': {'system_prompt': str(dataset.id)}}},
        {'id': 'dataset', 'type': 'dataset', 'data': {'config': {
            'dataset_id': str(dataset.id), 'dataset_name': dataset.name}}},
        {'id': 'skill', 'type': 'skill', 'data': {'config': {'skill_id': str(skill.id)}}},
        {'id': 'script', 'type': 'script', 'data': {'config': {'code': 'raise RuntimeError("NEVER EXECUTE")'}}},
    ], 'edges': [{'id': 'input', 'source': 'dataset', 'target': 'agent', 'targetHandle': 'dataset',
                  'data': {'dataset_input': {'mode': mode, **({'columns': ['x']} if mode == 'per_row' else {})}}}]}
    publication = await db.get(ProtocolRevision, scope.protocol_revision_id)
    publication.graph = graph
    publication.experiment_snapshot = {'design_spec': {'factors': []}, 'hypothesis': 'Preserved',
                                       'design_type': 'factorial', 'measurement_plan': None, 'task_brief': None}
    publication.dataset_inputs = await freeze_dataset_inputs(db, graph, owner_id=owner.id)
    job = PackageImport(id=uuid.uuid4(), owner_id=destination_owner.id, request_key=uuid.uuid4().hex,
                        status='queued', preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
    db.add(job)
    await db.flush()
    root = imports.directory(job.id)
    root.mkdir(parents=True)
    staging = root / 'writer'
    staging.mkdir()
    archive = root / 'archive.tar.gz'
    writer = packages.PackageWriter(archive, staging)
    writer.json('publication/canvas.json', graph)
    writer.json('publication/settings.json', publication.experiment_snapshot)
    writer.json('experiment-metadata.json', {'name': 'Source', 'description': 'Captured metadata'})
    await packages._datasets(db, writer, publication, owner.id)
    await packages._design(db, writer, publication)
    await packages._skill(resource_db, writer, str(skill.id), owner.id)
    if include_results:
        run = ProtocolRun(protocol_id=scope.protocol_id, owner_id=owner.id,
            protocol_revision_id=publication.id, design_revision_id=scope.design_revision_id, status='completed',
            attempt_result={'measurement': {'observations': [
                {'metric_id': 'null', 'status': 'measured', 'value': None},
                {'metric_id': 'missing', 'status': 'unavailable'},
                {'metric_id': 'legacy', 'provenance': 'legacy.unknown', 'value': str(dataset.id)}]}})
        db.add(run)
        await db.flush()
        await packages._results(db, writer, SimpleNamespace(owner_id=owner.id, protocol_id=scope.protocol_id,
            protocol_revision_id=publication.id, design_revision_id=scope.design_revision_id,
            experiment_id=scope.experiment_id), AsyncMock(), graph)
    manifest = writer.finish({'format': 'asaree.experiment-package', 'schema_version': 2,
        'experiment_id': str(scope.experiment_id), 'protocol_id': str(scope.protocol_id),
        'publication': {'id': str(publication.id), 'version': 1},
        'design_revision_id': str(scope.design_revision_id), 'results_included': include_results})
    summary = inspect(archive, root / 'payload', Limits(), threading.Event())
    job.preflight, job.sha256 = summary, hashlib.sha256(archive.read_bytes()).hexdigest()
    importer = Reconstruction(db, job, root / 'payload', Limits())
    experiment_id = await importer.activate(resource_db)
    @asynccontextmanager
    async def resource_session(**kwargs):
        yield resource_db
    monkeypatch.setattr(imports, 'system_session', resource_session)
    await imports.activate_resources(job)
    job.experiment_id, job.status = experiment_id, 'completed'
    await db.flush()
    protocol = await db.get(Protocol, uuid.UUID(importer.maps['protocol', str(scope.protocol_id)]))
    assert protocol.owner_id == destination_owner.id and protocol.published_revision_id is None
    assert protocol.graph['nodes'][0]['data']['config']['system_prompt'] == str(dataset.id)
    local_dataset = await db.get(RegisteredDataset, uuid.UUID(importer.maps['dataset', str(dataset.id)]))
    assert local_dataset.owner_id == destination_owner.id
    assert Path(local_dataset.raw_path).read_bytes() == source
    assert bool(local_dataset.current_split_id) == (mode == 'whole_dataset')
    local_skill = await resource_db.get(Skill, uuid.UUID(importer.maps['skill', str(skill.id)]))
    assert local_skill.owner_id == destination_owner.id and not local_skill.is_system
    assert local_skill.body == skill.body and local_skill.frontmatter == skill.frontmatter
    local_file = await resource_db.scalar(select(SkillFile).where(SkillFile.skill_id == local_skill.id))
    assert base64.b64decode(local_file.content) == binary and local_file.position == 7
    with pytest.raises(ValueError, match='local dependency'):
        await require_configured(db, experiment_id)
    assert await db.scalar(select(func.count()).select_from(ProtocolRun).where(
        ProtocolRun.owner_id == destination_owner.id)) == 0
    identities = (await db.scalars(select(ImportedIdentity).where(ImportedIdentity.import_id == job.id))).all()
    assert len([row for row in identities if row.kind == 'historical_attempt']) == int(include_results)
    # Exact source re-export is a first-class owned API operation, including
    # evidence absent from current execution projections.
    response = await api.source(job.id, db, destination_owner)
    assert hashlib.sha256(Path(response.path).read_bytes()).hexdigest() == job.sha256
    with pytest.raises(Exception) as forbidden:
        await api.source(job.id, db, owner)
    assert forbidden.value.status_code == 404
    # Native local publication still passes normal input resolution, after an
    # explicit local-configuration acknowledgement. No model calls are made.
    job.configured_at = datetime.now(UTC)
    await db.flush()
    local_publication = await publish_protocol(db, protocol, owner_id=destination_owner.id)
    local_stage = root / 'second-writer'
    local_stage.mkdir()
    second = packages.PackageWriter(root / 'second.tar.gz', local_stage)
    await packages._datasets(db, second, local_publication, destination_owner.id)
    await packages._skill(resource_db, second, str(local_skill.id), destination_owner.id)
    second.finish({})
    import tarfile
    with tarfile.open(root / 'second.tar.gz') as reexport:
        pin = local_publication.dataset_inputs['inputs'][0]
        assert reexport.extractfile(f'inputs/files/{pin["raw_artifact"]["id"]}.csv').read() == source
        assert reexport.extractfile(f'resources/skills/{local_skill.id}/assets/binary.bin').read() == binary
    assert manifest['schema_version'] == 2
