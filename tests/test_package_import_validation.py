"""Real ZIP exports through the bounded, untrusted upload parser."""
import gzip
import stat
import struct
import threading
import uuid
import zipfile
from dataclasses import replace

import pytest

from asaree.services.experiment_packages import PackageWriter
from asaree.services.package_validation import Limits, PackageError, inspect


def package(tmp_path, *, version=2, graph=None, modify=None):
    stage = tmp_path / 'writer'
    stage.mkdir()
    archive = tmp_path / 'package.zip'
    writer = PackageWriter(archive, stage)
    writer.json('publication/canvas.json', graph or {'nodes': [], 'edges': []})
    writer.json('publication/settings.json', {'design_spec': {'factors': []}})
    writer.json('experiment-metadata.json', {'name': 'Independent owner'})
    if modify:
        modify(writer)
    writer.finish({'format': 'asaree.experiment-package', 'schema_version': version,
                   'experiment_id': str(uuid.uuid4()), 'protocol_id': str(uuid.uuid4()),
                   'publication': {'id': str(uuid.uuid4()), 'version': 3}, 'results_included': False})
    return archive


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('zip64_limit', [zipfile.ZIP64_LIMIT, 32])
def test_import_archive_preflight_preserves_exact_source_files(tmp_path, version, monkeypatch, zip64_limit):
    monkeypatch.setattr(zipfile, 'ZIP64_LIMIT', zip64_limit)
    archive = package(tmp_path, version=version)
    summary = inspect(archive, tmp_path / 'read', Limits(), threading.Event())
    assert summary['manifest']['publication']['version'] == 3
    assert summary['categories']['publication'] == 2
    assert len(summary['warnings']) == (2 if version == 1 else 1)
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            path = tmp_path / 'read' / name
            assert path.read_bytes() == zipped.read(name)
            assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('field,value', [
    ('required_tool_names', {}), ('required_tool_names', [None]),
    ('tool_names', ['valid', {}]), ('service_identity', []),
])
def test_malformed_destination_requirements_are_rejected_during_inspection(tmp_path, field, value):
    source = str(uuid.uuid4())
    def modify(writer):
        writer.json(f'requirements/mcp/{source}.json', {
            'id': source, 'name': 'Untrusted', 'transport': 'stdio', field: value})
    with pytest.raises(PackageError, match='service identity or required tool names'):
        inspect(package(tmp_path, modify=modify), tmp_path / 'read', Limits(), threading.Event())


@pytest.mark.parametrize('version', [0, 3, '2'])
def test_rejects_unsupported_schema_without_guessing(tmp_path, version):
    with pytest.raises(PackageError, match='Unsupported'):
        inspect(package(tmp_path, version=version), tmp_path / 'read', Limits(), threading.Event())


@pytest.mark.parametrize('name,kind', [('../escape', 'file'), ('/absolute', 'file'), ('x/../escape', 'file'),
    ('x\\y', 'file'), ('x//y', 'file'), ('x', 'symlink'), ('x', 'socket'), ('x', 'fifo'),
    ('x', 'directory'), ('x/', 'file'), ('x', 'dos-directory'), ('x', 'device'),
    ('x\n', 'file'), ('x' * 1025, 'file'), ('credentials.json', 'file')])
def test_rejects_unsafe_archive_entries(tmp_path, name, kind):
    path = tmp_path / 'malformed.zip'
    with zipfile.ZipFile(path, 'w') as zipped:
        member = zipfile.ZipInfo(name)
        member.external_attr = {'file': stat.S_IFREG, 'symlink': stat.S_IFLNK, 'socket': stat.S_IFSOCK,
            'fifo': stat.S_IFIFO, 'directory': stat.S_IFDIR, 'device': stat.S_IFCHR, 'dos-directory': 0}[kind] << 16
        if kind == 'dos-directory':
            member.external_attr |= 0x10
        zipped.writestr(member, b'/tmp/escape')
    with pytest.raises(ValueError):
        inspect(path, tmp_path / 'read', Limits(), threading.Event())


@pytest.mark.parametrize('names', [('same', 'same'), ('same', 'same/child'), ('same/child', 'same')])
def test_duplicate_and_conflicting_members_rejected(tmp_path, names):
    path = tmp_path / 'malformed.zip'
    with zipfile.ZipFile(path, 'w') as zipped:
        zipped.writestr(names[0], b'')
        if names[0] == names[1]:
            with pytest.warns(UserWarning, match='Duplicate name'):
                zipped.writestr(names[1], b'')
        else:
            zipped.writestr(names[1], b'')
    with pytest.raises(PackageError, match='Malformed'):
        inspect(path, tmp_path / 'read', Limits(), threading.Event())


