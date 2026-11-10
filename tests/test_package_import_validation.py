"""Real exporter archives through the untrusted upload parser."""
import io
import tarfile
import threading
import uuid
from dataclasses import replace

import pytest

from asaree.services.experiment_packages import PackageWriter
from asaree.services.package_validation import Limits, PackageError, inspect


def package(tmp_path, *, version=2, graph=None, modify=None):
    stage = tmp_path / 'writer'
    stage.mkdir()
    archive = tmp_path / 'package.tar.gz'
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
def test_exporter_archive_preflight_preserves_exact_source_files(tmp_path, version):
    archive = package(tmp_path, version=version)
    summary = inspect(archive, tmp_path / 'read', Limits(), threading.Event())
    assert summary['manifest']['publication']['version'] == 3
    assert summary['categories']['publication'] == 2
    assert len(summary['warnings']) == (2 if version == 1 else 1)
    with tarfile.open(archive) as tar:
        for member in tar:
            assert (tmp_path / 'read' / member.name).read_bytes() == tar.extractfile(member).read()


@pytest.mark.parametrize('version', [0, 3, '2'])
def test_rejects_unsupported_schema_without_guessing(tmp_path, version):
    with pytest.raises(PackageError, match='Unsupported'):
        inspect(package(tmp_path, version=version), tmp_path / 'read', Limits(), threading.Event())


@pytest.mark.parametrize('name,kind', [('../escape', 'file'), ('/absolute', 'file'), ('x/../escape', 'file'),
    ('x\\y', 'file'), ('x//y', 'file'), ('x', 'symlink'), ('x', 'hardlink'), ('x', 'fifo'), ('x', 'directory')])
def test_rejects_unsafe_archive_entries(tmp_path, name, kind):
    path = tmp_path / 'malformed.tar.gz'
    with tarfile.open(path, 'w:gz') as tar:
        member = tarfile.TarInfo(name)
        member.type = {'file': tarfile.REGTYPE, 'symlink': tarfile.SYMTYPE, 'hardlink': tarfile.LNKTYPE,
                       'fifo': tarfile.FIFOTYPE, 'directory': tarfile.DIRTYPE}[kind]
        member.linkname = '/tmp/escape'
        tar.addfile(member)
    with pytest.raises(ValueError):
        inspect(path, tmp_path / 'read', Limits(), threading.Event())


def test_duplicate_and_conflicting_members_rejected(tmp_path):
    path = tmp_path / 'malformed.tar.gz'
    with tarfile.open(path, 'w:gz') as tar:
        tar.addfile(tarfile.TarInfo('same'))
        tar.addfile(tarfile.TarInfo('same'))
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
    tampered = tmp_path / 'tampered.tar.gz'
    with tarfile.open(path) as original, tarfile.open(tampered, 'w:gz') as output:
        for member in original:
            raw = original.extractfile(member).read()
            if member.name == 'publication/settings.json':
                raw = b'{}'
                member.size = len(raw)
            output.addfile(member, io.BytesIO(raw))
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


@pytest.mark.parametrize('headers', [{'GNU.sparse.map': '0,1000000000'}, {'comment': 'unsupported'},
                                    {'path': 'unicode/é/' + 'x' * 110}])
def test_pax_metadata_is_bounded_and_only_exporter_extensions_are_accepted(tmp_path, headers):
    archive = tmp_path / 'metadata.tar.gz'
    with tarfile.open(archive, 'w:gz', format=tarfile.PAX_FORMAT) as tar:
        member = tarfile.TarInfo('payload')
        member.pax_headers = headers
        tar.addfile(member)
    if 'path' in headers:
        # The supported long Unicode path is parsed; this standalone fixture
        # then fails because it is not a complete experiment package.
        with pytest.raises(PackageError, match='Malformed'):
            inspect(archive, tmp_path / 'read', Limits(), threading.Event())
        assert (tmp_path / 'read' / headers['path']).is_file()
    else:
        with pytest.raises(PackageError, match='metadata field'):
            inspect(archive, tmp_path / 'read', Limits(), threading.Event())


def test_oversized_pax_is_rejected_before_buffering_its_declared_contents(tmp_path):
    archive = tmp_path / 'metadata.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        header = tarfile.TarInfo('metadata')
        header.type, header.size = tarfile.XHDTYPE, 1024 * 1024
        tar.addfile(header, io.BytesIO(b'\0' * header.size))
    with pytest.raises(PackageError, match='Oversized'):
        inspect(archive, tmp_path / 'read', Limits(), threading.Event())


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
