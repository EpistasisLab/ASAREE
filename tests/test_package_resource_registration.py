"""Package orchestration uses native registration and ordinary storage locations."""
import asyncio
import base64
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from motoro.models.mcp_server import MCPServerConfig
from motoro.models.skill import Skill
from motoro.services import mcp_service, skill_service
from sqlalchemy.exc import IntegrityError

from asaree.config import AsareeSettings
from asaree.models.dataset import RegisteredDataset
from asaree.services import datasets, okf_bundles, okf_documents
from asaree.services.package_reconstruction import Reconstruction
from asaree.services.package_validation import Limits, PackageError


class RecordingSession:
    """Record writes; database constraints are covered by the PostgreSQL suite."""
    def __init__(self):
        self.added = []
        self.refresh = AsyncMock()
        self.flush = AsyncMock(side_effect=self.defaults)

    def add(self, value):
        self.added.append(value)

    async def get(self, model, identifier):
        return next((value for value in self.added if isinstance(value, model) and value.id == identifier), None)

    async def defaults(self):
        for value in self.added:
            if hasattr(value, 'id') and value.id is None:
                value.id = uuid.uuid4()
            for file in getattr(value, 'files', []):
                if file.id is None:
                    file.id = uuid.uuid4()

    @asynccontextmanager
    async def begin_nested(self):
        yield


@pytest.fixture
def registration(tmp_path, monkeypatch):
    settings = AsareeSettings(_env_file=None, data_root=str(tmp_path / 'normal-storage'))
    for module in ('asaree.config', 'asaree.services.datasets', 'asaree.services.okf_bundles',
                   'asaree.services.okf_documents'):
        monkeypatch.setattr(f'{module}.get_settings', lambda: settings)
    monkeypatch.setattr(mcp_service, '_lock_server_name', AsyncMock())
    monkeypatch.setattr(mcp_service, '_name_conflicts', AsyncMock(return_value=False))
    job = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    product, resources = RecordingSession(), RecordingSession()
    root = tmp_path / 'extraction'
    root.mkdir()
    importer = Reconstruction(product, job, root, Limits())
    return importer, product, resources, settings


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def skill_payload(root, text=None, resources=()):
    source = str(uuid.uuid4())
    directory = f'resources/skills/{source}'
    entrypoint = root / directory / 'SKILL.md'
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text(text or '---\nname: source-skill\ndescription: Import me\n---\nInstructions')
    write_json(root / f'resources/skill-metadata/{source}.json', {'id': source, 'name': 'source-skill'})
    dependencies = [{'id': source, 'kind': 'skill', 'status': 'included', 'directory': directory}]
    records = []
    for name, content in resources:
        path = root / directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        identity = str(uuid.uuid4())
        records.append({'path': f'{directory}/{name}', 'source_id': identity})
        dependencies.append({'id': identity, 'kind': 'skill_file', 'status': 'included',
            'skill_id': source, 'file': f'{directory}/{name}', 'relative_path': name,
            'encoding': 'utf-8', 'position': 9})
    (root / 'files.jsonl').write_text(''.join(json.dumps(value) + '\n' for value in records))
    (root / 'dependencies.jsonl').write_text(''.join(json.dumps(value) + '\n' for value in dependencies))
    return source


@pytest.mark.parametrize('fault', ['frontmatter', 'optional-metadata', 'hidden-file', 'duplicate-path', 'file-count'])
async def test_invalid_skill_is_rejected_by_native_registration(registration, fault):
    importer, _, resources, _ = registration
    text = 'No frontmatter' if fault == 'frontmatter' else None
    if fault == 'optional-metadata':
        text = '---\nname: source-skill\ndescription: Import me\nmetadata: [invalid]\n---\nInstructions'
    files = {'hidden-file': [('.hidden/ref.md', b'reference')],
        'duplicate-path': [('A.md', b'one'), ('a.md', b'two')],
        'file-count': [(f'{number}.md', b'reference') for number in range(51)]}.get(fault, [])
    skill_payload(importer.root, text, files)
    with pytest.raises(PackageError, match='Could not register skill "source-skill"'):
        await importer.skills(resources)
    assert not resources.added


async def test_skill_conflict_fails_without_renaming(registration):
    importer, _, resources, _ = registration
    skill_payload(importer.root)
    resources.flush.side_effect = IntegrityError('INSERT', {}, Exception('uq_skills_owner_name_active'))
    with pytest.raises(PackageError, match='source-skill.*already registered'):
        await importer.skills(resources)
    assert resources.added[0].name == 'source-skill'