@pytest.mark.parametrize('field,value', [('compressed', 1), ('expanded', 100), ('members', 1), ('document', 10)])
def test_configurable_limits(tmp_path, field, value):
    path = package(tmp_path)
    with pytest.raises(PackageError, match='limit|count'):
        inspect(path, tmp_path / 'read', replace(Limits(), **{field: value}), threading.Event())


def test_cancellation_stops_copy(tmp_path):
    stop = threading.Event()
    stop.set()
    with pytest.raises(PackageError, match='cancelled'):
        inspect(package(tmp_path), tmp_path / 'read', Limits(), stop)


def test_hash_mismatch_rejected_before_activation(tmp_path):
    path = package(tmp_path)
    tampered = tmp_path / 'tampered.zip'
    with zipfile.ZipFile(path) as original, zipfile.ZipFile(tampered, 'w') as output:
        for name in original.namelist():
            raw = original.read(name)
            if name == 'publication/settings.json':
                raw = b'{}'
            output.writestr(name, raw)
    with pytest.raises(PackageError, match='hash mismatch'):
        inspect(tampered, tmp_path / 'read', Limits(), threading.Event())


def test_inconsistent_graph_reference_rejected(tmp_path):
    path = package(tmp_path, graph={'nodes': [], 'edges': [{'source': 'foreign', 'target': 'absent'}]})
    with pytest.raises(PackageError, match='missing node'):
        inspect(path, tmp_path / 'read', Limits(), threading.Event())


def test_duplicate_json_keys_rejected(tmp_path):
    def modify(writer):
        raw = tmp_path / 'bad.json'
        raw.write_bytes(b'{"same":1,"same":2}')
        writer.add(raw, 'requirements/bad.json', provenance='publication_pinned', identity='bad')
    with pytest.raises(PackageError, match='Duplicate JSON key'):
        inspect(package(tmp_path, modify=modify), tmp_path / 'read', Limits(), threading.Event())


def test_no_scripts_executed_during_inspection(tmp_path):
    sentinel = tmp_path / 'executed'
    graph = {'nodes': [{'id': 'script', 'type': 'script', 'data': {'config': {
        'code': f'open({str(sentinel)!r}, "w").write("bad")'}}}], 'edges': []}
    inspect(package(tmp_path, graph=graph), tmp_path / 'read', Limits(), threading.Event())
    assert not sentinel.exists()


def test_unicode_resource_paths_and_zip_comments_preserve_bytes(tmp_path):
    name = 'resources/knowledge/unicode/é/' + 'x' * 110
    def modify(writer):
        raw = tmp_path / 'resource'
        raw.write_bytes(b'\x00\xffopaque\r\n')
        writer.add(raw, name, identity='resource', provenance='captured_at_download')
        writer.archive.comment = b'ZIP package comment'
    inspect(package(tmp_path, modify=modify), tmp_path / 'read', Limits(), threading.Event())
    assert (tmp_path / 'read' / name).read_bytes() == b'\x00\xffopaque\r\n'


@pytest.mark.parametrize('fault', ['metadata-size', 'member-count', 'hidden-members'])
def test_metadata_limits_apply_before_zipfile_buffers_the_directory(tmp_path, monkeypatch, fault):
    path = package(tmp_path)
    data = bytearray(path.read_bytes())
    end = data.rindex(b'PK\x05\x06')
    limits = Limits(members=1) if fault != 'metadata-size' else Limits()
    if fault == 'metadata-size':
        struct.pack_into('<I', data, end + 12, limits.document + 1)
    if fault == 'hidden-members':
        struct.pack_into('<HH', data, end + 8, 0, 0)
    path.write_bytes(data)
    def forbidden(*args, **kwargs):
        pytest.fail('ZipFile must not buffer rejected metadata')
    monkeypatch.setattr(zipfile, 'ZipFile', forbidden)
    with pytest.raises(PackageError, match='limit|count'):
        inspect(path, tmp_path / 'read', limits, threading.Event())


@pytest.mark.parametrize('fault', [
    'encrypted', 'compression', 'crc', 'nul-name', 'truncated', 'trailing', 'multi-disk',
    'prefixed', 'directory-offset', 'directory-signature', 'directory-count', 'directory-entry-size'])
