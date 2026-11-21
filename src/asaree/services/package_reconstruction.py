"""Reconstruct editable local records; immutable source bytes are never rewritten."""
from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from motoro.models.database import system_session
from motoro.models.mcp_server import MCPServerConfig
from motoro.models.skill import Skill
from motoro.services import mcp_service, skill_service
from motoro.services.skill_service import MAX_BUNDLE_BYTES, MAX_BUNDLE_FILES, SkillFormatError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.experiment_design_revision import ExperimentDesignRevision
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.protocol import Protocol
from asaree.services.datasets import create_dataset, discard_registration_files, register_dataset_split
from asaree.services.experiment_packages import node_variants, portable_definition
from asaree.services.experiments import create_experiment_with_available_name, set_experiment_datasets
from asaree.services.package_validation import (
    Limits,
    PackageError,
    canonical_identity,
    document,
    payload_path,
    records,
)
from asaree.services.personas import create_persona, snapshot_identity
from asaree.services.protocols import create_protocol, generated_protocol_name


def identity(job: PackageImport, kind: str, source: Any) -> uuid.UUID:
    return uuid.uuid5(job.id, f'{kind}:{source}')


def registration_error(kind: str, name: str, exc: Exception) -> PackageError:
    if isinstance(exc, IntegrityError):
        reason = f'A {kind} with this name is already registered to your account.'
    elif isinstance(exc, SQLAlchemyError):
        reason = 'The registration could not be saved. Check the resource’s registration fields.'
    elif isinstance(exc, UnicodeError):
        reason = 'Packaged files must contain UTF-8 text.'
    else:
        reason = str(exc)
    return PackageError(f'Could not register {kind} "{name}": {reason}')


def knowledge_files(root: Path) -> list[tuple[str, str]]:
    from asaree.services import okf_bundles

    paths = [payload_path(root, path.relative_to(root).as_posix()) for path in root.rglob('*') if path.is_file()]
    if len(paths) > okf_bundles.MAX_UPLOAD_FILES or sum(path.stat().st_size for path in paths) > (
        okf_bundles.MAX_UPLOAD_BYTES
    ):
        raise ValueError('Knowledge files exceed the normal upload count or size limit.')
    return [(path.relative_to(root).as_posix(), path.read_bytes().decode('utf-8')) for path in paths]


