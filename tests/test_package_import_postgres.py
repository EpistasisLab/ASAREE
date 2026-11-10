"""Independent-owner reconstruction against both existing PostgreSQL databases."""
from __future__ import annotations

import base64
import hashlib
import os
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from motoro.models.mcp_server import MCPServerConfig, MCPTransport
from motoro.models.skill import Skill, SkillFile
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_result_reads import results_db as _results_db

from asaree.api import package_imports as api
from asaree.config import AsareeSettings
from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.user import User
from asaree.services import experiment_packages as packages
from asaree.services import okf_bundles
from asaree.services import package_imports as imports
from asaree.services.dataset_inputs import freeze_dataset_inputs
from asaree.services.datasets import create_dataset, quick_split_dataset
from asaree.services.design_generation import generate_design_cells
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
    monkeypatch.setattr(okf_bundles, 'get_settings', lambda: settings)
    source = b'x,y\r\n01,0\r\n02,1\r\n03,0\r\n04,1\r\n05,0\r\n06,1\r\n07,0\r\n08,1\r\n'
    dataset = await create_dataset(db, name='Pinned original', csv_bytes=source, owner_id=owner.id, target_column='y')
    if mode == 'whole_dataset':
        await quick_split_dataset(db, dataset=dataset, seed=17)
    skill = Skill(id=uuid.uuid4(), name='binary-skill', description='Discovery', body='Opaque instructions',
                  frontmatter={'license': 'MIT', 'metadata': {'source': str(dataset.id)}},
                  source_filename='source.md', owner_id=owner.id)
    binary = b'\x00\xff\x80\r\nEXACT'
    resource_db.add(skill)
    alternate = Skill(id=uuid.uuid4(), name='alternate-skill', description='Alternative', body='Other exact content',
                      frontmatter={}, owner_id=owner.id)
    resource_db.add(alternate)
    await resource_db.flush()
    resource_db.add(SkillFile(skill_id=skill.id, path='assets/binary.bin', encoding='base64',
                              content=base64.b64encode(binary).decode(), position=7))
    await resource_db.flush()
    graph = {'nodes': [
        {'id': 'agent', 'type': 'agent', 'data': {'config': {
            'system_prompt': str(dataset.id), 'api_key': 'NEVER_IMPORT_CONNECTION_SECRET'},
            'factor_bindings': {'skill_selection': 'Skills'}, 'skill_selection': [str(skill.id)]}},
        {'id': 'dataset', 'type': 'dataset', 'data': {'config': {
            'dataset_id': str(dataset.id), 'dataset_name': dataset.name}}},
        {'id': 'skill', 'type': 'skill', 'data': {'config': {'skill_id': str(skill.id)}}},
        {'id': 'alternate', 'type': 'skill', 'data': {'config': {'skill_id': str(alternate.id)}}},
        {'id': 'script', 'type': 'script', 'data': {'config': {'code': 'raise RuntimeError("NEVER EXECUTE")'}}},
    ], 'edges': [{'id': 'input', 'source': 'dataset', 'target': 'agent', 'targetHandle': 'dataset',
                  'data': {'dataset_input': {'mode': mode, **({'columns': ['x']} if mode == 'per_row' else {})}}},
                 {'source': 'skill', 'target': 'agent', 'targetHandle': 'skill'},
                 {'source': 'alternate', 'target': 'agent', 'targetHandle': 'skill'}],
        'sampling_snapshot': {'source-key': {'explicit_input': {'model': 'source-model'},
                                            'input': {'api_key': 'NEVER_IMPORT_CONNECTION_SECRET'}}}}
    factors = [{'name': 'Skills', 'level_type': 'skill_selection',
                'levels': [[str(skill.id)], [str(alternate.id)]], 'level_labels': ['Binary', 'Plain']}]
    secondary = None
    if mode == 'whole_dataset':
        secondary = await create_dataset(db, name='Explicit original', csv_bytes=source,
                                         owner_id=owner.id, target_column='y')
        graph['nodes'].append({'id': 'original', 'type': 'dataset', 'data': {'config': {
            'dataset_id': str(secondary.id), 'dataset_name': secondary.name}}})
        graph['edges'].append({'source': 'original', 'target': 'agent', 'targetHandle': 'dataset'})
        graph['nodes'][0]['data']['factor_bindings']['dataset_selection'] = 'Inputs'
        graph['nodes'][0]['data']['dataset_selection'] = [str(dataset.id)]
        factors.append({'name': 'Inputs', 'level_type': 'dataset_selection',
                        'levels': [[str(dataset.id)], [str(secondary.id)]], 'level_labels': ['Split', 'Original']})
    knowledge = Path(settings.okf_bundle_upload_dir) / str(owner.id) / 'source'
    knowledge.mkdir(parents=True)
    knowledge_bytes = b'# Exact knowledge\r\n\xc3\xa9\n'
    (knowledge / 'concept.md').write_bytes(knowledge_bytes)
    server = MCPServerConfig(id=uuid.uuid4(), owner_id=owner.id, transport=MCPTransport.STDIO,
        name=okf_bundles.server_name_for(owner.id, knowledge),
        command=okf_bundles.command_for_bundle(knowledge, managed=True),
        capabilities={'tools': [{'name': 'read_concept'}]})
    resource_db.add(server)
    await resource_db.flush()
    graph['nodes'].append({'id': 'knowledge', 'type': 'okf_bundle', 'data': {'config': {
        'bundle_id': str(server.id), 'bundle_path': str(knowledge), 'server_name': server.name}}})
    graph['edges'].append({'source': 'knowledge', 'target': 'agent', 'targetHandle': 'knowledge'})
    source_experiment = await db.get(ResearchExperiment, scope.experiment_id)
    source_experiment.design_spec = {'factors': factors, 'replicates': 2}
    await generate_design_cells(db, experiment_id=scope.experiment_id, factors=factors, replicates=2,
                                design_spec=source_experiment.design_spec)
    from asaree.services.design_revisions import get_current_revision
    design = await get_current_revision(db, scope.experiment_id)
    scope = replace(scope, design_revision_id=design.id)
    publication = await db.get(ProtocolRevision, scope.protocol_revision_id)
    publication.graph = graph
    publication.design_revision_id = design.id
    publication.experiment_snapshot = {'design_spec': source_experiment.design_spec, 'hypothesis': 'Preserved',
                                       'design_type': 'factorial', 'measurement_plan': None, 'task_brief': None}
    publication.dataset_inputs = await freeze_dataset_inputs(db, graph, owner_id=owner.id,
                                                            design_spec=source_experiment.design_spec)
    if mode == 'whole_dataset':
        await quick_split_dataset(db, dataset=dataset, seed=42)
        await quick_split_dataset(db, dataset=secondary, seed=3)
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
    portable_graph, portable_settings = packages.portable_definition(graph, publication.experiment_snapshot)
    writer.json('publication/canvas.json', portable_graph)
    writer.json('publication/settings.json', portable_settings)
    writer.json('experiment-metadata.json', {'name': 'Source', 'description': 'Captured metadata'})
    await packages._datasets(db, writer, publication, owner.id)
    await packages._design(db, writer, publication)
    await packages._skill(resource_db, writer, str(skill.id), owner.id)
    await packages._skill(resource_db, writer, str(alternate.id), owner.id)
    await packages._server(resource_db, writer, {'bundle_id': str(server.id)}, owner.id, knowledge=True)
    if include_results:
        parent = await db.scalar(select(FactorialReplicateResult).join(FactorialCell).where(
            FactorialCell.design_revision_id == design.id).limit(1))
        slot = None
        if mode == 'per_row':
            slot = FactorialRowResult(replicate_result_id=parent.id, protocol_revision_id=publication.id,
                dataset_id=dataset.id, raw_sha256=dataset.raw_sha256, row_index=0)
            db.add(slot)
            await db.flush()
        run = ProtocolRun(protocol_id=scope.protocol_id, owner_id=owner.id,
            protocol_revision_id=publication.id, design_revision_id=scope.design_revision_id, status='completed',
            replicate_result_id=parent.id, row_result_id=slot.id if slot else None,
            dataset_row={'dataset_id': str(dataset.id), 'raw_sha256': dataset.raw_sha256,
                         'row_index': 0, 'values': {'x': '01'}} if slot else None,
            attempt_result={'measurement': {'observations': [
                {'metric_id': 'null', 'status': 'measured', 'value': None},
                {'metric_id': 'missing', 'status': 'unavailable'},
                {'metric_id': 'legacy', 'provenance': 'legacy.unknown', 'value': str(dataset.id)}]}})
        db.add(run)
        await db.flush()
        outputs = Path(settings.dataset_workspace_dir) / str(scope.experiment_id) / str(run.id) / 'agent=agent'
        outputs.mkdir(parents=True)
        (outputs / 'output.bin').write_bytes(binary)
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
    assert 'api_key' not in protocol.graph['nodes'][0]['data']['config']
    assert 'sampling_snapshot' not in protocol.graph
    local_dataset = await db.get(RegisteredDataset, uuid.UUID(importer.maps['dataset', str(dataset.id)]))
    assert local_dataset.owner_id == destination_owner.id
    assert Path(local_dataset.raw_path).read_bytes() == source
    assert bool(local_dataset.current_split_id) == (mode == 'whole_dataset')
    local_server = await resource_db.get(MCPServerConfig, uuid.UUID(importer.maps['mcp', str(server.id)]))
    assert local_server.owner_id == destination_owner.id
    restored_knowledge = Path(okf_bundles.bundle_path_from_command(local_server.command))
    assert (restored_knowledge / 'concept.md').read_bytes() == knowledge_bytes
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
    await packages._import_sources(db, second, experiment_id, include_results=include_results)
    second.finish({})
    import tarfile
    with tarfile.open(root / 'second.tar.gz') as reexport:
        pin = local_publication.dataset_inputs['inputs'][0]
        assert reexport.extractfile(f'inputs/files/{pin["raw_artifact"]["id"]}.csv').read() == source
        assert reexport.extractfile(f'resources/skills/{local_skill.id}/assets/binary.bin').read() == binary
        if include_results:
            nested = reexport.extractfile(f'sources/{job.id}/source.tar.gz').read()
            assert hashlib.sha256(nested).hexdigest() == job.sha256
    assert manifest['schema_version'] == 2