def test_invalid_or_unsupported_zip_is_rejected_before_activation(tmp_path, fault):
    path = package(tmp_path)
    data = bytearray(path.read_bytes())
    directory = data.index(b'PK\x01\x02')
    end = data.rindex(b'PK\x05\x06')
    if fault == 'encrypted':
        struct.pack_into('<H', data, directory + 8, 1)
    elif fault == 'compression':
        struct.pack_into('<H', data, directory + 10, 99)
    elif fault == 'crc':
        struct.pack_into('<I', data, directory + 16, 0)
    elif fault == 'nul-name':
        data[directory + 46] = 0
    elif fault == 'truncated':
        del data[-1:]
    elif fault == 'trailing':
        data.extend(b'unexpected trailing data')
    elif fault == 'multi-disk':
        struct.pack_into('<H', data, end + 4, 1)
    elif fault == 'prefixed':
        data[:0] = b'prefix'
    elif fault == 'directory-offset':
        struct.pack_into('<I', data, end + 16, 0)
    elif fault == 'directory-signature':
        data[directory] = 0
    elif fault == 'directory-count':
        struct.pack_into('<HH', data, end + 8, 0, 0)
    else:
        struct.pack_into('<H', data, directory + 30, 65535)  # Extra field overruns the directory.
    path.write_bytes(data)
    with pytest.raises(PackageError):
        inspect(path, tmp_path / 'read', Limits(), threading.Event())