async def test_skill_registration_calls_the_same_native_writer(registration, monkeypatch):
    importer, _, resources, _ = registration
    source = skill_payload(importer.root, resources=[('references/SKILL.md', b'Nested\r\n'),
        ('assets/icon.bin', b'\x00\xff\x80')])
    register = AsyncMock(wraps=skill_service.create_skill_from_bundle)
    monkeypatch.setattr(skill_service, 'create_skill_from_bundle', register)
    await importer.skills(resources)
    skill = resources.added[0]
    assert isinstance(skill, Skill) and skill.name == 'source-skill'
    assert skill.files[0].content == 'Nested\r\n' and skill.files[0].position == 0
    assert base64.b64decode(skill.files[1].content) == b'\x00\xff\x80'
    register.assert_awaited_once()
    assert register.call_args.kwargs['db'] is resources
    # A retry adopts only the deterministic resource owned by this request.
    await importer.skills(resources)
    register.assert_awaited_once()
    assert importer.names['skill', source] == skill.name


def dataset_payload(root, content, target=None):
    source, artifact = str(uuid.uuid4()), str(uuid.uuid4())
    path = root / 'inputs' / f'{artifact}.csv'
    path.parent.mkdir()
    path.write_bytes(content)
    write_json(root / 'inputs/bindings.json', {'inputs': [{'dataset_id': source,
        'dataset_name': 'Source dataset', 'target_column': target}]})
    dep = {'id': artifact, 'kind': 'dataset_file', 'status': 'included', 'role': 'raw_artifact',
           'dataset_id': source, 'file': f'inputs/{artifact}.csv'}
    (root / 'dependencies.jsonl').write_text(json.dumps(dep) + '\n')
    return source


@pytest.mark.parametrize('fault', ['csv', 'target-column', 'name-conflict'])
async def test_dataset_native_validation_reports_resource_name(registration, fault):
    importer, product, _, _ = registration
    dataset_payload(importer.root, b'' if fault == 'csv' else b'x,y\n1,0\n2,1\n',
        'missing' if fault == 'target-column' else None)
    if fault == 'name-conflict':
        product.flush.side_effect = IntegrityError('INSERT', {}, Exception('uq_registered_datasets_owner_name'))
    with pytest.raises(PackageError, match='Could not register dataset "Source dataset"'):
        await importer.datasets()


async def test_dataset_files_use_the_manual_registration_layout(registration):
    importer, product, _, settings = registration
    raw = b'x,y\r\n01,0\r\n02,1\r\n'
    source = dataset_payload(importer.root, raw)
    await importer.datasets()
    dataset = next(value for value in product.added if isinstance(value, RegisteredDataset))
    expected = datasets._dataset_dir(dataset.id) / 'raw.csv'
    assert expected.read_bytes() == raw
    assert dataset.raw_artifact.path == str(expected)
    assert dataset.raw_artifact.storage_key == f'{dataset.id}/raw.csv'
    assert str(importer.root) not in dataset.raw_artifact.path
    assert importer.names['dataset', source] == 'Source dataset'
    shutil.rmtree(importer.root)
    assert expected.read_bytes() == raw


async def test_retry_reclaims_only_uncommitted_native_dataset_files(registration):
    from asaree.services.package_reconstruction import identity

    importer, product, _, _ = registration
    raw = b'x,y\n1,0\n2,1\n'
    source = dataset_payload(importer.root, raw)
    local = identity(importer.job, 'dataset', source)
    orphaned = datasets._dataset_dir(local) / 'splits' / 'uncommitted'
    orphaned.mkdir(parents=True)
    (orphaned / 'train.parquet').write_bytes(b'Interrupted registration')
    await importer.datasets()
    assert not orphaned.exists()
    dataset = next(value for value in product.added if isinstance(value, RegisteredDataset))
    assert (datasets._dataset_dir(dataset.id) / 'raw.csv').read_bytes() == raw


@pytest.mark.parametrize('invalid_role', [None, 'train', 'test'])
async def test_partition_registration_uses_native_layout_and_preserves_bytes(registration, tmp_path, invalid_role):
    _, product, _, _ = registration
    dataset = RegisteredDataset(id=uuid.uuid4(), name='Source dataset', raw_sha256='a' * 64)
    paths, originals = {}, {}
    for role in ('train', 'test'):
        path = tmp_path / f'{role}.parquet'
        pd.DataFrame({'x': [1, 2]}).to_parquet(path, index=False)
        if role == invalid_role:
            path.write_bytes(b'Invalid Parquet')
        paths[role], originals[role] = path, path.read_bytes()
    if invalid_role:
        with pytest.raises(datasets.DatasetValidationError, match=f'Could not read the {invalid_role} partition'):
            await datasets.register_dataset_split(product, dataset, paths['train'], paths['test'])
    else:
        await datasets.register_dataset_split(product, dataset, paths['train'], paths['test'])
        split = dataset.current_split
        for role, artifact in (('train', split.train_artifact), ('test', split.test_artifact)):
            expected = datasets._dataset_dir(dataset.id) / 'splits' / str(split.id) / f'{role}.parquet'
            assert artifact.path == str(expected) and expected.read_bytes() == originals[role]


