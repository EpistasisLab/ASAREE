"""Independent-owner reconstruction against both existing PostgreSQL databases."""
from __future__ import annotations

import base64
import copy
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
from asaree.api.protocols import publish_protocol_endpoint
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
from asaree.services import okf_bundles, okf_documents
from asaree.services import package_imports as imports
from asaree.services.dataset_inputs import freeze_dataset_inputs, resolve_attempt_inputs
from asaree.services.datasets import create_dataset, quick_split_dataset
from asaree.services.design_generation import generate_design_cells
from asaree.services.package_reconstruction import Reconstruction, require_configured
from asaree.services.package_validation import Limits, inspect

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
    monkeypatch.setattr(okf_documents, 'get_settings', lambda: settings)
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
    async def read_fixture_skill(identifier):
        value = await resource_db.get(Skill, uuid.UUID(str(identifier)))
        return value if value is not None and value.deleted_at is None else None
    monkeypatch.setattr('motoro.services.skill_service.get_skill', read_fixture_skill)
    graph = {'nodes': [
        {'id': 'agent', 'type': 'agent', 'data': {'config': {
            'system_prompt': str(dataset.id), 'api_key': 'NEVER_IMPORT_CONNECTION_SECRET'},
            'factor_bindings': {'skill_selection': 'Skills'}, 'skill_selection': [str(skill.id)]}},
        {'id': 'dataset', 'type': 'dataset', 'data': {'config': {
            'dataset_id': str(dataset.id), 'dataset_name': dataset.name}}},
        {'id': 'skill', 'type': 'skill', 'data': {'config': {'skill_id': str(skill.id)}}},
        {'id': 'alternate', 'type': 'skill', 'data': {'config': {'skill_id': str(alternate.id)}}},
        {'id': 'script', 'type': 'script', 'data': {'config': {'code': 'raise RuntimeError("NEVER EXECUTE")'}}},
        {'id': 'model', 'type': 'model_openai', 'data': {'config': {
            'provider': 'openai', 'model': 'gpt-test', 'temperature': 0.1,
            'api_key': 'NEVER_IMPORT_CONNECTION_SECRET', 'api_base': 'https://source.invalid'}}},
    ], 'edges': [{'id': 'input', 'source': 'dataset', 'target': 'agent', 'targetHandle': 'dataset',
                  'data': {'dataset_input': {'mode': mode, **({'columns': ['x']} if mode == 'per_row' else {})}}},
                 {'source': 'skill', 'target': 'agent', 'targetHandle': 'skill'},
                 {'source': 'alternate', 'target': 'agent', 'targetHandle': 'skill'}],
        'sampling_snapshot': {'source-key': {'explicit_input': {'model': 'source-model'},
                                            'input': {'api_key': 'NEVER_IMPORT_CONNECTION_SECRET'}}}}
    graph['edges'].append({'source': 'model', 'target': 'agent', 'targetHandle': 'model'})
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
    document = mode == 'per_row'
    knowledge_root = settings.okf_document_dir if document else settings.okf_bundle_upload_dir
    knowledge = Path(knowledge_root) / str(owner.id) / 'source'
    knowledge.mkdir(parents=True)
    knowledge_bytes = b'# Exact knowledge\r\n\xc3\xa9\n'
    (knowledge / 'concept.md').write_bytes(knowledge_bytes)
    server = MCPServerConfig(id=uuid.uuid4(), owner_id=owner.id, transport=MCPTransport.STDIO,
        name=(okf_documents.server_name_for(owner.id, knowledge) if document else
              okf_bundles.server_name_for(owner.id, knowledge)),
        command=okf_bundles.command_for_bundle(knowledge, managed=True),
        capabilities={'tools': [{'name': 'read_concept'}]})
    resource_db.add(server)
    await resource_db.flush()
    graph['nodes'].append({'id': 'knowledge', 'type': 'okf_document' if document else 'okf_bundle', 'data': {'config': {
        'document_id' if document else 'bundle_id': str(server.id),
        'document_path' if document else 'bundle_path': str(knowledge), 'server_name': server.name}}})
    graph['edges'].append({'source': 'knowledge', 'target': 'agent', 'targetHandle': 'knowledge'})
    graph['nodes'].append({'id': 'tool', 'type': 'mcp_tool', 'data': {'config': {
        'server_id': str(server.id), 'server_name': server.name, 'tool_names': ['read_concept']}}})
    graph['edges'].append({'source': 'tool', 'target': 'agent', 'targetHandle': 'tool'})
    measurement_plan = {'metrics': [{'id': 'reported', 'name': 'Reported output', 'value_type': 'opaque',
                                    'direction': 'neutral', 'aggregation': 'none'}],
        'producers': [{'id': 'source-binding', 'producer_id': 'asaree.mcp_tool', 'kind': 'reported',
                       'outputs': {'value': 'reported'}, 'config': {'agent_node_id': 'agent', 'mcp_node_id': 'tool',
                           'server_id': str(server.id), 'tool_name': 'read_concept', 'opaque_value': str(server.id)}}],
        'inputs': []}
    source_experiment = await db.get(ResearchExperiment, scope.experiment_id)
    source_experiment.measurement_plan = measurement_plan
    source_experiment.design_spec = {'factors': factors, 'replicates': 2,
        'metrics': [{'id': 'reported', 'name': 'Reported output', 'kind': 'custom'}]}
    source_protocol = await db.get(Protocol, scope.protocol_id)
    source_protocol.graph = graph
    await db.flush()
    await generate_design_cells(db, experiment_id=scope.experiment_id, factors=factors, replicates=2,
                                design_spec=source_experiment.design_spec)
    from asaree.services.design_revisions import get_current_revision
    design = await get_current_revision(db, scope.experiment_id)
    scope = replace(scope, design_revision_id=design.id)
    publication = await db.get(ProtocolRevision, scope.protocol_revision_id)
    publication.graph = graph
    publication.design_revision_id = design.id
    publication.experiment_snapshot = {'design_spec': source_experiment.design_spec, 'hypothesis': 'Preserved',
                                       'design_type': 'factorial', 'measurement_plan': measurement_plan,
                                       'task_brief': None}
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
    @asynccontextmanager
    async def source_resources(**kwargs):
        yield resource_db
    monkeypatch.setattr(packages, 'system_session', source_resources)
    await packages._resources(writer, portable_graph, portable_settings, owner.id)
    if include_results:
        parent, factor_values = (await db.execute(select(
            FactorialReplicateResult, FactorialCell.factor_values,
        ).join(FactorialCell).where(FactorialCell.design_revision_id == design.id).limit(1))).one()
        slot = None
        if mode == 'per_row':
            slot = FactorialRowResult(replicate_result_id=parent.id, protocol_revision_id=publication.id,
                dataset_id=dataset.id, raw_sha256=dataset.raw_sha256, row_index=0)
            db.add(slot)
            await db.flush()
        row_snapshot = {'dataset_id': str(dataset.id), 'raw_sha256': dataset.raw_sha256,
                        'row_index': 0, 'values': {'x': '01'}} if slot else None
        from asaree.services.protocol_execution import apply_factor_bindings
        attempt_inputs = resolve_attempt_inputs(publication.dataset_inputs, apply_factor_bindings(graph, factor_values),
                                                 dataset_row=row_snapshot)
        run = ProtocolRun(protocol_id=scope.protocol_id, owner_id=owner.id,
            protocol_revision_id=publication.id, design_revision_id=scope.design_revision_id, status='completed',
            replicate_result_id=parent.id, row_result_id=slot.id if slot else None,
            dataset_row=row_snapshot, dataset_inputs=attempt_inputs, factor_values=factor_values,
            attempt_result={'measurement': {'observations': [
                {'metric_id': 'null', 'status': 'measured', 'value': None},
                {'metric_id': 'missing', 'status': 'unavailable'},
                {'metric_id': 'legacy', 'provenance': 'legacy.unknown', 'value': str(dataset.id)}]}})
        db.add(run)
        db.add(ProtocolRun(protocol_id=scope.protocol_id, owner_id=owner.id,
            protocol_revision_id=publication.id, design_revision_id=scope.design_revision_id,
            replicate_result_id=parent.id, row_result_id=slot.id if slot else None, status='failed',
            dataset_row=row_snapshot, dataset_inputs=attempt_inputs, factor_values=factor_values,
            attempt_result={'legacy': {'provenance': 'legacy.unknown', 'value': str(dataset.id)}},
            created_at=datetime.now(UTC) - timedelta(hours=1)))
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
    assert okf_documents.is_document_server(local_server) == document
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
    assert len([row for row in identities if row.kind == 'historical_attempt']) == 2 * int(include_results)
    # Exact source re-export is a first-class owned API operation, including
    # evidence absent from current execution projections.
    response = await api.source(job.id, db, destination_owner)
    assert hashlib.sha256(Path(response.path).read_bytes()).hexdigest() == job.sha256
    with pytest.raises(Exception) as forbidden:
        await api.source(job.id, db, owner)
    assert forbidden.value.status_code == 404
    # Configure a local test connection. Only the external provider boundary is
    # stubbed; ownership, graph validation, inputs/design and publication use the
    # normal API and both PostgreSQL databases. No provider is contacted.
    from motoro.schemas.agent import ModelConfig

    from asaree.services import package_reconstruction, sampling_snapshots
    draft = copy.deepcopy(protocol.graph)
    model = next(node for node in draft['nodes'] if node['type'] == 'model_openai')
    assert 'api_key' not in model['data']['config'] and 'api_base' not in model['data']['config']
    model['data']['config'].update(api_key='destination-test-key', api_base='http://127.0.0.1:9')
    protocol.graph = draft
    async def local_model_config(config, *, owner_id):
        assert owner_id == destination_owner.id
        assert config['api_key'] == 'destination-test-key'
        assert config['api_base'] == 'http://127.0.0.1:9'
        return ModelConfig(**config)
    monkeypatch.setattr(sampling_snapshots, 'build_model_config', local_model_config)
    monkeypatch.setattr(sampling_snapshots, 'resolve_discovery_context', AsyncMock(return_value={
        'provider': 'openai', 'credential_revision': 'local-fixture', 'discovered_at': None}))
    monkeypatch.setattr(package_reconstruction, 'system_session', resource_session)
    async def read_local_server(identifier):
        return await resource_db.get(MCPServerConfig, identifier)
    monkeypatch.setattr('asaree.services.reported_metrics.mcp_service.get_server', read_local_server)
    await db.flush()
    await api.configuration(job.id, api.Configuration(connections_configured=True, dependencies_resolved=True),
                            db, destination_owner)
    await publish_protocol_endpoint(protocol.id, destination_owner, db)
    local_publication = await db.get(ProtocolRevision, protocol.published_revision_id)
    assert local_publication.dataset_inputs['bindings'] == publication.dataset_inputs['bindings']
    for source_pin, local_pin in zip(publication.dataset_inputs['inputs'],
                                     local_publication.dataset_inputs['inputs'], strict=True):
        assert source_pin['dataset_id'] != local_pin['dataset_id']
        assert source_pin['raw_sha256'] == local_pin['raw_sha256']
        for key in ('mode', 'target_column', 'description', 'dictionary_json'):
            assert source_pin[key] == local_pin[key]
        for key in ('raw_artifact', 'train_artifact', 'test_artifact'):
            if source_pin[key] is None:
                assert local_pin[key] is None
            else:
                assert source_pin[key]['sha256'] == local_pin[key]['sha256']
                assert source_pin[key]['id'] != local_pin[key]['id']
    configured_producer = local_publication.experiment_snapshot['measurement_plan']['producers'][0]['config']
    local_server_id = uuid.UUID(importer.maps['mcp', str(server.id)])
    assert configured_producer['server_id'] == str(local_server_id)
    assert local_server_id != server.id and configured_producer['opaque_value'] == str(server.id)
    assert local_publication.graph['sampling_snapshot'] != graph['sampling_snapshot']
    local_stage = root / 'second-writer'
    local_stage.mkdir()
    second = packages.PackageWriter(root / 'second.tar.gz', local_stage)
    local_graph, local_settings = packages.portable_definition(local_publication.graph,
                                                               local_publication.experiment_snapshot)
    second.json('publication/canvas.json', local_graph)
    second.json('publication/settings.json', local_settings)
    second.json('experiment-metadata.json', {'name': 'Destination', 'id': str(experiment_id)})
    await packages._datasets(db, second, local_publication, destination_owner.id)
    await packages._design(db, second, local_publication)
    await packages._resources(second, local_graph, local_settings, destination_owner.id)
    await packages._import_sources(db, second, experiment_id, include_results=include_results)
    second.finish({'format': 'asaree.experiment-package', 'schema_version': 2,
        'experiment_id': str(experiment_id), 'protocol_id': str(protocol.id),
        'publication': {'id': str(local_publication.id), 'version': local_publication.revision},
        'design_revision_id': str(local_publication.design_revision_id), 'results_included': include_results})
    inspect(root / 'second.tar.gz', root / 'second-payload', Limits(), threading.Event())
    import tarfile
    with tarfile.open(root / 'second.tar.gz') as reexport:
        pin = local_publication.dataset_inputs['inputs'][0]
        assert reexport.extractfile(f'inputs/files/{pin["raw_artifact"]["id"]}.csv').read() == source
        assert reexport.extractfile(f'resources/skills/{local_skill.id}/assets/binary.bin').read() == binary
        if include_results:
            nested = reexport.extractfile(f'sources/{job.id}/source.tar.gz').read()
            assert hashlib.sha256(nested).hexdigest() == job.sha256
    assert manifest['schema_version'] == 2