@pytest.mark.parametrize('fault', ['record-size', 'multi-disk', 'offset'])
def test_malformed_zip64_directory_is_rejected(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(zipfile, 'ZIP64_LIMIT', 32)
    path = package(tmp_path)
    data = bytearray(path.read_bytes())
    record = data.rindex(b'PK\x06\x06')
    locator = data.rindex(b'PK\x06\x07')
    if fault == 'record-size':
        struct.pack_into('<Q', data, record + 4, 43)
    elif fault == 'multi-disk':
        struct.pack_into('<I', data, locator + 4, 1)
    else:
        struct.pack_into('<Q', data, locator + 8, 0)
    path.write_bytes(data)
    with pytest.raises(PackageError, match='ZIP64'):
        inspect(path, tmp_path / 'read', Limits(), threading.Event())


def test_rejects_tar_gzip_instead_of_guessing_an_archive_format(tmp_path):
    path = tmp_path / 'old.tar.gz'
    path.write_bytes(gzip.compress(b'\0' * 1024))
    with pytest.raises(PackageError, match='ZIP'):
        inspect(path, tmp_path / 'read', Limits(), threading.Event())


def test_expanded_limit_applies_across_all_files(tmp_path):
    path = package(tmp_path)
    with zipfile.ZipFile(path) as zipped:
        sizes = [member.file_size for member in zipped.infolist()]
    limit = sum(sizes) - 1
    assert max(sizes) < limit
    with pytest.raises(PackageError, match='expanded limit'):
        inspect(path, tmp_path / 'read', replace(Limits(), expanded=limit), threading.Event())


def test_cancellation_during_copy_stops_extraction(tmp_path, monkeypatch):
    path = package(tmp_path)
    stop = threading.Event()
    original = zipfile.ZipExtFile.read
    def cancel_after_read(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        stop.set()
        return value
    monkeypatch.setattr(zipfile.ZipExtFile, 'read', cancel_after_read)
    with pytest.raises(PackageError, match='cancelled'):
        inspect(path, tmp_path / 'read', Limits(), stop)


@pytest.mark.parametrize('suffix', ['json', 'jsonl'])
def test_opaque_resource_extensions_do_not_imply_a_schema_or_rewrite_bytes(tmp_path, suffix):
    raw = b'\xff\x00not-a-JSON-document\r\n'
    name = f'resources/knowledge/source/opaque.{suffix}'
    def modify(writer):
        file = tmp_path / 'opaque'
        file.write_bytes(raw)
        writer.add(file, name, identity='source', provenance='captured_at_download')
    inspect(package(tmp_path, modify=modify), tmp_path / 'read', Limits(), threading.Event())
    assert (tmp_path / 'read' / name).read_bytes() == raw


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('fault', ['traversal', 'noncanonical', 'filename', 'directory', 'requirement_missing'])
async def test_knowledge_identity_and_directory_must_agree_before_reconstruction(tmp_path, version, fault):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from asaree.services.package_reconstruction import Reconstruction

    source = str(uuid.uuid4())
    identifier = {'traversal': '../../../safe-sibling', 'noncanonical': source.upper()}.get(fault, source)
    sibling = tmp_path / 'safe-sibling'
    sibling.mkdir()
    (sibling / 'sentinel.txt').write_bytes(b'outside extracted archive')

    def modify(writer):
        if fault != 'requirement_missing':
            filename = 'safe' if fault in {'traversal', 'filename'} else identifier
            writer.json(f'requirements/mcp/{filename}.json', {
                'id': identifier, 'name': 'fixture', 'transport': 'stdio', 'knowledge_kind': 'bundle'})
        writer.json(f'resources/knowledge/{source}/content.json', {})
        writer.dependency(identifier, 'knowledge', 'captured_at_download',
                          directory='publication' if fault in {'traversal', 'directory'}
                          else f'resources/knowledge/{identifier}', file_count=1)

    with pytest.raises(PackageError):
        inspect(package(tmp_path, version=version, modify=modify), tmp_path / 'read', Limits(), threading.Event())
    # Reconstruction also refuses inconsistent metadata, even when called with
    # the rejected, partially extracted fixture instead of a verified payload.
    if fault != 'requirement_missing':
        job = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
        db = SimpleNamespace(add=lambda record: None)
        motoro = SimpleNamespace(get=AsyncMock(return_value=None), add=lambda record: None, flush=AsyncMock())
        with pytest.raises(PackageError):
            await Reconstruction(db, job, tmp_path / 'read', Limits()).servers(motoro)
    assert (sibling / 'sentinel.txt').read_bytes() == b'outside extracted archive'


@pytest.mark.parametrize('version', [1, 2])
def test_missing_name_only_dependencies_remain_explicit_diagnostics(tmp_path, version):
    def modify(writer):
        writer.dependency('unavailable legacy name', 'knowledge', 'captured_at_download', status='missing')
        writer.dependency('unavailable external tool', 'mcp', 'captured_at_download', status='missing')
    summary = inspect(package(tmp_path, version=version, modify=modify), tmp_path / 'read', Limits(), threading.Event())
    assert summary['unresolved_count'] == 2
    assert {record['id'] for record in summary['diagnostics']} == {
        'unavailable legacy name', 'unavailable external tool'}


@pytest.mark.parametrize('referenced', [False, True])
@pytest.mark.parametrize('fault', ['different_id', 'noncanonical_id', 'noncanonical_filename'])
def test_split_identity_must_match_filename_and_pin(tmp_path, referenced, fault):
    source, split = str(uuid.uuid4()), str(uuid.uuid4())
    identifier = str(uuid.uuid4()) if fault == 'different_id' else split
    if fault == 'noncanonical_id':
        identifier = identifier.upper()
    filename = split.upper() if fault == 'noncanonical_filename' else split

    def modify(writer):
        writer.dependency(source, 'dataset', 'publication_pinned', status='missing')
        writer.json(f'inputs/splits/{filename}.json', {
            'id': identifier, 'dataset_id': source, 'source_sha256': '0' * 64,
            'train_artifact_id': None, 'test_artifact_id': None,
        })
        if referenced:
            writer.json('inputs/bindings.json', {'schema_version': 1, 'inputs': [{
                'dataset_id': source, 'mode': 'whole_dataset', 'raw_artifact': None,
                'raw_sha256': '0' * 64, 'split_id': split,
                'train_artifact': None, 'test_artifact': None,
            }]})

    with pytest.raises(PackageError, match='identity|canonical'):
        inspect(package(tmp_path, modify=modify), tmp_path / 'read', Limits(), threading.Event())


@pytest.mark.parametrize('version', [1, 2])
async def test_verified_knowledge_source_containment_is_rechecked_before_copy(tmp_path, version, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from asaree.services import managed_storage
    from asaree.services.package_reconstruction import Reconstruction

    source = str(uuid.uuid4())
    folder = f'resources/knowledge/{source}'
    def modify(writer):
        writer.json(f'requirements/mcp/{source}.json', {
            'id': source, 'name': 'fixture', 'transport': 'stdio', 'knowledge_kind': 'bundle'})
        writer.json(f'{folder}/content.json', {})
        writer.dependency(source, 'knowledge', 'captured_at_download', directory=folder, file_count=1)
    payload = tmp_path / 'read'
    assert inspect(package(tmp_path, version=version, modify=modify), payload, Limits(),
                   threading.Event())['unresolved_count'] == 0
    # Defense at the copy boundary even if the private verified tree is changed.
    sibling = tmp_path / 'safe-sibling'
    sibling.mkdir()
    (sibling / 'sentinel.txt').write_bytes(b'outside extracted archive')
    (payload / folder / 'content.json').unlink()
    (payload / folder / 'sentinel.txt').symlink_to(sibling / 'sentinel.txt')
    namespaces = {name: tmp_path / 'managed' / name for name in ('bundles', 'documents')}
    monkeypatch.setattr(managed_storage, 'roots', lambda: namespaces)
    db = SimpleNamespace(add=lambda record: None)
    motoro = SimpleNamespace(get=AsyncMock(return_value=None), add=lambda record: None, flush=AsyncMock())
    job = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    with pytest.raises(PackageError, match='outside|containment'):
        await Reconstruction(db, job, payload, Limits()).servers(motoro)
    assert not list((tmp_path / 'managed').rglob('sentinel.txt'))
