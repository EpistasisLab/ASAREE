"""Bounded, non-executing package inspection. Extracted bytes stay private."""
from __future__ import annotations

import gzip
import hashlib
import importlib.metadata
import json
import tarfile
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from asaree.services.experiment_packages import BLOCK, credential_file, relative_path


@dataclass(frozen=True)
class Limits:
    compressed: int = 1024**3
    expanded: int = 8 * 1024**3
    members: int = 100000
    document: int = 16 * 1024**2


class PackageError(ValueError):
    """An actionable untrusted-package diagnostic."""


def member_path(value: str) -> str:
    relative_path(value)
    if len(value.encode()) > 1024 or len(value.split('/')) > 32 or any(ord(c) < 32 for c in value):
        raise PackageError('Invalid member path length, depth or control character')
    return value


def invalid(message: str) -> Any:
    raise PackageError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PackageError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def document(path: Path, limits: Limits) -> Any:
    if path.stat().st_size > limits.document:
        raise PackageError(f'JSON document exceeds limit: {path.name}')
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=unique_object,
                          parse_constant=lambda value: invalid(f'Non-finite JSON: {value}'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PackageError(f'Invalid JSON: {path.name}: {exc}') from exc


def records(path: Path, limits: Limits) -> Iterator[dict[str, Any]]:
    with path.open('rb') as stream:
        while line := stream.readline(limits.document + 1):
            if len(line) > limits.document:
                raise PackageError(f'Inventory record exceeds limit: {path.name}')
            try:
                value = json.loads(line, object_pairs_hook=unique_object,
                                   parse_constant=lambda value: invalid(f'Non-finite JSON: {value}'))
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise PackageError(f'Invalid inventory: {path.name}: {exc}') from exc
            if not isinstance(value, dict):
                raise PackageError(f'Expected object record: {path.name}')
            yield value


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(root: Path, record: dict[str, Any]) -> None:
    path = root / member_path(record['path'])
    if (not path.is_file() or type(record.get('bytes')) is not int
            or path.stat().st_size != record['bytes'] or digest(path) != record.get('sha256')):
        raise PackageError(f'Missing member or size/hash mismatch: {record["path"]}')


class Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class ExpandedReader:
    def __init__(self, stream: Readable, limit: int, stopping: threading.Event):
        self.stream, self.limit, self.stopping, self.count = stream, limit, stopping, 0
        self.tail = b''

    def read(self, size: int = -1) -> bytes:
        if self.stopping.is_set():
            raise PackageError('Import cancelled')
        value = self.stream.read(min(BLOCK, self.limit - self.count + 1, size if size >= 0 else BLOCK))
        self.count += len(value)
        self.tail = (self.tail + value[-1024:])[-1024:]
        if self.count > self.limit:
            raise PackageError('Expanded archive exceeds configured limit')
        return value


class PackageTarInfo(tarfile.TarInfo):
    """Gate metadata before tarfile can allocate attacker-declared PAX buffers.

    The exporter emits ordinary files and local PAX path/size extensions only.
    GNU sparse maps, global headers and nested extensions are not this format.
    """
    header_limit = 200002

    @staticmethod
    def padded(size: int) -> int:
        return (size + 511) // 512 * 512

    def _proc_member(self, archive: Any) -> Any:
        headers = getattr(archive, '_package_headers', 0) + 1
        archive._package_headers = headers
        if headers > self.header_limit:
            raise PackageError('Archive metadata exceeds configured member count')
        if self.type == tarfile.XHDTYPE:
            return self._proc_pax(archive)
        if self.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}:
            raise PackageError('Only ordinary files and local path/size PAX metadata are supported')
        return super()._proc_member(archive)  # type: ignore[misc]  # Private stdlib hook, covered on supported Pythons.

    def _proc_pax(self, archive: Any) -> Any:
        if self.size < 0 or self.size > 4096 or getattr(archive, '_package_pax_active', False):
            raise PackageError('Oversized or nested archive metadata')
        archive._package_pax_active = True
        try:
            raw = archive.fileobj.read(self.padded(self.size))[:self.size]
            values: dict[str, str] = {}
            offset = 0
            while offset < len(raw):
                space = raw.find(b' ', offset)
                if space < 0 or not raw[offset:space].isdigit():
                    raise PackageError('Invalid archive metadata length')
                length = int(raw[offset:space])
                if length <= space - offset + 1 or offset + length > len(raw):
                    raise PackageError('Invalid archive metadata record')
                record = raw[space + 1:offset + length]
                if not record.endswith(b'\n') or b'=' not in record:
                    raise PackageError('Invalid archive metadata field')
                key, value = record[:-1].split(b'=', 1)
                name = key.decode('ascii')
                if name not in {'path', 'size'} or name in values:
                    raise PackageError('Unsupported or duplicate archive metadata field')
                values[name] = value.decode('utf-8')
                offset += length
            following = type(self).fromtarfile(archive)
            if following is None:
                raise PackageError('Archive metadata has no file header')
            if 'path' in values:
                following.name = member_path(values['path'])
            if 'size' in values:
                if not values['size'].isdigit():
                    raise PackageError('Invalid extended member size')
                following.size = int(values['size'])
                archive.offset = following.offset_data + self.padded(following.size)
            following.pax_headers = values
            following.offset = self.offset
            return following
        finally:
            archive._package_pax_active = False


