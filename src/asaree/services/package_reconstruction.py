"""Reconstruct editable local records; immutable source bytes are never rewritten."""
from __future__ import annotations

import asyncio
import base64
import shutil
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from motoro.models.database import system_session
from motoro.models.mcp_server import MCPServerConfig, MCPTransport
from motoro.models.skill import Skill, SkillFile
from motoro.services.skill_service import parse_skill_markdown
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.dataset import RegisteredDataset
from asaree.models.dataset_split import DatasetArtifact, DatasetSplit
from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.protocol import Protocol
from asaree.services import managed_storage
from asaree.services.experiment_packages import node_variants, portable_definition
from asaree.services.experiments import set_experiment_datasets
from asaree.services.package_validation import (
    Limits,
    PackageError,
    canonical_identity,
    document,
    member_path,
    payload_path,
    records,
)


def identity(job: PackageImport, kind: str, source: Any) -> uuid.UUID:
    return uuid.uuid5(job.id, f'{kind}:{source}')


def copy_knowledge(source: Path, destination: Path, stopping: threading.Event) -> None:
    """Copy exact bytes with cooperative cancellation and atomic publication."""
    from asaree.services.package_validation import digest

    temporary = destination.with_suffix('.preparing')
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, mode=0o700)
    try:
        for file in source.rglob('*'):
            if stopping.is_set():
                raise PackageError('Import cancelled')
            if not file.is_file():
                continue
            relative = file.relative_to(source)
            file = payload_path(source, relative.as_posix())
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with file.open('rb') as input_file, target.open('xb') as output:
                target.chmod(0o600)
                while block := input_file.read(1024 * 1024):
                    if stopping.is_set():
                        raise PackageError('Import cancelled')
                    output.write(block)
            if destination.exists() and (not (destination / relative).is_file()
                                        or digest(destination / relative) != digest(file)):
                raise PackageError('Staged knowledge bytes changed; refusing replacement')
        if destination.exists():
            for file in destination.rglob('*'):
                if file.is_file() and not (temporary / file.relative_to(destination)).is_file():
                    raise PackageError('Staged knowledge has unexpected files')
        else:
            temporary.replace(destination)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