async def test_v1_skill_binary_content_is_restored_with_explicit_unknown_source_metadata(
    results_db, resource_db, tmp_path, monkeypatch,
):
    from test_package_import_lifecycle import write_archive

    db, scope, cell, owner = results_db
    settings = AsareeSettings(_env_file=None, data_root=str(tmp_path / 'storage'))
    monkeypatch.setattr('asaree.config.get_settings', lambda: settings)
    job = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key='v1', status='queued',
                        preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
    db.add(job)
    await db.flush()
    root = imports.directory(job.id)
    write_archive(job, root, version=1)
    summary = inspect(root / 'archive.tar.gz', root / 'payload', Limits(), threading.Event())
    assert any('v1 omits' in warning for warning in summary['warnings'])
    importer = Reconstruction(db, job, root / 'payload', Limits())
    experiment = await importer.activate(resource_db)
    source = await db.scalar(select(ImportedIdentity).where(ImportedIdentity.import_id == job.id,
                                                           ImportedIdentity.kind == 'skill_file'))
    file = await resource_db.get(SkillFile, source.local_id)
    assert file.encoding == 'base64' and base64.b64decode(file.content) == b'\x00\xffnot JSON\r\n'
    assert source.provenance['dependency']['source_encoding'] == 'legacy.unknown'
    assert source.provenance['dependency']['source_position'] == 'legacy.unknown'
    protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == experiment))
    assert protocol.published_revision_id is None