def knowledge_payload(root, kind, text, filename='concept.md'):
    source = str(uuid.uuid4())
    directory = f'resources/knowledge/{source}'
    path = root / directory / filename
    path.parent.mkdir(parents=True)
    path.write_bytes(text)
    write_json(root / f'requirements/mcp/{source}.json', {'id': source, 'name': 'Source knowledge',
        'transport': 'stdio', 'knowledge_kind': kind})
    (root / 'dependencies.jsonl').write_text(json.dumps({'id': source, 'kind': 'knowledge',
        'status': 'included', 'directory': directory, 'name': 'source-folder'}) + '\n')
    return source


@pytest.mark.parametrize('kind', ['bundle', 'document'])
async def test_knowledge_uses_native_registration_and_upload_layout(registration, monkeypatch, kind):
    importer, _, resources, settings = registration
    text = b'---\ntitle: Source concept\n---\nKnowledge\r\n'
    source = knowledge_payload(importer.root, kind, text)
    module = okf_documents if kind == 'document' else okf_bundles
    method = 'register_document' if kind == 'document' else 'register_uploaded_bundle'
    register = AsyncMock(wraps=getattr(module, method))
    monkeypatch.setattr(module, method, register)
    await importer.servers(resources)
    server = next(value for value in resources.added if isinstance(value, MCPServerConfig))
    stored = okf_bundles.bundle_path_from_command(server.command)
    parent = okf_documents.owner_root(importer.job.owner_id) if kind == 'document' else (
        okf_bundles.owner_upload_root(importer.job.owner_id))
    assert str(parent / ('source-concept' if kind == 'document' else 'source-folder')) == stored
    assert '/imports/' not in stored and str(importer.root) not in stored
    assert importer.knowledge_paths[source] == stored
    register.assert_awaited_once()
    assert register.call_args.kwargs['connect'] is False and register.call_args.kwargs['db'] is resources
    shutil.rmtree(importer.root)
    concept = okf_documents.concept_file_for(server) if kind == 'document' else Path(stored) / 'concept.md'
    assert concept.read_bytes() == text


@pytest.mark.parametrize('kind,fault', [('bundle', 'non-markdown'), ('bundle', 'non-utf8'),
    ('document', 'frontmatter'), ('document', 'non-utf8')])
async def test_knowledge_validation_failure_is_friendly(registration, kind, fault):
    importer, _, resources, _ = registration
    content = b'\xff' if fault == 'non-utf8' else b'Missing frontmatter'
    knowledge_payload(importer.root, kind, content, 'concept.bin' if fault == 'non-markdown' else 'concept.md')
    with pytest.raises(PackageError, match='Could not register knowledge "Source knowledge"'):
        await importer.servers(resources)
    assert not resources.added


@pytest.mark.parametrize('kind', ['bundle', 'document'])
async def test_native_knowledge_registration_cleans_up_cancelled_storage(registration, monkeypatch, kind):
    importer, _, resources, _ = registration
    monkeypatch.setattr(mcp_service, 'register_server', AsyncMock(side_effect=asyncio.CancelledError))
    text = '---\ntitle: Source concept\n---\nKnowledge'
    root = okf_documents.owner_root(importer.job.owner_id) if kind == 'document' else (
        okf_bundles.owner_upload_root(importer.job.owner_id))
    with pytest.raises(asyncio.CancelledError):
        if kind == 'document':
            await okf_documents.register_document(owner_id=importer.job.owner_id, text=text, db=resources,
                identifier=uuid.uuid4(), connect=False)
        else:
            await okf_bundles.register_uploaded_bundle(owner_id=importer.job.owner_id,
                files=[('source-folder/concept.md', text)], db=resources, identifier=uuid.uuid4(), connect=False)
    assert not list(root.iterdir())


async def test_persona_uses_native_registration_validation(registration):
    importer, product, _, _ = registration
    write_json(importer.root / 'resources/personas/source.json', {'id': 'source', 'name': 'Source persona',
        'config': {'persona_text': 'x' * 1_000_001}})
    with pytest.raises(PackageError, match='Could not register persona "Source persona".*1 MB'):
        await importer.personas()