class Reconstruction:
    def __init__(self, db: AsyncSession, job: PackageImport, root: Path, limits: Limits,
                 *, progress: Any = None, io: Any = None, stopping: threading.Event | None = None):
        self.db, self.job, self.root, self.limits = db, job, root, limits
        self.progress, self.io = progress, io or asyncio.to_thread
        self.stopping = stopping or threading.Event()
        self.maps: dict[tuple[str, str], str] = {}
        self.names: dict[tuple[str, str], str] = {}
        self.knowledge_namespaces: dict[str, str] = {}

    async def report(self, phase: str, scanned: int) -> None:
        if self.progress is not None and scanned % 25 == 0:
            await self.progress(phase, scanned=scanned)

    def map(self, kind: str, source_id: Any, **provenance: Any) -> uuid.UUID:
        key = kind, str(source_id)
        local = identity(self.job, 'mcp' if kind == 'knowledge' else kind, str(source_id))
        if key not in self.maps:
            self.db.add(ImportedIdentity(import_id=self.job.id, kind=kind, source_id=str(source_id),
                                         local_id=local, provenance=provenance))
            if kind not in {'cell', 'replicate', 'historical_attempt', 'historical_row', 'historical_artifact',
                            'skill_file', 'payload_member'}:
                self.maps[key] = str(local)
        return local

    def read(self, path: str) -> Any:
        return document(payload_path(self.root, path), self.limits)

    def name(self, kind: str, source: str, name: str, maximum: int = 255) -> str:
        # Import UUID namespaces avoid name collisions without cross-owner lookup.
        prefix = name[:maximum - 34].rstrip('-') if kind == 'skill' else name[:maximum - 34]
        value = f'{prefix}-{identity(self.job, kind, source).hex}'
        self.names[kind, source] = value
        return value

    async def datasets(self) -> list[uuid.UUID]:
        path = self.root / 'inputs/bindings.json'
        if not path.exists():
            return []
        bindings = self.read('inputs/bindings.json')
        dataset_ids = []
        for pin in bindings.get('inputs', []):
            source = canonical_identity(pin['dataset_id'])
            if ('dataset', source) in self.maps:
                continue
            local = self.map('dataset', source, pin=pin)
            metadata_path = f'inputs/datasets/{source}.json'
            metadata = self.read(metadata_path) if payload_path(self.root, metadata_path).exists() else pin
            dataset = RegisteredDataset(id=local, owner_id=self.job.owner_id,
                name=self.name('dataset', source, metadata.get('name') or pin['dataset_name']),
                description=pin.get('description', metadata.get('description')),
                dictionary_json=pin.get('dictionary_json', metadata.get('dictionary_json')),
                target_column=pin.get('target_column', metadata.get('target_column')),
                raw_sha256=pin.get('raw_sha256'))
            self.db.add(dataset)
            dataset_ids.append(local)
        await self.db.flush()
        for dep in records(self.root / 'dependencies.jsonl', self.limits):
            if dep.get('kind') != 'dataset_file' or dep.get('status') != 'included':
                continue
            source = dep['id']
            dataset_id = self.maps.get(('dataset', dep['dataset_id']))
            if dataset_id is None:
                raise PackageError('Dataset file has no pinned registration')
            # Dataset namespace stores stable relative keys to retained verified bytes.
            file = payload_path(self.root, dep['file'])
            relative = file.relative_to(managed_storage.roots()['datasets'])
            from asaree.services.package_validation import digest

            artifact_id = self.map('artifact', source, dependency=dep)
            self.db.add(DatasetArtifact(id=artifact_id, dataset_id=uuid.UUID(dataset_id), namespace='datasets',
                storage_key=relative.as_posix(), sha256=await asyncio.to_thread(digest, file),
                byte_size=file.stat().st_size,
                format=file.suffix.lstrip('.'), status='ready'))
            if dep['role'] == 'raw_artifact':
                loaded = await self.db.get(RegisteredDataset, uuid.UUID(dataset_id))
                if loaded is None:
                    raise PackageError('Dataset registration disappeared')
                loaded.raw_artifact_id = artifact_id
        await self.db.flush()
        for file in (self.root / 'inputs/splits').glob('*.json'):
            source = document(file, self.limits)
            local = self.map('split', source['id'], source=source)
            dataset_id = self.maps.get(('dataset', source['dataset_id']))
            if not dataset_id:
                raise PackageError('Split has no pinned registration')
            fields = {key: source.get(key) for key in (
                'source_sha256', 'target_column', 'method', 'strategy', 'group_column', 'stratify_column',
                'test_size', 'seed', 'legacy')}
            for key in ('train_artifact_id', 'test_artifact_id'):
                mapped = self.maps.get(('artifact', source.get(key)))
                fields[key] = uuid.UUID(mapped) if mapped else None
            fields['created_at'] = datetime.fromisoformat(source['created_at'])
            self.db.add(DatasetSplit(id=local, dataset_id=uuid.UUID(dataset_id), **fields))
        await self.db.flush()
        # Only pinned whole-dataset selections become local defaults. Original
        # selections remain unsplit even if another source default now exists.
        for pin in bindings.get('inputs', []):
            loaded = await self.db.get(RegisteredDataset, uuid.UUID(self.maps['dataset', pin['dataset_id']]))
            if loaded is None:
                raise PackageError('Dataset registration disappeared')
            split = self.maps.get(('split', pin.get('split_id')))
            if split:
                loaded.current_split_id = uuid.UUID(split)
            elif pin.get('split_id'):
                # A missing source split is a requirement, never an explicit
                # original-only selection. Keep it across worker restarts.
                self.map('unresolved_split', pin['split_id'], pin=pin)
        await self.db.flush()
        return dataset_ids

    async def skills(self, motoro: AsyncSession) -> None:
        for dep in records(self.root / 'dependencies.jsonl', self.limits):
            if dep.get('kind') != 'skill' or dep.get('status') != 'included':
                continue
            source_id = canonical_identity(dep['id'])
            if dep['directory'] != f'resources/skills/{source_id}':
                raise PackageError('Skill directory and identity disagree')
            metadata = payload_path(self.root, f'resources/skill-metadata/{source_id}.json')
            if metadata.exists():
                source = document(metadata, self.limits)
            else:
                # v1's rendered frontmatter contains explicit discovery facts.
                # Parse those facts, preserving unavailable filename/order as unknown.
                entrypoint = payload_path(self.root, f'{dep["directory"]}/SKILL.md')
                if entrypoint.stat().st_size > self.limits.document:
                    raise PackageError('Skill entrypoint exceeds resource limit')
                parsed = parse_skill_markdown(entrypoint.read_text(encoding='utf-8'))
                source = {'id': dep['id'], 'name': parsed.name, 'description': parsed.description,
                          'body': parsed.body, 'frontmatter': parsed.frontmatter}
            local = self.map('skill', source['id'], source=source)
            name = self.name('skill', source['id'], source['name'], 64)
            existing = await motoro.get(Skill, local)
            if existing is not None:
                if existing.owner_id != self.job.owner_id:
                    raise PackageError('Import resource ownership conflict')
                continue
            # No upload service: those may validate by invoking external tools.
            motoro.add(Skill(id=local, owner_id=self.job.owner_id, is_system=False,
                name=name,
                description=source['description'], body=source['body'], frontmatter=source['frontmatter'],
                source_filename=source.get('source_filename'), deleted_at=datetime.now(UTC)))
        await motoro.flush()
        for record in records(self.root / 'files.jsonl', self.limits):
            if not record['path'].startswith('resources/skills/'):
                continue
            parts = record['path'].split('/')
            source_skill = parts[2]
            relative = '/'.join(parts[3:])
            canonical_identity(source_skill)
            if relative == 'SKILL.md':
                continue
            file_dep: dict[str, Any] | None = next((entry for entry in records(
                self.root / 'dependencies.jsonl', self.limits) if entry.get('kind') == 'skill_file'
                and entry.get('file') == record['path'] and entry.get('status') == 'included'), None)
            if file_dep is None:
                if self.read('manifest.json')['schema_version'] >= 2:
                    raise PackageError('Missing structured Skill file metadata')
                file_dep = {'id': record['source_id'], 'skill_id': source_skill, 'file': record['path'],
                       'relative_path': relative, 'encoding': 'base64', 'position': 0,
                       'source_encoding': 'legacy.unknown', 'source_position': 'legacy.unknown'}
            skill_id = self.maps.get(('skill', file_dep['skill_id']))
            if not skill_id:
                raise PackageError('Skill file has no structured Skill record')
            local = self.map('skill_file', file_dep['id'], dependency=file_dep)
            if await motoro.get(SkillFile, local) is not None:
                continue
            path = payload_path(self.root, file_dep['file'])
            if path.stat().st_size > self.limits.document:
                raise PackageError('Skill resource exceeds per-resource limit')
            raw = path.read_bytes()
            content = base64.b64encode(raw).decode() if file_dep['encoding'] == 'base64' else raw.decode('utf-8')
            motoro.add(SkillFile(id=local, skill_id=uuid.UUID(skill_id), path=member_path(file_dep['relative_path']),
                                 encoding=file_dep['encoding'], position=file_dep['position'], content=content))
            await motoro.flush()

    async def servers(self, motoro: AsyncSession) -> None:
        from asaree.services import okf_documents
        from asaree.services.okf_bundles import command_for_bundle, server_name_for

        for file in (self.root / 'requirements/mcp').glob('*.json'):
            source = self.read(file.relative_to(self.root).as_posix())
            source_id = canonical_identity(source['id'])
            if file.name != f'{source_id}.json':
                raise PackageError('MCP requirement filename and identity disagree')
            local = self.map('mcp', source['id'], source=source)
            command = None
            name = self.name('mcp', source['id'], source['name'])
            knowledge = payload_path(self.root, f'resources/knowledge/{source_id}')
            included = next((
                dep for dep in records(self.root / 'dependencies.jsonl', self.limits)
                if dep.get('kind') == 'knowledge' and dep.get('id') == source_id and dep.get('status') == 'included'
            ), None)
            if included is not None:
                if included.get('directory') != f'resources/knowledge/{source_id}':
                    raise PackageError('Knowledge directory and identity disagree')
                knowledge_kind = source.get('knowledge_kind')
                if knowledge_kind not in {'bundle', 'document'}:
                    source_graph = self.read('publication/canvas.json')
                    source_settings = self.read('publication/settings.json')
                    knowledge_kind = next((
                        'document' if node.get('type') == 'okf_document' else 'bundle'
                        for node in source_graph.get('nodes', []) if node.get('type') in {'okf_bundle', 'okf_document'}
                        for variant in node_variants(node, source_settings.get('design_spec'))
                        if source['id'] in {((variant.get('data') or {}).get('config') or {}).get('bundle_id'),
                                             ((variant.get('data') or {}).get('config') or {}).get('document_id')}
                    ), None)
                if knowledge_kind is None:
                    raise PackageError('Knowledge registration type is unknown; source files remain inspectable')
                namespace = 'documents' if knowledge_kind == 'document' else 'bundles'
                self.knowledge_namespaces[source['id']] = namespace
                self.map('knowledge', source['id'], directory=knowledge.relative_to(self.root).as_posix())
                destination = managed_storage.roots()[namespace] / str(self.job.owner_id) / 'imports' / str(local)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                await self.io(copy_knowledge, knowledge, destination, self.stopping)
                command = command_for_bundle(destination, managed=True)
                name = (okf_documents.server_name_for(self.job.owner_id, destination) if knowledge_kind == 'document'
                        else server_name_for(self.job.owner_id, destination))
            self.names['mcp', source['id']] = name
            existing = await motoro.get(MCPServerConfig, local)
            if existing is not None:
                if existing.owner_id != self.job.owner_id or existing.is_system:
                    raise PackageError('Import server ownership conflict')
                continue
            motoro.add(MCPServerConfig(id=local, owner_id=self.job.owner_id, is_system=False,
                name=name, transport=MCPTransport(source['transport']), command=command,
                capabilities={'tools': [{'name': value} for value in source.get('tool_names', [])]},
                error_message='Import staging: configure locally after activation.'))
        await motoro.flush()

    def config(self, kind: str, config: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(config)
        fields = {'dataset': ('dataset_id', 'dataset'), 'skill': ('skill_id', 'skill'),
                  'okf_bundle': ('bundle_id', 'knowledge'), 'okf_document': ('document_id', 'knowledge'),
                  'mcp_tool': ('server_id', 'mcp'), 'mcp_client_tool': ('server_id', 'mcp'),
                  'mcp_scikit_learn': ('server_id', 'mcp')}
        if kind in fields:
            field, category = fields[kind]
            source = str(result.get(field) or '')
            if not source and category == 'dataset':
                pins = self.read('inputs/bindings.json') if (self.root / 'inputs/bindings.json').exists() else {}
                source = next((pin['dataset_id'] for pin in pins.get('inputs', [])
                               if pin['dataset_name'] == result.get('dataset_name')), '')
            if not source and category == 'mcp':
                for path in (self.root / 'requirements/mcp').glob('*.json'):
                    requirement = document(path, self.limits)
                    if requirement['name'] == result.get('server_name'):
                        source = requirement['id']
                        break
            # Missing identities are assigned isolated local IDs too. Never
            # fall back to a same-name system or foreign library resource.
            result[field] = str(self.map(category, source, unresolved=(category, source) not in self.maps))
            if category in {'dataset', 'skill'}:
                name_field = 'dataset_name' if category == 'dataset' else 'skill_name'
                result[name_field] = self.names.get((category, source), f'unresolved-{result[field]}')
            if category in {'knowledge', 'mcp'}:
                result[field] = str(self.map('mcp', source))
                result['server_name'] = self.names.get(('mcp', source), f'unconfigured-import-{result[field]}')
            if category == 'knowledge':
                key = 'document_path' if kind == 'okf_document' else 'bundle_path'
                namespace = self.knowledge_namespaces.get(source, 'documents' if kind == 'okf_document' else 'bundles')
                result[key] = str(managed_storage.roots()[namespace] / str(self.job.owner_id)
                                  / 'imports' / str(identity(self.job, 'mcp', source)))
        return result

    def definition(self) -> tuple[dict[str, Any], dict[str, Any]]:
        graph, settings = portable_definition(
            self.read('publication/canvas.json'), self.read('publication/settings.json'))
        if 'design_spec' not in settings and (self.root / 'design/revision.json').exists():
            # Explicit source design facts can seed a draft even when legacy
            # publication settings were not recorded. Source settings stay unknown.
            settings['design_spec'] = self.read('design/revision.json').get('design_spec')
        # Sanitized source resolver snapshots are evidence, never runnable state.
        graph.pop('sampling_snapshot', None)
        graph.pop('sampling_snapshot_version', None)
        factors = {f['name']: f for f in (settings.get('design_spec') or {}).get('factors', [])}
        rewrites: dict[str, tuple[str, str]] = {}
        for node in graph.get('nodes', []):
            kind, data = node['type'], node.setdefault('data', {})
            data['config'] = self.config(kind, data.get('config') or {})
            for path, category in (('dataset_selection', 'dataset'), ('skill_selection', 'skill'),
                                   ('knowledge_selection', 'knowledge')):
                if isinstance(data.get(path), list):
                    data[path] = [str(self.map(category, source)) for source in data[path]]
            for path, factor in (data.get('factor_bindings') or {}).items():
                if path in {'config', 'config.dataset_id', 'config.skill_id', 'config.bundle_id',
                            'config.document_id', 'config.server_id', 'dataset_selection',
                            'skill_selection', 'knowledge_selection'}:
                    rewrites[factor] = kind, path
        for name, (kind, path) in rewrites.items():
            factor = factors.get(name)
            if factor is None:
                continue
            levels: list[Any] = []
            for level in factor.get('levels', []):
                if path == 'config' and isinstance(level, dict):
                    levels.append(self.config(kind, level))
                elif path.endswith('_selection') and isinstance(level, list):
                    category = {'dataset_selection': 'dataset', 'skill_selection': 'skill',
                                'knowledge_selection': 'knowledge'}[path]
                    levels.append([str(self.map(category, source)) for source in level])
                elif path.startswith('config.'):
                    key = path.split('.')[1]
                    levels.append(self.config(kind, {key: level})[key])
                else:
                    levels.append(deepcopy(level))
            factor['levels'] = levels
        for producer in (settings.get('measurement_plan') or {}).get('producers', []):
            if producer.get('producer_id') == 'asaree.mcp_tool':
                config = producer.get('config') or {}
                if config.get('server_id'):
                    config['server_id'] = str(self.map('mcp', config['server_id']))
        return graph, settings

    async def activate(self, motoro: AsyncSession) -> uuid.UUID:
        manifest = self.read('manifest.json')
        metadata = self.read('experiment-metadata.json')
        experiment_id = self.map('experiment', manifest['experiment_id'], source=metadata)
        protocol_id = self.map('protocol', manifest['protocol_id'])
        self.map('source_publication', manifest['publication']['id'], source=manifest['publication'])
        datasets = await self.datasets()
        await self.skills(motoro)
        await self.servers(motoro)
        # Capture knowledge and external requirements as owned, typed records.
        # Content remains in managed storage; connections require explicit setup.
        for dep in records(self.root / 'dependencies.jsonl', self.limits):
            if dep.get('kind') in {'knowledge', 'mcp'}:
                self.map(dep['kind'], dep['id'], dependency=dep)
        graph, settings = self.definition()
        experiment = ResearchExperiment(id=experiment_id, owner_id=self.job.owner_id,
            name=self.name('experiment', manifest['experiment_id'], metadata.get('name') or 'Imported experiment'),
            description=metadata.get('description'), **{key: settings.get(key) for key in (
                'hypothesis', 'design_spec', 'measurement_plan', 'task_brief')},
            design_type=settings.get('design_type') or 'factorial')
        self.db.add(experiment)
        await self.db.flush()
        self.db.add(Protocol(id=protocol_id, owner_id=self.job.owner_id, experiment_id=experiment_id,
                             name=f'Imported protocol {protocol_id}', graph=graph))
        await set_experiment_datasets(self.db, experiment_id, datasets)
        if manifest.get('design_revision_id'):
            design_id = self.map('design', manifest['design_revision_id'], source=self.read('design/revision.json'))
            self.db.add(ExperimentDesignRevision(id=design_id, experiment_id=experiment_id, revision=1,
                                                 design_spec=settings.get('design_spec')))
            await self.db.flush()
            for scanned, source in enumerate(records(self.root / 'design/cells.jsonl', self.limits)):
                await self.report('restoring_cells', scanned)
                cell_id = self.map('cell', source['id'], source=source)
                factor_values = deepcopy(source.get('factor_values'))
                original_factors = {factor['name']: factor for factor in
                    (self.read('publication/settings.json').get('design_spec') or {}).get('factors', [])}
                local_factors = {factor['name']: factor for factor in
                                 (settings.get('design_spec') or {}).get('factors', [])}
                if isinstance(factor_values, dict):
                    for name, value in factor_values.items():
                        old = original_factors.get(name, {}).get('levels', [])
                        new = local_factors.get(name, {}).get('levels', [])
                        if value in old and len(old) == len(new):
                            factor_values[name] = deepcopy(new[old.index(value)])
                self.db.add(FactorialCell(id=cell_id, experiment_id=experiment_id, design_revision_id=design_id,
                    cell_label=source['cell_label'], factor_values=factor_values))
                await self.db.flush()
            await self.db.flush()
            for scanned, source in enumerate(records(self.root / 'design/replicates.jsonl', self.limits)):
                await self.report('restoring_replicates', scanned)
                cell = identity(self.job, 'cell', source['cell_id'])
                if await self.db.get(FactorialCell, cell) is None:
                    raise PackageError('Replicate references unknown cell')
                self.db.add(FactorialReplicateResult(id=self.map('replicate', source['id'], source=source),
                    cell_id=cell, replicate_number=source['replicate_number'],
                    replicate_label=source['replicate_label']))
                await self.db.flush()
        for path in (self.root / 'results/attempts').glob('*/attempt.json'):
            source = document(path, self.limits)
            self.map('historical_attempt', source['id'], source=source,
                     directory=path.parent.relative_to(self.root).as_posix())
            await self.db.flush()
        for path in (self.root / 'results/rows').glob('*.json'):
            source = document(path, self.limits)
            self.map('historical_row', source['id'], source=source)
            await self.db.flush()
        for scanned, record in enumerate(records(self.root / 'files.jsonl', self.limits)):
            await self.report('recording_provenance', scanned)
            if record['path'].startswith('results/artifacts/'):
                self.map('historical_artifact', record['path'], source=record)
                await self.db.flush()
            else:
                self.map('payload_member', record['path'], source=record)
                await self.db.flush()
        await self.db.flush()
        return experiment_id


async def require_configured(db: AsyncSession, experiment_id: uuid.UUID | None) -> None:
    if experiment_id is not None and await db.scalar(select(PackageImport.id).where(
        PackageImport.experiment_id == experiment_id,
        (PackageImport.configured_at.is_(None)) | (PackageImport.status != 'completed'),
    )):
        raise ValueError('Imported experiment requires explicit local dependency and connection configuration.')


async def validate_local_dependencies(db: AsyncSession, job: PackageImport) -> None:
    """Read-only validation of every typed alternative; never connect or invoke tools."""
    protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == job.experiment_id,
                                                     Protocol.owner_id == job.owner_id))
    experiment = await db.get(ResearchExperiment, job.experiment_id)
    if protocol is None or experiment is None:
        raise PackageError('Imported editable draft is unavailable')
    unresolved_splits = (await db.scalars(select(ImportedIdentity).where(
        ImportedIdentity.import_id == job.id, ImportedIdentity.kind == 'unresolved_split',
    ))).all()
    split_datasets = {
        identity(job, 'dataset', requirement.provenance['pin']['dataset_id'])
        for requirement in unresolved_splits
    }
    async with system_session(
        reason='validate owned import dependency configuration without execution',
    ) as resources:
        for node in protocol.graph.get('nodes', []):
            for variant in node_variants(node, experiment.design_spec):
                kind, config = variant.get('type'), (variant.get('data') or {}).get('config') or {}
                if kind == 'dataset':
                    try:
                        dataset = await db.scalar(select(RegisteredDataset).where(
                            RegisteredDataset.id == uuid.UUID(config.get('dataset_id') or ''),
                            RegisteredDataset.owner_id == job.owner_id))
                    except ValueError:
                        dataset = None
                    if dataset is None or dataset.raw_artifact is None or dataset.raw_artifact.status != 'ready':
                        raise PackageError(f'Dataset dependency remains unresolved: {node["id"]}')
                    if dataset.id in split_datasets:
                        split = dataset.current_split
                        if (split is None or split.dataset_id != dataset.id
                            or split.source_sha256 != dataset.raw_sha256
                            or any(artifact is None or artifact.status != 'ready'
                                   or artifact.dataset_id != dataset.id
                                   for artifact in (split.train_artifact, split.test_artifact))):
                            raise PackageError(f'Pinned split dependency remains unresolved: {node["id"]}. '
                                               'Restore a local split or explicitly rebind the dataset.')
                elif kind == 'skill':
                    try:
                        skill = await resources.scalar(select(Skill).where(
                            Skill.id == uuid.UUID(config.get('skill_id') or ''), Skill.deleted_at.is_(None),
                            (Skill.owner_id == job.owner_id) | Skill.is_system.is_(True)))
                    except ValueError:
                        skill = None
                    if skill is None:
                        raise PackageError(f'Skill dependency remains unresolved: {node["id"]}')
                elif kind in {'okf_bundle', 'okf_document', 'mcp_tool', 'mcp_scikit_learn', 'mcp_client_tool'}:
                    source_id = config.get('bundle_id') or config.get('document_id') or config.get('server_id')
                    try:
                        server = await resources.scalar(select(MCPServerConfig).where(
                            MCPServerConfig.id == uuid.UUID(source_id or ''),
                            (MCPServerConfig.owner_id == job.owner_id) | MCPServerConfig.is_system.is_(True)))
                    except ValueError:
                        server = None
                    if server is None or not (server.command or server.url):
                        raise PackageError(f'Local server connection remains unconfigured: {node["id"]}')