class Reconstruction:
    def __init__(self, db: AsyncSession, job: PackageImport, root: Path, limits: Limits,
                 *, progress: Any = None, io: Any = None, stopping: threading.Event | None = None):
        self.db, self.job, self.root, self.limits = db, job, root, limits
        self.progress, self.io = progress, io or asyncio.to_thread
        self.stopping = stopping or threading.Event()
        self.maps: dict[tuple[str, str], str] = {}
        self.names: dict[tuple[str, str], str] = {}
        self.knowledge_namespaces: dict[str, str] = {}
        self.knowledge_paths: dict[str, str] = {}
        self.runtime_bindings: dict[str, str] = {}

    async def report(self, phase: str, scanned: int) -> None:
        if self.progress is not None and scanned % 25 == 0:
            await self.progress(phase, scanned=scanned)

    def map(self, kind: str, source_id: Any, *, local_id: uuid.UUID | None = None, **provenance: Any) -> uuid.UUID:
        key = kind, str(source_id)
        local = local_id or identity(self.job, 'mcp' if kind == 'knowledge' else kind, str(source_id))
        if key not in self.maps:
            self.db.add(ImportedIdentity(import_id=self.job.id, kind=kind, source_id=str(source_id),
                                         local_id=local, provenance=provenance))
            if kind not in {'cell', 'replicate', 'skill_file'}:
                self.maps[key] = str(local)
        return local

    def read(self, path: str) -> Any:
        return document(payload_path(self.root, path), self.limits)

    async def personas(self) -> None:
        """Restore exact node snapshots as reusable destination personas."""
        for path in (self.root / 'resources/personas').glob('*.json'):
            source = self.read(path.relative_to(self.root).as_posix())
            config = source['config']
            local = self.map('persona', source['id'], source=source)
            try:
                await create_persona(self.db, identifier=local, owner_id=self.job.owner_id,
                    name=source['name'], prompt=config.get('persona_text') or '',
                    description=config.get('persona_description'), source_filename=config.get('persona_file'))
            except (ValueError, SQLAlchemyError) as exc:
                raise registration_error('persona', source['name'], exc) from exc

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
            name = metadata.get('name') or pin.get('dataset_name') or 'Imported dataset'
            try:
                raw = next((dep for dep in records(self.root / 'dependencies.jsonl', self.limits)
                    if dep.get('kind') == 'dataset_file' and dep.get('dataset_id') == source
                    and dep.get('role') == 'raw_artifact' and dep.get('status') == 'included'), None)
                if raw is None:
                    raise ValueError('The package does not contain the original dataset file.')
                raw_path = payload_path(self.root, raw['file'])
                if await self.db.get(RegisteredDataset, local) is not None:
                    raise ValueError('This import already has a dataset registration; retry activation instead.')
                # Reclaim files left by a worker that died before committing
                # its product transaction, using only this request's unused ID.
                await self.io(discard_registration_files, local)
                dataset = await create_dataset(self.db, identifier=local, owner_id=self.job.owner_id,
                    name=name, csv_bytes=await self.io(raw_path.read_bytes),
                    description=pin.get('description', metadata.get('description')),
                    dictionary_json=pin.get('dictionary_json', metadata.get('dictionary_json')),
                    target_column=pin.get('target_column', metadata.get('target_column')))
                if dataset.raw_artifact is None:
                    raise ValueError('Dataset registration did not create an original source artifact.')
                self.map('artifact', raw['id'], local_id=dataset.raw_artifact.id, dependency=raw)
            except (ValueError, SQLAlchemyError, OSError) as exc:
                raise registration_error('dataset', name, exc) from exc
            self.names['dataset', source] = dataset.name
            dataset_ids.append(local)
        await self.db.flush()
        for file in (self.root / 'inputs/splits').glob('*.json'):
            source = document(file, self.limits)
            local = self.map('split', source['id'], source=source)
            dataset_id = self.maps.get(('dataset', source['dataset_id']))
            if not dataset_id:
                raise PackageError('Split has no pinned registration')
            fields = {key: source.get(key) for key in (
                'target_column', 'method', 'strategy', 'group_column', 'stratify_column',
                'test_size', 'seed', 'legacy')}
            fields['created_at'] = datetime.fromisoformat(source['created_at'])
            registered_dataset = await self.db.get(RegisteredDataset, uuid.UUID(dataset_id))
            if registered_dataset is None:
                raise PackageError('Dataset registration disappeared')
            try:
                partitions = []
                for key in ('train_artifact_id', 'test_artifact_id'):
                    dep = next((dep for dep in records(self.root / 'dependencies.jsonl', self.limits)
                        if dep.get('kind') == 'dataset_file' and dep.get('id') == source.get(key)
                        and dep.get('status') == 'included'), None)
                    if dep is None:
                        role = key.removesuffix('_artifact_id')
                        raise ValueError(f'The package does not contain the {role} partition.')
                    partitions.append(dep)
                await register_dataset_split(self.db, registered_dataset,
                    payload_path(self.root, partitions[0]['file']), payload_path(self.root, partitions[1]['file']),
                    identifier=local, **fields)
                split = registered_dataset.current_split
                if split is None or split.train_artifact is None or split.test_artifact is None:
                    raise ValueError('Dataset split registration did not create its partition artifacts.')
                for dep, artifact in zip(partitions, (split.train_artifact, split.test_artifact), strict=True):
                    self.map('artifact', dep['id'], local_id=artifact.id, dependency=dep)
            except (ValueError, SQLAlchemyError) as exc:
                raise registration_error('dataset split', source['id'], exc) from exc
        await self.db.flush()
        # Only pinned whole-dataset selections become local defaults. Original
        # selections remain unsplit even if another source default now exists.
        for pin in bindings.get('inputs', []):
            loaded = await self.db.get(RegisteredDataset, uuid.UUID(self.maps['dataset', pin['dataset_id']]))
            if loaded is None:
                raise PackageError('Dataset registration disappeared')
            selected_split = self.maps.get(('split', pin.get('split_id')))
            if selected_split:
                loaded.current_split_id = uuid.UUID(selected_split)
            elif pin.get('split_id'):
                # A missing source split is a requirement, never an explicit
                # original-only selection. Keep it across worker restarts.
                self.map('unresolved_split', pin['split_id'], pin=pin)
        await self.db.flush()
        return dataset_ids

    async def skills(self, motoro: AsyncSession) -> None:
        registered = []
        for dep in records(self.root / 'dependencies.jsonl', self.limits):
            if dep.get('kind') != 'skill':
                continue
            if dep.get('status') != 'included':
                raise registration_error('skill', dep['id'], ValueError(
                    'The package does not contain the required skill files.'))
            source_id = canonical_identity(dep['id'])
            if dep['directory'] != f'resources/skills/{source_id}':
                raise PackageError('Skill directory and identity disagree')
            metadata = payload_path(self.root, f'resources/skill-metadata/{source_id}.json')
            source = document(metadata, self.limits) if metadata.exists() else {'id': source_id}
            name = source.get('name', source_id)
            try:
                local = self.map('skill', source_id, source=source)
                bundle, file_dependencies = self.skill_bundle(dep)
                skill = await motoro.get(Skill, local)
                if skill is not None:
                    if skill.owner_id != self.job.owner_id or skill.is_system:
                        raise ValueError('The staged skill does not belong to this import request.')
                    skill_service.parse_skill_bundle(bundle)
                else:
                    skill = await skill_service.create_skill_from_bundle(bundle, db=motoro, identifier=local,
                        owner_id=self.job.owner_id, source_filename=source.get('source_filename'))
                for resource in skill.files:
                    file_dep = file_dependencies[resource.path]
                    self.map('skill_file', file_dep['id'], local_id=resource.id, dependency=file_dep)
                self.names['skill', source_id] = skill.name
                registered.append(skill)
            except (ValueError, SQLAlchemyError, OSError) as exc:
                raise registration_error('skill', name, exc) from exc
        # Keep the whole batch live until every registration has passed the
        # normal uniqueness check, then hide it before the Motoro commit.
        for skill in registered:
            skill.deleted_at = datetime.now(UTC)
        await motoro.flush()

    def skill_bundle(self, dep: dict[str, Any]) -> tuple[list[tuple[str, bytes]], dict[str, dict[str, Any]]]:
        directory = dep['directory']
        entrypoint = payload_path(self.root, f'{directory}/SKILL.md')
        if entrypoint.stat().st_size > self.limits.document:
            raise SkillFormatError('Skill entrypoint exceeds resource limit')
        files = [('SKILL.md', entrypoint.read_bytes())]
        identifiers = {}
        total = 0
        for record in records(self.root / 'files.jsonl', self.limits):
            if not record['path'].startswith(directory + '/') or record['path'] == f'{directory}/SKILL.md':
                continue
            relative = record['path'][len(directory) + 1:]
            path = payload_path(self.root, record['path'])
            total += path.stat().st_size
            if len(files) > MAX_BUNDLE_FILES or total > MAX_BUNDLE_BYTES:
                raise SkillFormatError('Skill bundle exceeds the normal file count or size registration limit.')
            file_dep = next((entry for entry in records(self.root / 'dependencies.jsonl', self.limits)
                if entry.get('kind') == 'skill_file' and entry.get('file') == record['path']
                and entry.get('status') == 'included'), None)
            if file_dep is None:
                if self.read('manifest.json')['schema_version'] >= 2:
                    raise PackageError('Missing structured Skill file metadata')
                file_dep = {'id': record['source_id'], 'skill_id': dep['id'], 'file': record['path'],
                    'relative_path': relative, 'source_encoding': 'legacy.unknown', 'source_position': 'legacy.unknown'}
            identifiers[relative] = file_dep
            files.append((relative, path.read_bytes()))
        return files, identifiers

    async def servers(self, motoro: AsyncSession) -> None:
        from asaree.services import okf_bundles, okf_documents
        from asaree.services.package_connections import automatic_match

        for file in (self.root / 'requirements/mcp').glob('*.json'):
            source = self.read(file.relative_to(self.root).as_posix())
            source_id = canonical_identity(source['id'])
            if file.name != f'{source_id}.json':
                raise PackageError('MCP requirement filename and identity disagree')
            local = self.map('mcp', source['id'], source=source)
            knowledge = payload_path(self.root, f'resources/knowledge/{source_id}')
            included = next((
                dep for dep in records(self.root / 'dependencies.jsonl', self.limits)
                if dep.get('kind') == 'knowledge' and dep.get('id') == source_id and dep.get('status') == 'included'
            ), None)
            if included is not None and included.get('directory') != f'resources/knowledge/{source_id}':
                raise PackageError('Knowledge directory and identity disagree')
            name = source['name']
            destination_server = None
            if included is None and source.get('service_identity'):
                authorized = list((await motoro.scalars(select(MCPServerConfig).where(
                    (MCPServerConfig.owner_id == self.job.owner_id) | MCPServerConfig.is_system.is_(True),
                ))).all())
                destination_server = automatic_match(source, authorized)
            if destination_server is not None:
                self.runtime_bindings[source_id] = str(destination_server.id)
                self.names['mcp', source_id] = destination_server.name
                self.db.add(ImportedIdentity(import_id=self.job.id, kind='runtime_mcp', source_id=source_id,
                    local_id=destination_server.id, provenance={'requirement_id': str(local),
                        'service_identity': source['service_identity'], 'selection': 'automatic'}))
                # The owned requirement identity remains separate from its runtime binding.
                continue
            if included is not None:
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
                    raise registration_error('knowledge', source['name'], ValueError(
                        'The package does not identify this knowledge source as a bundle or document.'))
                namespace = 'documents' if knowledge_kind == 'document' else 'bundles'
                self.knowledge_namespaces[source['id']] = namespace
                self.map('knowledge', source['id'], directory=knowledge.relative_to(self.root).as_posix())
            existing = await motoro.get(MCPServerConfig, local)
            if existing is not None and (existing.owner_id != self.job.owner_id or existing.is_system):
                raise PackageError('Import server ownership conflict')
            try:
                if existing is not None:
                    server = existing
                elif included is not None:
                    files = await self.io(knowledge_files, knowledge)
                    if knowledge_kind == 'document':
                        concepts = [(path, text) for path, text in files
                            if '/' not in path and Path(path).stem not in {'index', 'log'}]
                        if len(concepts) != 1:
                            raise ValueError('An OKF document must contain exactly one concept Markdown file.')
                        server = await okf_documents.register_document(owner_id=self.job.owner_id,
                            text=concepts[0][1], filename=concepts[0][0], db=motoro, identifier=local, connect=False)
                    else:
                        folder = included.get('name') or 'knowledge'
                        server = await okf_bundles.register_uploaded_bundle(owner_id=self.job.owner_id,
                            files=[(f'{folder}/{path}', text) for path, text in files],
                            db=motoro, identifier=local, connect=False)
                else:
                    server = await mcp_service.register_server(db=motoro, identifier=local, connect=False,
                        owner_id=self.job.owner_id, name=name, transport=source['transport'])
                self.names['mcp', source_id] = server.name
                # Hide the native row until the product identity ledger commits;
                # activation removes this marker before completing the request.
                server.error_message = 'Import staging: awaiting experiment activation.'
                if included is not None:
                    stored = okf_bundles.bundle_path_from_command(server.command)
                    if stored is None:
                        raise ValueError('Knowledge registration did not provide its managed storage location.')
                    self.knowledge_paths[source_id] = stored
            except (ValueError, SQLAlchemyError, OSError) as exc:
                raise registration_error('knowledge' if included else 'MCP server', source['name'], exc) from exc
        await motoro.flush()

    def config(self, kind: str, config: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(config)
        if kind == 'persona':
            source = snapshot_identity(config)
            local = self.maps.get(('persona', source))
            if local:
                result.update(mode='library', persona_id=local, persona_ids=[local],
                    persona_sources=[{'id': local, 'revision': 1,
                        'prompt_sha256': hashlib.sha256((config.get('persona_text') or '').encode()).hexdigest()}])
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
                result[field] = self.runtime_bindings.get(source) or str(self.map('mcp', source))
                result['server_name'] = self.names.get(('mcp', source), f'unconfigured-import-{result[field]}')
            if category == 'knowledge':
                key = 'document_path' if kind == 'okf_document' else 'bundle_path'
                result[key] = self.knowledge_paths.get(source, '')
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
        base_configs: dict[str, dict[str, Any]] = {}
        for node in graph.get('nodes', []):
            kind, data = node['type'], node.setdefault('data', {})
            data['config'] = self.config(kind, data.get('config') or {})
            for path, category in (('dataset_selection', 'dataset'), ('skill_selection', 'skill'),
                                   ('knowledge_selection', 'knowledge')):
                if isinstance(data.get(path), list):
                    data[path] = [str(self.map(category, source)) for source in data[path]]
            for path, factor in list((data.get('factor_bindings') or {}).items()):
                if path in {'config', 'config.dataset_id', 'config.skill_id', 'config.bundle_id',
                            'config.document_id', 'config.server_id', 'dataset_selection',
                            'skill_selection', 'knowledge_selection'}:
                    rewrites[factor] = kind, path
                    base_configs[factor] = deepcopy((node.get('data') or {}).get('config') or {})
                    if path == 'config.server_id' and kind in {'mcp_tool', 'mcp_client_tool', 'mcp_scikit_learn'}:
                        # Runtime tool dispatch uses both ID and name. A scalar
                        # alternative must carry its matching destination name.
                        data['factor_bindings'] = {'config': factor, **{
                            key: value for key, value in data['factor_bindings'].items() if key != path}}
        for name, (kind, path) in rewrites.items():
            factor = factors.get(name)
            if factor is None:
                continue
            if path == 'config.server_id' and kind in {'mcp_tool', 'mcp_client_tool', 'mcp_scikit_learn'}:
                factor['level_type'] = 'tool_config'
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
                    if path == 'config.server_id' and kind in {'mcp_tool', 'mcp_client_tool', 'mcp_scikit_learn'}:
                        levels.append(self.config(kind, {**base_configs[name], key: level}))
                    else:
                        levels.append(self.config(kind, {key: level})[key])
                else:
                    levels.append(deepcopy(level))
            factor['levels'] = levels
        for producer in (settings.get('measurement_plan') or {}).get('producers', []):
            if producer.get('producer_id') == 'asaree.mcp_tool':
                config = producer.get('config') or {}
                if config.get('server_id'):
                    source = config['server_id']
                    config['server_id'] = self.runtime_bindings.get(source) or str(self.map('mcp', source))
        return graph, settings

    async def activate(self, motoro: AsyncSession) -> uuid.UUID:
        # Imported registrations are staged in Motoro until activation commits.
        # The ledger still retains their IDs; readiness gates any execution.
        previous = self.db.info.get('staging_skill_import')
        self.db.info['staging_skill_import'] = True
        try:
            return await self._activate(motoro)
        finally:
            if previous is None:
                self.db.info.pop('staging_skill_import', None)
            else:
                self.db.info['staging_skill_import'] = previous

    async def _activate(self, motoro: AsyncSession) -> uuid.UUID:
        manifest = self.read('manifest.json')
        metadata = self.read('experiment-metadata.json')
        experiment_id = self.map('experiment', manifest['experiment_id'], source=metadata)
        protocol_id = self.map('protocol', manifest['protocol_id'])
        self.map('source_publication', manifest['publication']['id'], source=manifest['publication'],
            graph=self.read('publication/canvas.json'), settings=self.read('publication/settings.json'),
            design=self.read('design/revision.json') if (self.root / 'design/revision.json').is_file() else None)
        datasets = await self.datasets()
        await self.personas()
        await self.skills(motoro)
        await self.servers(motoro)
        # Capture knowledge and external requirements as owned, typed records.
        # Content remains in managed storage; connections require explicit setup.
        for dep in records(self.root / 'dependencies.jsonl', self.limits):
            if dep.get('kind') in {'knowledge', 'mcp'}:
                self.map(dep['kind'], dep['id'], dependency=dep)
        graph, settings = self.definition()
        experiment = await create_experiment_with_available_name(self.db,
            identifier=experiment_id, owner_id=self.job.owner_id,
            name=metadata.get('name') or 'Imported experiment',
            description=metadata.get('description'), **{key: settings.get(key) for key in (
                'hypothesis', 'design_spec', 'measurement_plan', 'task_brief')},
            design_type=settings.get('design_type') or 'factorial')
        await create_protocol(self.db, identifier=protocol_id, owner_id=self.job.owner_id,
            experiment_id=experiment_id, name=generated_protocol_name(experiment.name, experiment_id), graph=graph)
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
        await self.db.flush()
        return experiment_id


async def require_configured(db: AsyncSession, experiment_id: uuid.UUID | None, *, validate_draft: bool = True) -> None:
    if experiment_id is None:
        return
    jobs = (await db.scalars(select(PackageImport).where(PackageImport.experiment_id == experiment_id))).all()
    for job in jobs:
        if job.status != 'completed':
            raise ValueError('Imported experiment activation is incomplete.')
        if validate_draft:
            await validate_local_dependencies(db, job)


async def validate_local_dependencies(db: AsyncSession, job: PackageImport) -> None:
    issues = await readiness_issues(db, job)
    if issues:
        raise ReadinessError(issues)


class ReadinessError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]):
        self.issues = issues
        super().__init__('; '.join(issue['message'] for issue in issues))


