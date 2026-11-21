"""Independent-owner reconstruction against both existing PostgreSQL databases."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
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
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.protocol import Protocol
from asaree.models.protocol_revision import ProtocolRevision
from asaree.models.protocol_run import ProtocolRun
from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services import experiment_packages as packages
from asaree.services import okf_bundles, okf_documents
from asaree.services import package_imports as imports
from asaree.services.dataset_inputs import freeze_dataset_inputs
from asaree.services.datasets import create_dataset, quick_split_dataset
from asaree.services.design_generation import generate_design_cells
from asaree.services.package_reconstruction import Reconstruction, require_configured
from asaree.services.package_validation import Limits, inspect

results_db = _results_db


def storage_settings(root):
    from asaree_workspace_core.storage import NAMESPACES

    return AsareeSettings(_env_file=None, data_root=str(root),
                          **{field: '' for field, _ in NAMESPACES.values()})


async def select_test_provider(db, job):
    setting = UserLLMSetting(user_id=job.owner_id, provider='openai', api_key_encrypted='destination-only')
    db.add(setting)
    await db.flush()


async def test_provider_factor_alternatives_use_saved_owned_connections_automatically(
    results_db, monkeypatch, tmp_path,
):
    from asaree.services import package_reconstruction

    db, scope, _, owner = results_db
    settings = storage_settings(tmp_path / 'provider-setup')
    monkeypatch.setattr('asaree.services.managed_storage.get_settings', lambda: settings)
    experiment = await db.get(ResearchExperiment, scope.experiment_id)
    protocol = await db.get(Protocol, scope.protocol_id)
    experiment.design_spec = {'factors': [{'name': 'Models', 'levels': [
        {'provider': 'openai', 'model': 'first'}, {'provider': 'anthropic', 'model': 'second'}]}]}
    protocol.graph = {'nodes': [{'id': 'model', 'type': 'model_openai', 'data': {
        'config': {'provider': 'openai', 'model': 'first'}, 'factor_bindings': {'config': 'Models'}}}], 'edges': []}
    job = PackageImport(id=uuid.uuid4(), owner_id=owner.id, experiment_id=experiment.id, status='completed',
        request_key=uuid.uuid4().hex, preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
    db.add(job)
    await db.flush()
    await select_test_provider(db, job)
    with pytest.raises(ValueError, match='anthropic'):
        await package_reconstruction.require_configured(db, experiment.id)
    anthropic = UserLLMSetting(user_id=owner.id, provider='anthropic', api_key_encrypted='destination-anthropic')
    db.add(anthropic)
    await db.flush()
    await package_reconstruction.require_configured(db, experiment.id)
    assert job.configured_at is None
    assert anthropic.api_key_encrypted == 'destination-anthropic'


@pytest.mark.parametrize('repair', ['split', 'rebind'])
async def test_missing_pinned_split_blocks_configuration_until_repaired(
    results_db, resource_db, tmp_path, monkeypatch, repair,
):
    from motoro.schemas.agent import ModelConfig
    from test_package_import_validation import package

    from asaree.services import package_reconstruction, sampling_snapshots

    db, _, _, owner = results_db
    settings = AsareeSettings(_env_file=None, data_root=str(tmp_path / 'storage'))
    monkeypatch.setattr('asaree.config.get_settings', lambda: settings)
    monkeypatch.setattr('asaree.services.datasets.get_settings', lambda: settings)
    source, split = str(uuid.uuid4()), str(uuid.uuid4())
    graph = {'nodes': [
        {'id': 'd', 'type': 'dataset', 'data': {'config': {'dataset_id': source}}},
        {'id': 'a', 'type': 'agent', 'data': {}},
        {'id': 'm', 'type': 'model_openai', 'data': {'config': {'model': 'gpt-test'}}},
    ], 'edges': [{'source': 'd', 'target': 'a', 'targetHandle': 'dataset'},
                 {'source': 'm', 'target': 'a', 'targetHandle': 'model'}]}
    content = b'x,y\n1,0\n2,1\n3,0\n4,1\n5,0\n6,1\n7,0\n8,1\n'

    def modify(writer):
        refs = {}
        # Keep both partition members: the split record alone is unavailable.
        for role in ('raw_artifact', 'train_artifact', 'test_artifact'):
            identifier = str(uuid.uuid4())
            file = tmp_path / role
            file.write_bytes(content)
            member = f'inputs/files/{identifier}.csv'
            writer.add(file, member, provenance='publication_pinned', identity=identifier)
            writer.dependency(identifier, 'dataset_file', 'publication_pinned',
                              file=member, role=role, dataset_id=source)
            refs[role] = {'id': identifier, 'sha256': hashlib.sha256(content).hexdigest()}
        writer.json('inputs/bindings.json', {'schema_version': 1, 'inputs': [{
            'dataset_node_id': 'd', 'dataset_id': source, 'dataset_name': 'Source',
            'mode': 'whole_dataset', 'split_id': split, 'raw_sha256': refs['raw_artifact']['sha256'],
            'target_column': 'y', **refs,
        }]})
        writer.dependency(split, 'split', 'publication_pinned', status='missing')

    archive = package(tmp_path, graph=graph, modify=modify)
    job = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key=uuid.uuid4().hex,
        status='queued', progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
    db.add(job)
    await db.flush()
    payload = imports.directory(job.id) / 'payload'
    payload.parent.mkdir(parents=True)
    job.preflight = inspect(archive, payload, Limits(), threading.Event())
    assert job.preflight['unresolved_count'] == 1
    importer = Reconstruction(db, job, payload, Limits())
    job.experiment_id = await importer.activate(resource_db)
    job.status = 'completed'
    await db.flush()
    protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == job.experiment_id))
    dataset = await db.get(RegisteredDataset, uuid.UUID(importer.maps['dataset', source]))
    assert dataset.current_split_id is None

    @asynccontextmanager
    async def resources(**kwargs):
        yield resource_db

    monkeypatch.setattr(package_reconstruction, 'system_session', resources)
    monkeypatch.setattr(sampling_snapshots, 'build_model_config', AsyncMock(return_value=ModelConfig(model='gpt-test')))
    monkeypatch.setattr(sampling_snapshots, 'resolve_discovery_context', AsyncMock(return_value={}))
    readiness = await api.readiness(job.id, db, owner)
    assert not readiness['ready']
    assert any('split' in issue['message'] for issue in readiness['issues'])
    with pytest.raises(HTTPException) as error:
        await publish_protocol_endpoint(protocol.id, owner, db)
    assert error.value.status_code == 422 and protocol.published_revision_id is None
    requirement = await db.get(ImportedIdentity, (job.id, 'unresolved_split', split))
    assert requirement.provenance['pin']['split_id'] == split

    if repair == 'split':
        await quick_split_dataset(db, dataset=dataset, seed=17)
        expected_split = dataset.current_split_id
    else:
        replacement = await create_dataset(db, name='Explicitly rebound original', csv_bytes=content,
                                           owner_id=owner.id, target_column='y')
        draft = copy.deepcopy(protocol.graph)
        draft['nodes'][0]['data']['config'].update(dataset_id=str(replacement.id), dataset_name=replacement.name)
        protocol.graph = draft
        expected_split = None
    await db.flush()
    await select_test_provider(db, job)
    assert job.configured_at is None
    await publish_protocol_endpoint(protocol.id, owner, db)
    publication = await db.get(ProtocolRevision, protocol.published_revision_id)
    pin = publication.dataset_inputs['inputs'][0]
    assert pin['split_id'] == (str(expected_split) if expected_split else None)
    assert bool(pin['train_artifact']) == (repair == 'split')
    assert pin['dataset_id'] == str(dataset.id if repair == 'split' else replacement.id)


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


@pytest_asyncio.fixture
async def destination_results_db():
    async for value in _results_db.__wrapped__():
        yield value


@pytest_asyncio.fixture
async def destination_resource_db():
    async for value in resource_db.__wrapped__():
        yield value


async def test_legacy_json_creates_only_a_draft_and_uses_shared_collision_handling(results_db, monkeypatch):
    from asaree.api import experiments as api_experiments
    from asaree.services.experiments import create_experiment_with_available_name

    db, _, _, owner = results_db
    existing = await create_experiment_with_available_name(db, name='Imported JSON', owner_id=owner.id)
    existing.archived_at = datetime.now(UTC)
    monkeypatch.setattr(api_experiments.mcp_service, 'list_servers', AsyncMock(return_value=[]))
    graph = {'nodes': [{'id': 'draft-node', 'type': 'agent',
                       'data': {'config': {'system_prompt': 'Preserve the current draft'}}}], 'edges': []}
    request = api_experiments.ImportExperimentDefinitionRequest(name='Imported JSON', graph=graph,
        hypothesis='Preserve this hypothesis',
        published_graph={'nodes': [{'id': 'old-node', 'type': 'agent', 'data': {}}], 'edges': []})
    for number in (2, 3):
        created = await api_experiments.import_experiment_definition_endpoint(request, owner, db)
        assert created.name == f'Imported JSON ({number})'
        assert created.hypothesis == 'Preserve this hypothesis'
        protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == created.id))
        assert protocol.graph == graph
        assert protocol.published_revision_id is None
        assert await db.scalar(select(func.count()).select_from(ProtocolRevision).where(
            ProtocolRevision.protocol_id == protocol.id)) == 0


async def test_persona_snapshots_and_subagents_survive_repeated_independent_imports(
    results_db, resource_db, tmp_path, monkeypatch,
):
    from asaree.models.persona import Persona
    from asaree.services.experiments import create_experiment_with_available_name
    from asaree.services.personas import get_persona, list_personas, snapshot_identity

    db, _, _, owner = results_db
    settings = storage_settings(tmp_path / 'personas')
    monkeypatch.setattr('asaree.services.managed_storage.get_settings', lambda: settings)
    config = {'mode': 'library', 'persona_ids': [str(uuid.uuid4()), str(uuid.uuid4())],
        'persona_text': '# First\r\nBe careful.\n\n# Second\nBe curious. λ',
        'persona_label': 'Combined persona', 'persona_file': 'combined.md'}
    graph = {'nodes': [
        {'id': 'parent', 'type': 'agent', 'data': {'config': {'system_prompt': 'Delegate to @child'},
            'factor_bindings': {'sub_agent_selection': 'Workers', 'persona_selection': 'Personas'},
            'sub_agent_selection': ['child'], 'persona_selection': ['persona']}},
        {'id': 'child', 'type': 'sub_agent', 'data': {'config': {'system_prompt': 'Preserve my instructions'}}},
        {'id': 'persona', 'type': 'persona', 'data': {'config': config}},
    ], 'edges': [{'source': 'child', 'target': 'parent', 'targetHandle': 'sub_agents'},
                 {'source': 'persona', 'target': 'parent', 'targetHandle': 'persona'}]}
    design = {'factors': [{'name': 'Workers', 'level_type': 'sub_agent_toggle', 'levels': [[], ['child']]},
                           {'name': 'Personas', 'level_type': 'persona_toggle', 'levels': [[], ['persona']]}]}

    @asynccontextmanager
    async def resources(**kwargs):
        yield resource_db

    monkeypatch.setattr(packages, 'system_session', resources)
    await resource_db.connection()
    await create_experiment_with_available_name(db, name='Portable', owner_id=owner.id)
    imported_ids, persona_ids = [], []
    for number in range(2):
        job = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key=uuid.uuid4().hex,
            status='queued', preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
        db.add(job)
        await db.flush()
        root = imports.directory(job.id)
        stage = root / 'writer'
        stage.mkdir(parents=True)
        writer = packages.PackageWriter(root / 'archive.tar.gz', stage)
        writer.json('publication/canvas.json', graph)
        writer.json('publication/settings.json', {'design_spec': design})
        writer.json('experiment-metadata.json', {'name': 'Portable'})
        await packages._resources(writer, graph, {'design_spec': design}, owner.id)
        assert writer.missing == 0
        writer.finish({'format': 'asaree.experiment-package', 'schema_version': 2,
            'experiment_id': str(uuid.uuid4()), 'protocol_id': str(uuid.uuid4()),
            'publication': {'id': str(uuid.uuid4()), 'version': 1}, 'results_included': False})
        job.preflight = inspect(root / 'archive.tar.gz', root / 'payload', Limits(), threading.Event())
        importer = Reconstruction(db, job, root / 'payload', Limits())
        job.experiment_id = await importer.activate(resource_db)
        await db.flush()
        local_id = uuid.UUID(importer.maps['persona', snapshot_identity(config)])
        assert await get_persona(db, local_id, owner_id=owner.id) is None  # Staged resources stay hidden.
        job.status = 'completed'
        await db.flush()
        persona = await get_persona(db, local_id, owner_id=owner.id)
        assert persona.prompt == config['persona_text']
        assert persona.source_filename == 'combined.md'
        assert persona.name == 'Combined persona'  # Personas allow duplicate display names.
        assert persona.id not in [uuid.UUID(value) for value in config['persona_ids']]
        markdown = root / f'payload/resources/personas/{snapshot_identity(config)}.md'
        assert markdown.read_bytes() == persona.prompt.encode()
        protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == job.experiment_id))
        experiment = await db.get(ResearchExperiment, job.experiment_id)
        assert experiment.name == f'Portable ({number + 2})'
        assert experiment.design_spec['factors'] == design['factors']
        assert protocol.published_revision_id is None
        assert [node['id'] for node in protocol.graph['nodes']] == ['parent', 'child', 'persona']
        assert protocol.graph['nodes'][0]['data']['sub_agent_selection'] == ['child']
        assert protocol.graph['nodes'][0]['data']['config']['system_prompt'] == 'Delegate to @child'
        restored = protocol.graph['nodes'][2]['data']['config']
        assert restored['persona_ids'] == [str(local_id)] and restored['persona_text'] == config['persona_text']
        assert restored['persona_sources'][0]['id'] == str(local_id)
        imported_ids.append(job.experiment_id)
        persona_ids.append(local_id)
    assert len(set(imported_ids)) == len(set(persona_ids)) == 2
    assert len(await list_personas(db, owner_id=owner.id)) == 2
    assert await db.scalar(select(func.count()).select_from(Persona).where(Persona.owner_id == owner.id)) == 2


@pytest.mark.parametrize('builtin_state', ['compatible', 'incompatible', 'ambiguous'])
async def test_destination_services_and_explicit_custom_mapping(
    results_db, resource_db, destination_results_db, destination_resource_db,
    tmp_path, monkeypatch, builtin_state,
):
    from asaree.services import package_reconstruction, package_setup
    from asaree.services.package_reconstruction import readiness_issues

    source_db, scope, _, source_owner = results_db
    db, _, _, owner = destination_results_db
    settings = storage_settings(tmp_path / 'destination')
    monkeypatch.setattr('asaree.config.get_settings', lambda: settings)
    monkeypatch.setattr('asaree.services.managed_storage.get_settings', lambda: settings)
    source_builtin = MCPServerConfig(id=uuid.uuid4(), name='Source built-in', is_system=True,
        transport=MCPTransport.STDIO, command='uv run --directory /source python -m asaree_sklearn_eda',
        stdio_env_encrypted='SOURCE_SECRET', capabilities={'tools': [{'name': 'describe'}]})
    source_custom = MCPServerConfig(id=uuid.uuid4(), name='Source custom', owner_id=source_owner.id,
        transport=MCPTransport.HTTP, url='https://source.invalid/SECRET',
        headers_encrypted='SOURCE_SECRET', capabilities={'tools': [{'name': 'custom'}]})
    resource_db.add_all([source_builtin, source_custom])
    await resource_db.flush()
    job = PackageImport(id=uuid.uuid4(), owner_id=owner.id, request_key=uuid.uuid4().hex,
        status='queued', preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(days=1))
    db.add(job)
    await db.flush()
    root = imports.directory(job.id)
    stage = root / 'writer'
    stage.mkdir(parents=True)
    graph = {'nodes': [
        {'id': 'agent', 'type': 'agent', 'data': {'config': {'system_prompt': 'Do nothing'}}},
        {'id': 'model', 'type': 'model_openai', 'data': {'config': {'provider': 'openai', 'model': 'gpt-test'}}},
        {'id': 'builtin', 'type': 'mcp_tool', 'data': {'config': {
            'server_id': str(source_builtin.id), 'server_name': source_builtin.name, 'tool_names': ['describe']}}},
        {'id': 'custom', 'type': 'mcp_tool', 'data': {'config': {
            'server_id': str(source_custom.id), 'server_name': source_custom.name, 'tool_names': ['custom']},
            'factor_bindings': {'config.server_id': 'Connection'}}}],
        'edges': [{'source': 'model', 'target': 'agent', 'targetHandle': 'model'}]}
    design_spec = {'factors': [{'name': 'Connection', 'levels': [str(source_custom.id)]}]}
    writer = packages.PackageWriter(root / 'archive.tar.gz', stage)
    writer.json('publication/canvas.json', graph)
    writer.json('publication/settings.json', {'design_spec': design_spec})
    writer.json('experiment-metadata.json', {'name': 'Service portability'})
    for server in (source_builtin, source_custom):
        await packages._server(resource_db, writer, {'server_id': str(server.id)}, source_owner.id)
    writer.finish({'format': 'asaree.experiment-package', 'schema_version': 2,
        'experiment_id': str(scope.experiment_id), 'protocol_id': str(scope.protocol_id),
        'publication': {'id': str(scope.protocol_revision_id), 'version': 1}, 'results_included': False})
    job.preflight = inspect(root / 'archive.tar.gz', root / 'payload', Limits(), threading.Event())
    requirements = ''.join(path.read_text() for path in (root / 'payload/requirements/mcp').glob('*.json'))
    assert 'SOURCE_SECRET' not in requirements
    destination_builtin = MCPServerConfig(id=uuid.uuid4(), name='Renamed built-in', is_system=True,
        transport=MCPTransport.STDIO, command='uv run --directory /destination python -m asaree_sklearn_eda',
        stdio_env_encrypted='DESTINATION_SECRET', capabilities={'tools': [
            {'name': 'describe' if builtin_state != 'incompatible' else 'other'}]})
    custom = MCPServerConfig(id=uuid.uuid4(), name='Destination custom', owner_id=owner.id,
        transport=MCPTransport.HTTP, url='https://destination.invalid', headers_encrypted='DESTINATION_HEADERS',
        capabilities={'tools': [{'name': 'custom'}]})
    foreign = MCPServerConfig(id=uuid.uuid4(), name='Foreign custom', owner_id=source_owner.id,
        transport=MCPTransport.HTTP, url='https://foreign.invalid', capabilities={'tools': [{'name': 'custom'}]})
    destination_resource_db.add_all([destination_builtin, custom, foreign])
    if builtin_state == 'ambiguous':
        destination_resource_db.add(MCPServerConfig(id=uuid.uuid4(), name='Second built-in', is_system=True,
            transport=MCPTransport.STDIO, command='python -m asaree_sklearn_eda',
            capabilities={'tools': [{'name': 'describe'}]}))
    await destination_resource_db.flush()
    importer = Reconstruction(db, job, root / 'payload', Limits())
    job.experiment_id = await importer.activate(destination_resource_db)
    job.status = 'completed'
    await db.flush()
    protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == job.experiment_id))
    builtin = next(node for node in protocol.graph['nodes'] if node['id'] == 'builtin')
    assert (builtin['data']['config']['server_id'] == str(destination_builtin.id)) == (builtin_state == 'compatible')
    owned_requirement = await db.get(ImportedIdentity, (job.id, 'mcp', str(source_builtin.id)))
    assert owned_requirement.local_id not in {source_builtin.id, destination_builtin.id}
    @asynccontextmanager
    async def destination_resources(**kwargs):
        yield destination_resource_db
    monkeypatch.setattr(package_reconstruction, 'system_session', destination_resources)
    monkeypatch.setattr(package_setup, 'system_session', destination_resources)
    options = await package_setup.setup_options(db, job)
    with pytest.raises(HTTPException) as publication_error:
        await publish_protocol_endpoint(protocol.id, owner, db)
    assert publication_error.value.status_code == 422
    assert publication_error.value.detail['message'] == 'Setup needed'
    assert any(issue['node_id'] == 'model' for issue in publication_error.value.detail['issues'])
    requirement = next(item for item in options['mcp'] if item['source_id'] == str(source_custom.id))
    assert [candidate['id'] for candidate in requirement['candidates']] == [str(custom.id)]
    with pytest.raises(ValueError):
        await package_setup.select_connection(db, job, 'mcp', str(source_custom.id), foreign.id)
    await package_setup.select_connection(db, job, 'mcp', str(source_custom.id), custom.id)
    experiment = await db.get(ResearchExperiment, job.experiment_id)
    assert experiment.design_spec['factors'][0]['levels'][0]['server_id'] == str(custom.id)
    assert experiment.design_spec['factors'][0]['levels'][0]['server_name'] == custom.name
    assert experiment.design_spec['factors'][0]['level_type'] == 'tool_config'
    custom_node = next(node for node in protocol.graph['nodes'] if node['id'] == 'custom')
    assert custom_node['data']['config']['server_name'] == custom.name
    await select_test_provider(db, job)
    issues = await readiness_issues(db, job)
    assert not issues if builtin_state == 'compatible' else issues[0]['node_id'] == 'builtin'
    assert destination_builtin.stdio_env_encrypted == 'DESTINATION_SECRET'
    assert destination_builtin.command.startswith('uv run --directory /destination')
    assert custom.headers_encrypted == 'DESTINATION_HEADERS' and custom.url == 'https://destination.invalid'
    assert await source_db.get(ResearchExperiment, job.experiment_id) is None


@pytest.mark.parametrize('mode', ['whole_dataset', 'per_row'])
async def test_export_import_independent_owner_and_reexport(
    results_db, resource_db, destination_results_db, destination_resource_db,
    tmp_path, monkeypatch, mode,
):
    db, scope, cell, owner = results_db
    destination_db, _, _, destination_owner = destination_results_db
    settings = storage_settings(tmp_path / 'storage')
    monkeypatch.setattr('asaree.config.get_settings', lambda: settings)
    monkeypatch.setattr('asaree.services.managed_storage.get_settings', lambda: settings)
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
    nested_resources = [('references/SKILL.md', 'utf-8', 'Nested reference\r\nλ', 8),
                        ('references/deep/SKILL.md', 'base64', base64.b64encode(binary).decode(), 9)]
    for path, encoding, content, position in nested_resources:
        resource_db.add(SkillFile(skill_id=skill.id, path=path, encoding=encoding,
                                  content=content, position=position))
    await resource_db.flush()
    async def read_fixture_skill(identifier):
        value = await resource_db.get(Skill, uuid.UUID(str(identifier)))
        return value if value is not None and value.deleted_at is None else None
    monkeypatch.setattr('motoro.services.skill_service.get_skill', read_fixture_skill)
    @asynccontextmanager
    async def policy_resources(**kwargs):
        yield resource_db
    monkeypatch.setattr('asaree.services.skill_references.system_session', policy_resources)
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
    destination_db.add(job)
    await destination_db.flush()
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
    manifest = writer.finish({'format': 'asaree.experiment-package', 'schema_version': 2,
        'experiment_id': str(scope.experiment_id), 'protocol_id': str(scope.protocol_id),
        'publication': {'id': str(publication.id), 'version': 1},
        'design_revision_id': str(scope.design_revision_id), 'results_included': False})
    # Cross-installation boundary: independent PostgreSQL schemas, owners,
    # registrations, managed roots and saved credentials. Only archive bytes cross.
    archive_bytes = archive.read_bytes()
    source_root = root
    settings = storage_settings(tmp_path / 'destination-storage')
    root = imports.directory(job.id)
    assert root != source_root
    root.mkdir(parents=True)
    archive = root / 'archive.tar.gz'
    archive.write_bytes(archive_bytes)
    db, resource_db = destination_db, destination_resource_db
    assert await db.get(RegisteredDataset, dataset.id) is None
    assert await resource_db.get(Skill, skill.id) is None
    assert await resource_db.get(MCPServerConfig, server.id) is None
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
    local_files = {file.path: file for file in (await resource_db.scalars(
        select(SkillFile).where(SkillFile.skill_id == local_skill.id))).all()}
    assert set(local_files) == {'assets/binary.bin', *(entry[0] for entry in nested_resources)}
    local_file = local_files['assets/binary.bin']
    assert base64.b64decode(local_file.content) == binary and local_file.position == 7
    for path, encoding, content, position in nested_resources:
        restored = local_files[path]
        assert (restored.encoding, restored.content, restored.position) == (encoding, content, position)
    monkeypatch.setattr('asaree.services.package_reconstruction.system_session', resource_session)
    with pytest.raises(ValueError, match='saved openai'):
        await require_configured(db, experiment_id)
    assert await db.scalar(select(func.count()).select_from(ProtocolRun).where(
        ProtocolRun.owner_id == destination_owner.id)) == 0
    identities = (await db.scalars(select(ImportedIdentity).where(ImportedIdentity.import_id == job.id))).all()
    assert not any(row.kind.startswith('historical_') for row in identities)
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
    await select_test_provider(db, job)
    async def read_local_server(identifier):
        return await resource_db.get(MCPServerConfig, identifier)
    monkeypatch.setattr('asaree.services.reported_metrics.mcp_service.get_server', read_local_server)
    await db.flush()
    assert job.configured_at is None  # Normal publication needs no separate acknowledgement.
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
    await packages._import_sources(db, second, experiment_id)
    second.finish({'format': 'asaree.experiment-package', 'schema_version': 2,
        'experiment_id': str(experiment_id), 'protocol_id': str(protocol.id),
        'publication': {'id': str(local_publication.id), 'version': local_publication.revision},
        'design_revision_id': str(local_publication.design_revision_id), 'results_included': False})
    inspect(root / 'second.tar.gz', root / 'second-payload', Limits(), threading.Event())
    import tarfile
    with tarfile.open(root / 'second.tar.gz') as reexport:
        reexport_dependencies = [json.loads(line) for line in reexport.extractfile('dependencies.jsonl')]
        pin = local_publication.dataset_inputs['inputs'][0]
        assert reexport.extractfile(f'inputs/files/{pin["raw_artifact"]["id"]}.csv').read() == source
        assert reexport.extractfile(f'resources/skills/{local_skill.id}/assets/binary.bin').read() == binary
        for path, encoding, content, position in nested_resources:
            expected = base64.b64decode(content) if encoding == 'base64' else content.encode('utf-8')
            assert reexport.extractfile(f'resources/skills/{local_skill.id}/{path}').read() == expected
            dependency = next(entry for entry in reexport_dependencies if entry.get('kind') == 'skill_file'
                              and entry.get('file') == f'resources/skills/{local_skill.id}/{path}')
            assert (dependency['encoding'], dependency['position']) == (encoding, position)
        assert not any(name.startswith('results/') or name.endswith('/source.tar.gz') for name in reexport.getnames())
    assert manifest['schema_version'] == 2
    # Later draft setup changes do not revoke an immutable local publication.
    broken = copy.deepcopy(protocol.graph)
    broken_dataset = next(node for node in broken['nodes'] if node['type'] == 'dataset')
    broken_dataset['data']['config']['dataset_id'] = str(uuid.uuid4())
    protocol.graph = broken
    await db.flush()
    with pytest.raises(ValueError, match='Dataset dependency'):
        await require_configured(db, experiment_id)
    await require_configured(db, experiment_id, validate_draft=False)
    assert local_publication.graph != broken


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
    assert file.path == 'references/SKILL.md'
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
    readiness = await api.readiness(job.id, db, owner)
    assert not readiness['ready']
    assert any('unresolved' in issue['message'] for issue in readiness['issues'])