async def test_missing_source_stays_unresolved_instead_of_using_an_existing_same_name_dataset(
    results_db, resource_db, tmp_path, monkeypatch,
):
    db, scope, cell, owner = results_db
    settings = AsareeSettings(_env_file=None, data_root=str(tmp_path / 'storage'))
    monkeypatch.setattr('asaree.config.get_settings', lambda: settings)
    monkeypatch.setattr('asaree.services.datasets.get_settings', lambda: settings)
    existing = await create_dataset(db, name='Unavailable source', csv_bytes=b'x\ncurrent bytes\n', owner_id=owner.id)
    source_id = str(uuid.uuid4())
    graph = {'nodes': [{'id': 'agent', 'type': 'agent', 'data': {}},
        {'id': 'dataset', 'type': 'dataset', 'data': {'config': {
            'dataset_id': source_id, 'dataset_name': existing.name}}}],
        'edges': [{'source': 'dataset', 'target': 'agent', 'targetHandle': 'dataset'}]}
    pin = {'dataset_id': source_id, 'dataset_name': existing.name, 'dataset_node_id': 'dataset',
           'mode': 'whole_dataset', 'raw_sha256': 'a' * 64, 'split_id': None, 'raw_artifact': None,
           'train_artifact': None, 'test_artifact': None, 'description': None, 'dictionary_json': None,
           'target_column': ''}
    job = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key='missing', status='queued',
                        preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
    db.add(job)
    await db.flush()
    root = imports.directory(job.id)
    stage = root / 'writer'
    stage.mkdir(parents=True)
    writer = packages.PackageWriter(root / 'archive.tar.gz', stage)
    writer.json('publication/canvas.json', graph)
    writer.json('publication/settings.json', {'design_spec': {'factors': []}})
    writer.json('experiment-metadata.json', {'name': 'Missing original'})
    writer.json('inputs/bindings.json', {'schema_version': 1, 'provenance': 'pinned', 'inputs': [pin],
        'bindings': [{'dataset_node_id': 'dataset', 'agent_node_id': 'agent', 'mode': 'whole_dataset'}]})
    writer.dependency(source_id, 'dataset', 'publication_pinned', status='missing', reason='Pinned source unavailable')
    writer.finish({'format': 'asaree.experiment-package', 'schema_version': 2,
        'experiment_id': str(scope.experiment_id), 'protocol_id': str(scope.protocol_id),
        'publication': {'id': str(scope.protocol_revision_id), 'version': 1}, 'results_included': False})
    job.preflight = inspect(root / 'archive.tar.gz', root / 'payload', Limits(), threading.Event())
    assert job.preflight['unresolved_count'] == 1
    importer = Reconstruction(db, job, root / 'payload', Limits())
    job.experiment_id = await importer.activate(resource_db)
    job.status = 'completed'
    await db.flush()
    local = await db.get(RegisteredDataset, uuid.UUID(importer.maps['dataset', source_id]))
    assert local.id != existing.id and local.raw_artifact_id is None
    assert local.raw_sha256 == pin['raw_sha256'] and local.name != existing.name
    @asynccontextmanager
    async def resources(**kwargs):
        yield resource_db
    monkeypatch.setattr('asaree.services.package_reconstruction.system_session', resources)
    from fastapi import HTTPException
    with pytest.raises(HTTPException, match='unresolved') as rejected:
        await api.configuration(job.id, api.Configuration(connections_configured=True, dependencies_resolved=True),
                                db, owner)
    assert rejected.value.status_code == 422 and job.configured_at is None