async def readiness_issues(db: AsyncSession, job: PackageImport) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == job.experiment_id,
                                                     Protocol.owner_id == job.owner_id))
    if protocol is None:
        return [{'message': 'Imported editable draft is unavailable', 'node_id': None}]
    experiment = await db.get(ResearchExperiment, job.experiment_id)
    for node in protocol.graph.get('nodes', []):
        try:
            await validate_node_dependencies(db, job, node, experiment)
        except ValueError as exc:
            issues.append({'node_id': node['id'], 'message': str(exc)})
    from asaree.services.package_setup import runtime_requirement_issues

    issues.extend(await runtime_requirement_issues(db, job, protocol.graph,
                                                  experiment.design_spec if experiment else None))
    return issues


async def validate_node_dependencies(db: AsyncSession, job: PackageImport, source_node: dict[str, Any],
                                     experiment: ResearchExperiment | None) -> None:
    """Read-only validation of every typed alternative; never connect or invoke tools."""
    if experiment is None:
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
        for node in [source_node]:
            for variant in node_variants(node, experiment.design_spec):
                kind, config = variant.get('type'), (variant.get('data') or {}).get('config') or {}
                if config.get('enabled', True) is False:
                    continue
                data = variant.get('data') or {}
                for field, resource_kind, identifier in (
                    ('dataset_selection', 'dataset', 'dataset_id'),
                    ('skill_selection', 'skill', 'skill_id'),
                    ('knowledge_selection', 'mcp_tool', 'server_id'),
                ):
                    for selected in data.get(field) or []:
                        await validate_node_dependencies(db, job, {
                            'id': node['id'], 'type': resource_kind,
                            'data': {'config': {identifier: selected}},
                        }, experiment)
                if str(kind).startswith(('model_', 'llm_')):
                    from asaree.services.package_setup import validate_model_connection

                    await validate_model_connection(db, job, config, str(kind), node['id'])
                elif kind == 'dataset':
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
                    from asaree.services.package_connections import compatible

                    if not compatible({'tool_names': config.get('tool_names') or []}, server):
                        raise PackageError(f'Local server lacks required tools: {node["id"]}')