def inspect(archive: Path, root: Path, limits: Limits, stopping: threading.Event) -> dict[str, Any]:
    """Fresh root only. Never extractall, follow links, or trust tar metadata."""
    if archive.stat().st_size > limits.compressed:
        raise PackageError('Compressed archive exceeds configured limit')
    root.mkdir(mode=0o700)
    count = 0
    try:
        with gzip.open(archive, 'rb') as compressed:
            expanded = ExpandedReader(compressed, limits.expanded, stopping)
            class BoundedTarInfo(PackageTarInfo):
                header_limit = limits.members * 2 + 2
            # Tar stream mode uses only read(), not the broader seekable IO API.
            with tarfile.open(fileobj=cast(Any, expanded), mode='r|', tarinfo=BoundedTarInfo) as tar:
                for member in tar:
                    count += 1
                    if count > limits.members:
                        raise PackageError('Archive exceeds configured member count')
                    name = member_path(member.name)
                    if credential_file(name):
                        raise PackageError('Package contains an excluded credential-file identity')
                    if not member.isfile() or member.sparse is not None or member.size < 0:
                        raise PackageError(f'Only ordinary files are accepted: {name}')
                    if member.size > limits.expanded:
                        raise PackageError(f'Member exceeds expanded limit: {name}')
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    source = tar.extractfile(member)
                    if source is None:
                        raise PackageError(f'Missing member bytes: {name}')
                    with destination.open('xb') as output:
                        destination.chmod(0o600)
                        remaining = member.size
                        while remaining:
                            if stopping.is_set():
                                raise PackageError('Import cancelled')
                            block = source.read(min(remaining, BLOCK))
                            if not block:
                                raise PackageError(f'Truncated member: {name}')
                            output.write(block)
                            remaining -= len(block)
                    tar.members.clear()  # type: ignore[attr-defined]  # Runtime cache, absent from typeshed.
                if getattr(tar.fileobj, 'buf', b'').strip(b'\0'):
                    raise PackageError('Unexpected buffered data after tar end')
            while block := expanded.read(BLOCK):
                if block.strip(b'\0'):
                    raise PackageError('Unexpected data after tar end')
            if expanded.count % 512 or expanded.tail != b'\0' * 1024:
                raise PackageError('Missing complete tar end records')
        return validate(root, limits, count)
    except (OSError, EOFError, tarfile.TarError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise PackageError(f'Malformed experiment package: {exc}') from exc


def validate(root: Path, limits: Limits, count: int) -> dict[str, Any]:
    manifest = document(root / 'manifest.json', limits)
    if (manifest.get('format') != 'asaree.experiment-package'
        or type(manifest.get('schema_version')) is not int or manifest['schema_version'] not in (1, 2)):
        raise PackageError('Unsupported experiment package format/version; supported: 1, 2')
    if type(manifest.get('results_included')) is not bool:
        raise PackageError('results_included must be a Boolean')
    for key, name in (('files', 'files.jsonl'), ('dependencies', 'dependencies.jsonl')):
        if manifest[key]['path'] != name:
            raise PackageError(f'Invalid {key} inventory locator')
        verify(root, manifest[key])
    markers = root.parent / (root.name + '-verified')
    markers.mkdir(mode=0o700)
    files = 0
    categories: dict[str, int] = {}
    for record in records(root / 'files.jsonl', limits):
        name = member_path(record['path'])
        if name in {'manifest.json', 'files.jsonl'}:
            raise PackageError('Circular inventory member')
        (markers / hashlib.sha256(name.encode()).hexdigest()).touch(exist_ok=False)
        verify(root, record)
        if name.split('/')[0] not in {'dependencies.jsonl', 'publication', 'experiment-metadata.json',
                                      'design', 'inputs', 'resources', 'requirements', 'results', 'sources'}:
            raise PackageError(f'Unsupported payload category: {name}')
        schema_json = name in {'experiment-metadata.json', 'publication/canvas.json', 'publication/settings.json',
                               'inputs/bindings.json', 'design/revision.json'} or name.startswith((
            'inputs/datasets/', 'inputs/splits/', 'requirements/', 'resources/skill-metadata/',
            'results/attempts/', 'results/rows/'))
        if schema_json and name.endswith('.json'):
            document(root / name, limits)
        elif name in {'dependencies.jsonl', 'design/cells.jsonl', 'design/replicates.jsonl'}:
            for _ in records(root / name, limits):
                pass
        files += 1
        category = name.split('/')[0]
        categories[category] = categories.get(category, 0) + 1
    if files != manifest['file_count'] or files + 2 != count:
        raise PackageError('Inventory/member counts differ; unlisted or missing payload')
    required = ['publication/canvas.json', 'publication/settings.json', 'experiment-metadata.json']
    if manifest.get('design_revision_id'):
        required += ['design/revision.json', 'design/cells.jsonl', 'design/replicates.jsonl']
    for name in required:
        if not (root / name).is_file():
            raise PackageError(f'Required member missing: {name}')
    if manifest.get('design_revision_id') and document(root / 'design/revision.json', limits)['id'] != str(
        uuid.UUID(manifest['design_revision_id'])
    ):
        raise PackageError('Generated design identity differs from source publication')
    graph = document(root / 'publication/canvas.json', limits)
    settings = document(root / 'publication/settings.json', limits)
    if not isinstance(graph, dict) or not isinstance(settings, dict):
        raise PackageError('Canvas and settings must be objects')
    node_ids = [node['id'] for node in graph.get('nodes', [])]
    if len(set(node_ids)) != len(node_ids):
        raise PackageError('Duplicate canvas node identity')
    for edge in graph.get('edges', []):
        if edge['source'] not in node_ids or edge['target'] not in node_ids:
            raise PackageError('Canvas edge references missing node')
    unresolved, external = 0, 0
    diagnostics: list[dict[str, Any]] = []
    for record in records(root / 'dependencies.jsonl', limits):
        if record.get('status') in {'missing', 'unresolved', 'changed', 'forbidden'}:
            unresolved += 1
            if len(diagnostics) < 100:
                diagnostics.append(record)
        external += bool(record.get('external_requirement'))
        if record.get('status') == 'included':
            if 'file' in record and not (root / member_path(record['file'])).is_file():
                raise PackageError('Included dependency references missing file')
            empty_knowledge = record.get('kind') == 'knowledge' and (
                record.get('file_count') == 0 or manifest['schema_version'] == 1)
            if ('directory' in record and not (root / member_path(record['directory'])).is_dir()
                and not empty_knowledge):
                raise PackageError('Included dependency references missing directory')
    validate_references(root, limits, manifest, markers)
    warnings = ['Connections and credentials require local configuration and normal publication before execution.']
    compatibility = []
    for package, required in (manifest.get('requirements', {}).get('software') or {}).items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = 'not installed'
        if required != installed:
            compatibility.append(f'{package}: source {required}; local {installed}. Revalidate before publication.')
    if manifest['schema_version'] == 1:
        warnings.append('v1 omits structured Skill metadata/file order, complete dataset registration metadata, '
                        'and row-slot identities. Original files remain inspectable; omitted facts are unknown.')
    return {'manifest': manifest, 'categories': categories, 'unresolved_count': unresolved,
            'external_requirements': external, 'diagnostics': diagnostics, 'warnings': warnings,
            'compatibility': compatibility,
            'file_count': files, 'bytes': sum(p.stat().st_size for p in root.rglob('*') if p.is_file())}


def validate_references(root: Path, limits: Limits, manifest: dict[str, Any], markers: Path) -> None:
    """Disk-backed identity indexes avoid an in-memory attempt/file inventory."""
    def marker(kind: str, source: Any) -> Path:
        return markers / hashlib.sha256(f'{kind}:{source}'.encode()).hexdigest()

    def remember(kind: str, source: Any, record: Any) -> None:
        if not isinstance(source, str) or len(source) > (2048 if kind == 'skill_path' else 255):
            raise PackageError(f'Invalid {kind} identity')
        path = marker(kind, source)
        with path.open('xb') as stream:
            stream.write(json.dumps(record).encode())

    def require(kind: str, source: Any) -> dict[str, Any]:
        path = marker(kind, source)
        if not path.is_file():
            raise PackageError(f'Unknown {kind} reference: {source}')
        result = document(path, limits)
        if not isinstance(result, dict):
            raise PackageError(f'Invalid {kind} reference metadata')
        return result

    for key in ('experiment_id', 'protocol_id'):
        uuid.UUID(manifest[key])
    uuid.UUID(manifest['publication']['id'])
    for dep in records(root / 'dependencies.jsonl', limits):
        if dep.get('kind') in {'dataset_file', 'skill_file'} and dep.get('status') == 'included':
            remember(dep['kind'], dep['id'], dep)
        if dep.get('kind') == 'skill' and dep.get('status') == 'included':
            source = str(uuid.UUID(dep['id']))
            if dep.get('directory') != f'resources/skills/{source}':
                raise PackageError('Skill directory and identity disagree')
            if not (root / dep['directory'] / 'SKILL.md').is_file():
                raise PackageError('Included Skill has no entry point')
            if manifest['schema_version'] >= 2:
                metadata = document(root / f'resources/skill-metadata/{source}.json', limits)
                if metadata.get('id') != source:
                    raise PackageError('Structured Skill identity differs from dependency inventory')
            remember('skill', source, dep)
    for dep in records(root / 'dependencies.jsonl', limits):
        if dep.get('kind') == 'skill_file' and dep.get('status') == 'included':
            skill = require('skill', dep['skill_id'])
            relative = member_path(dep['relative_path'])
            if len(relative) > 255:
                raise PackageError('Skill resource path exceeds supported storage length')
            if dep['file'] != f'{skill["directory"]}/{relative}':
                raise PackageError('Skill resource relationship mismatch')
            remember('skill_path', f'{dep["skill_id"]}:{relative.lower()}', dep)
            if dep.get('encoding') not in {'utf-8', 'base64'} or type(dep.get('position')) is not int:
                raise PackageError('Unsupported Skill resource encoding/order metadata')
            file = root / member_path(dep['file'])
            if file.stat().st_size > limits.document:
                raise PackageError('Skill resource exceeds configured document/resource limit')
            if dep['encoding'] == 'utf-8':
                file.read_bytes().decode('utf-8')
    bindings_path = root / 'inputs/bindings.json'
    if bindings_path.exists():
        bindings = document(bindings_path, limits)
        if bindings.get('schema_version') != 1:
            raise PackageError('Unsupported pinned input schema')
        seen: dict[tuple[str, str], Any] = {}
        for pin in bindings.get('inputs', []):
            uuid.UUID(pin['dataset_id'])
            if pin.get('mode') not in {'whole_dataset', 'per_row'}:
                raise PackageError('Unknown dataset input mode')
            for field in ('raw_artifact', 'train_artifact', 'test_artifact'):
                ref = pin.get(field)
                source = ref['id'] if ref else f'{pin["dataset_id"]}:original'
                if not ref and field != 'raw_artifact':
                    continue
                expected = ref['sha256'] if ref else pin['raw_sha256']
                artifact_key = pin['dataset_id'], field
                if artifact_key in seen and seen[artifact_key] != source:
                    raise PackageError('Conflicting dataset artifact identities')
                seen[artifact_key] = source
                dep_path = marker('dataset_file', source)
                if dep_path.exists():
                    dep = document(dep_path, limits)
                    if dep['dataset_id'] != pin['dataset_id'] or dep['role'] != field:
                        raise PackageError('Pinned artifact relationship mismatch')
                    if digest(root / member_path(dep['file'])) != expected:
                        raise PackageError('Pinned artifact hash differs from inventory')
                elif not any(d.get('id') == source and d.get('status') in {'missing', 'unresolved', 'changed'}
                             for d in records(root / 'dependencies.jsonl', limits)):
                    # Entire dataset may be absent, and explicitly inventoried.
                    if not any(d.get('id') == pin['dataset_id'] and d.get('kind') == 'dataset'
                               and d.get('status') == 'missing'
                               for d in records(root / 'dependencies.jsonl', limits)):
                        raise PackageError('Pinned file is absent without missing-dependency evidence')
            if pin.get('split_id'):
                split_path = root / f'inputs/splits/{uuid.UUID(pin["split_id"])}.json'
                if split_path.exists():
                    split = document(split_path, limits)
                    if (split['dataset_id'] != pin['dataset_id'] or split['source_sha256'] != pin['raw_sha256']
                        or split['train_artifact_id'] != (pin.get('train_artifact') or {}).get('id')
                        or split['test_artifact_id'] != (pin.get('test_artifact') or {}).get('id')):
                        raise PackageError('Pinned split relationship mismatch')
    for category, filename in (('cell', 'design/cells.jsonl'), ('replicate', 'design/replicates.jsonl')):
        path = root / filename
        if not path.exists():
            continue
        for record in records(path, limits):
            uuid.UUID(record['id'])
            if category == 'replicate':
                require('cell', record['cell_id'])
                if type(record['replicate_number']) is not int or record['replicate_number'] < 1:
                    raise PackageError('Invalid planned replicate number')
            remember(category, record['id'], record)
    for path in (root / 'results/rows').glob('*.json'):
        row = document(path, limits)
        require('replicate', row['replicate_result_id'])
        if row['protocol_revision_id'] != manifest['publication']['id']:
            raise PackageError('Historical row belongs to a different publication')
        remember('row', row['id'], row)
    for path in (root / 'results/attempts').glob('*/attempt.json'):
        attempt = document(path, limits)
        if path.parent.name != str(uuid.UUID(attempt['id'])):
            raise PackageError('Attempt path and identity disagree')
        if not manifest.get('results_included'):
            raise PackageError('Results present while manifest excludes results')
        if attempt.get('replicate_result_id'):
            require('replicate', attempt['replicate_result_id'])
        if attempt.get('row_result_id') and manifest['schema_version'] >= 2:
            require('row', attempt['row_result_id'])
        remember('attempt', attempt['id'], attempt)
    for file in (root / 'results/artifacts').glob('*'):
        require('attempt', file.name)
