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
