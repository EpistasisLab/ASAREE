"""ZIP upload, retry and retained source downloads without external services."""
import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_package_import_validation import package

from asaree.api import package_imports as api
from asaree.models.package_import import PackageImport
from asaree.services import package_imports as imports
from asaree.services.package_validation import Limits, digest, inspect


@pytest.fixture
def upload_job(tmp_path, monkeypatch):
    job = PackageImport(id=uuid.uuid4(), owner_id=uuid.uuid4(), request_key='zip-upload', status='uploading',
        preflight={}, progress={}, expires_at=datetime.now(UTC) + timedelta(hours=1))
    db = SimpleNamespace(scalar=AsyncMock(return_value=True), commit=AsyncMock())
    root = tmp_path / 'upload'
    monkeypatch.setattr(imports, 'directory', lambda identifier: root)
    monkeypatch.setattr(imports, 'limits', Limits)
    monkeypatch.setattr(api, 'owned', AsyncMock(return_value=job))
    return job, db, root


async def test_uploaded_zip_can_be_inspected_retried_and_downloaded(tmp_path, upload_job):
    job, db, root = upload_job
    source = tmp_path / 'source'
    source.mkdir()
    archive = package(source)
    data = archive.read_bytes()

    async def chunks():
        yield data[:17]
        yield data[17:]

    response = await api.upload(job.id, SimpleNamespace(stream=chunks), db, SimpleNamespace(id=job.owner_id))
    stored = root / 'archive.zip'
    assert stored.read_bytes() == data
    assert stored.stat().st_mode & 0o777 == 0o600
    assert response['status'] == 'validating'
    assert response['sha256'] == digest(archive)
    assert not (root / 'archive.part').exists()
    job.preflight = inspect(stored, root / 'payload', Limits(), threading.Event())
    job.status, job.generation = 'failed', 0
    retried = await api.retry(job.id, db, SimpleNamespace(id=job.owner_id))
    assert retried['status'] == 'validating'
    assert job.generation == 1
    job.status = 'queued'
    source_response = api.source_response(job, None)
    assert source_response.path == stored
    assert source_response.headers['content-type'] == 'application/zip'
    assert source_response.headers['content-disposition'] == 'attachment; filename="archive.zip"'
    member_response = api.source_response(job, 'publication/canvas.json')
    assert member_response.path == root / 'payload/publication/canvas.json'
    assert member_response.headers['content-type'] == 'application/octet-stream'
