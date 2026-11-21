"""Bounded, non-executing package inspection. Extracted bytes stay private."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import stat
import struct
import threading
import uuid
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

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


def canonical_identity(value: Any) -> str:
    """Typed UUIDs have one spelling, including when used in archive paths."""
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError('Noncanonical UUID')
    except (ValueError, AttributeError) as exc:
        raise PackageError('Invalid canonical UUID identity') from exc
    return value


def payload_path(root: Path, value: str) -> Path:
    """Resolve only archive-relative paths contained in the private payload."""
    path = root / member_path(value)
    if not path.resolve().is_relative_to(root.resolve()):
        raise PackageError('Source path resolves outside payload containment boundary')
    return path


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


def check_zip_directory(source: BinaryIO, limits: Limits, stopping: threading.Event) -> int:
    """Bound metadata before ZipFile buffers its central directory and entry objects.

    Read fixed ZIP/ZIP64 end records, then scan directory headers without loading
    variable fields. Packages are single-disk ZIPs without executable prefixes,
    trailing data or ZIP64 extensible data. Directory bytes use the document limit.
    """
    source.seek(0, 2)
    size = source.tell()
    source.seek(max(0, size - 65557))  # 22-byte end record plus the maximum ZIP comment.
    tail = source.read(65557)
    offset = tail.rfind(b'PK\x05\x06')
    if offset < 0 or len(tail) - offset < 22:
        raise PackageError('Expected a ZIP experiment package')
    _, disk, directory_disk, disk_count, count, directory_size, directory_offset, comment_size = struct.unpack(
        '<4s4H2IH', tail[offset:offset + 22])
    if offset + 22 + comment_size != len(tail):
        raise PackageError('Truncated ZIP comment or unexpected trailing data')
    directory_end = size - len(tail) + offset
    if directory_end >= 20:
        source.seek(directory_end - 20)
        locator = source.read(20)
        if locator.startswith(b'PK\x06\x07'):
            _, locator_disk, zip64_offset, disks = struct.unpack('<4sIQI', locator)
            if locator_disk or disks != 1 or zip64_offset + 56 != directory_end - 20:
                raise PackageError('Unsupported ZIP64 directory layout')
            source.seek(zip64_offset)
            signature, record_size, _, _, disk, directory_disk, disk_count, count, directory_size, directory_offset = (
                struct.unpack('<4sQ2H2I4Q', source.read(56)))
            if signature != b'PK\x06\x06' or record_size != 44:
                raise PackageError('Unsupported ZIP64 directory metadata')
            directory_end = zip64_offset
    if disk or directory_disk or disk_count != count:
        raise PackageError('Multi-disk ZIP packages are unsupported')
    if count > limits.members:
        raise PackageError('Archive exceeds configured member count')
    if directory_size > limits.document:
        raise PackageError('ZIP directory metadata exceeds configured document limit')
    if directory_offset + directory_size != directory_end:
        raise PackageError('Invalid ZIP directory bounds')
    source.seek(directory_offset)
    actual_count = 0
    while source.tell() < directory_end:
        if stopping.is_set():
            raise PackageError('Import cancelled')
        header = struct.unpack('<4s6H3I5H2I', source.read(46))
        actual_count += 1
        if actual_count > limits.members:
            raise PackageError('Archive exceeds configured member count')
        if header[0] != b'PK\x01\x02' or header[10] > 1024:
            raise PackageError('Invalid ZIP directory entry or member path length')
        source.seek(sum(header[10:13]), 1)  # Filename, extra fields and entry comment.
        if source.tell() > directory_end:
            raise PackageError('Truncated ZIP directory entry')
    if actual_count != count:
        raise PackageError('ZIP directory member counts differ')
    source.seek(0)
    return actual_count


def inspect(archive: Path, root: Path, limits: Limits, stopping: threading.Event) -> dict[str, Any]:
    """Fresh root only. Never extractall, follow links, or trust ZIP sizes."""
    if archive.stat().st_size > limits.compressed:
        raise PackageError('Compressed archive exceeds configured limit')
    root.mkdir(mode=0o700)
    try:
        if stopping.is_set():
            raise PackageError('Import cancelled')
        with archive.open('rb') as stream:
            count = check_zip_directory(stream, limits, stopping)
            with zipfile.ZipFile(stream) as zipped:
                expanded = 0
                for member in zipped.infolist():
                    if member.orig_filename != member.filename:
                        raise PackageError('Invalid ZIP member filename')
                    name = member_path(member.filename)
                    if credential_file(name):
                        raise PackageError('Package contains an excluded credential-file identity')
                    mode = stat.S_IFMT(member.external_attr >> 16)
                    if member.is_dir() or member.external_attr & 0x10 or mode not in {0, stat.S_IFREG}:
                        raise PackageError(f'Only ordinary files are accepted: {name}')
                    if member.flag_bits & 1:
                        raise PackageError('Encrypted ZIP packages are unsupported')
                    if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                        raise PackageError('Unsupported ZIP compression method')
                    if expanded + member.file_size > limits.expanded:
                        raise PackageError(f'Member exceeds expanded limit: {name}')
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with zipped.open(member) as source, destination.open('xb') as output:
                        destination.chmod(0o600)
                        written = 0
                        while True:
                            if stopping.is_set():
                                raise PackageError('Import cancelled')
                            block = source.read(min(BLOCK, limits.expanded - expanded + 1))
                            if not block:
                                break
                            written += len(block)
                            expanded += len(block)
                            if expanded > limits.expanded:
                                raise PackageError('Expanded archive exceeds configured limit')
                            output.write(block)
                        if written != member.file_size:
                            raise PackageError(f'Truncated member: {name}')
        return validate(root, limits, count)
    except (OSError, EOFError, zipfile.BadZipFile, zlib.error, struct.error,
            NotImplementedError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise PackageError(f'Malformed experiment package: {exc}') from exc


def validate(root: Path, limits: Limits, count: int) -> dict[str, Any]:
    manifest = document(root / 'manifest.json', limits)
    if (manifest.get('format') != 'asaree.experiment-package'
        or type(manifest.get('schema_version')) is not int or manifest['schema_version'] not in (1, 2)):
        raise PackageError('Unsupported experiment package format/version; supported: 1, 2')
    if manifest.get('results_included') is not False:
        raise PackageError('Experiment packages contain configuration and input assets only; results are unsupported')
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
                                      'design', 'inputs', 'resources', 'requirements', 'sources'}:
            raise PackageError(f'Unsupported payload category: {name}')
        schema_json = name in {'experiment-metadata.json', 'publication/canvas.json', 'publication/settings.json',
                               'inputs/bindings.json', 'design/revision.json'} or name.startswith((
            'inputs/datasets/', 'inputs/splits/', 'requirements/', 'resources/skill-metadata/',
            'resources/personas/'))
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
    for path in (root / 'resources/personas').glob('*.json'):
        from asaree.services.personas import MAX_PROMPT_BYTES, snapshot_identity

        persona = document(path, limits)
        source = snapshot_identity(persona['config'])
        prompt = persona['config'].get('persona_text') or ''
        if (persona['id'] != source or path.stem != source or not isinstance(prompt, str)
            or len(prompt.encode()) > MAX_PROMPT_BYTES
            or (path.with_suffix('.md')).read_bytes() != prompt.encode()):
            raise PackageError('Persona snapshot identity or content mismatch')
    from asaree.services.experiment_packages import node_variants
    from asaree.services.personas import snapshot_identity

    for node in graph.get('nodes', []):
        if node.get('type') != 'persona':
            continue
        for variant in node_variants(node, settings.get('design_spec')):
            source = snapshot_identity((variant.get('data') or {}).get('config') or {})
            if not (root / f'resources/personas/{source}.json').is_file():
                raise PackageError('Persona snapshot is missing from the package')
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
        canonical_identity(manifest[key])
    canonical_identity(manifest['publication']['id'])
    for file in (root / 'requirements/mcp').glob('*.json'):
        requirement = document(payload_path(root, file.relative_to(root).as_posix()), limits)
        source = canonical_identity(requirement['id'])
        if (not isinstance(requirement.get('required_tool_names', []), list)
            or any(not isinstance(tool, str) for tool in requirement.get('required_tool_names', []))
            or not isinstance(requirement.get('tool_names', []), list)
            or any(not isinstance(tool, str) for tool in requirement.get('tool_names', []))
            or (requirement.get('service_identity') is not None
                and not isinstance(requirement['service_identity'], str))):
            raise PackageError('Invalid MCP service identity or required tool names')
        if file.name != f'{source}.json':
            raise PackageError('MCP requirement filename and identity disagree')
        remember('mcp', source, requirement)
    for dep in records(root / 'dependencies.jsonl', limits):
        if dep.get('kind') in {'mcp', 'knowledge'} and dep.get('status') == 'included':
            source = canonical_identity(dep['id'])
            require('mcp', source)
            if dep['kind'] == 'knowledge':
                if dep.get('directory') != f'resources/knowledge/{source}':
                    raise PackageError('Knowledge directory and identity disagree')
                payload_path(root, dep['directory'])
        if dep.get('kind') in {'dataset_file', 'skill_file'} and dep.get('status') == 'included':
            remember(dep['kind'], dep['id'], dep)
        if dep.get('kind') == 'skill' and dep.get('status') == 'included':
            source = canonical_identity(dep['id'])
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
    for file in (root / 'inputs/splits').glob('*.json'):
        split = document(payload_path(root, file.relative_to(root).as_posix()), limits)
        source = canonical_identity(split['id'])
        if file.name != f'{source}.json':
            raise PackageError('Split filename and identity disagree')
        remember('split', source, split)
    bindings_path = root / 'inputs/bindings.json'
    if bindings_path.exists():
        bindings = document(bindings_path, limits)
        if bindings.get('schema_version') != 1:
            raise PackageError('Unsupported pinned input schema')
        seen: dict[tuple[str, str], Any] = {}
        for pin in bindings.get('inputs', []):
            canonical_identity(pin['dataset_id'])
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
                split_path = payload_path(root, f'inputs/splits/{canonical_identity(pin["split_id"])}.json')
                if split_path.exists():
                    split = document(split_path, limits)
                    if (canonical_identity(split['id']) != pin['split_id']
                        or split['dataset_id'] != pin['dataset_id'] or split['source_sha256'] != pin['raw_sha256']
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
