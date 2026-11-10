"""Owned upload/preflight/confirmation and immutable source evidence readers."""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from asaree.config import get_settings
from asaree.deps import CurrentUser, DbSession
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.services import package_imports as imports
from asaree.services.package_reconstruction import validate_local_dependencies
from asaree.services.package_validation import member_path, records

router = APIRouter(prefix='/experiment-package-imports', tags=['experiment-packages'])


class Create(BaseModel):
    request_key: str = Field(min_length=1, max_length=100)


def payload(job: PackageImport) -> dict[str, Any]:
    result = {key: getattr(job, key) for key in (
        'id', 'status', 'sha256', 'preflight', 'progress', 'error', 'experiment_id',
        'expires_at', 'configured_at', 'created_at')}
    if job.preflight and job.status in {'awaiting_confirmation', 'completed'}:
        token = jwt.encode({'type': 'package_import_source', 'job': str(job.id), 'sub': str(job.owner_id),
                            'exp': datetime.now(UTC) + timedelta(minutes=5)},
                           get_settings().auth_secret_key, algorithm='HS256')
        result['source_url'] = f'/api/experiment-package-imports/{job.id}/download?token={token}'
    return result


async def owned(db: DbSession, identifier: uuid.UUID, owner: uuid.UUID, *, lock: bool = False) -> PackageImport:
    query = select(PackageImport).where(PackageImport.id == identifier, PackageImport.owner_id == owner)
    if lock:
        query = query.with_for_update()
    job = await db.scalar(query.execution_options(populate_existing=True))
    if job is None:
        raise HTTPException(404, 'Import not found')
    return job


@router.post('', status_code=201)
async def create(command: Create, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    identifier = uuid.uuid4()
    await db.execute(insert(PackageImport).values(id=identifier, owner_id=user.id, request_key=command.request_key,
        status='uploading', preflight={}, progress={}, generation=0,
        expires_at=datetime.now(UTC) + timedelta(hours=24)).on_conflict_do_nothing(
            constraint='uq_package_import_request'))
    job = await db.scalar(select(PackageImport).where(PackageImport.owner_id == user.id,
                                                     PackageImport.request_key == command.request_key))
    assert job is not None
    await db.commit()
    return payload(job)


@router.get('')
async def list_imports(
    db: DbSession, user: CurrentUser, experiment_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    query = select(PackageImport).where(PackageImport.owner_id == user.id)
    if experiment_id:
        query = query.where(PackageImport.experiment_id == experiment_id)
    return [payload(job) for job in (await db.scalars(query.order_by(PackageImport.created_at.desc()).limit(50))).all()]


@router.get('/{identifier}')
async def get(identifier: uuid.UUID, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    return payload(await owned(db, identifier, user.id))


@router.put('/{identifier}/upload', status_code=202)
async def upload(identifier: uuid.UUID, request: Request, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    job = await owned(db, identifier, user.id)
    lock = int.from_bytes(identifier.bytes[:8], 'big', signed=True)
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(lock))):
        raise HTTPException(409, 'An upload or import is already processing this request')
    if job.status not in {'uploading', 'failed'} or job.preflight:
        raise HTTPException(409, 'Use retry for an inspected upload or create a distinct import request')
    root = imports.directory(identifier)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = root / 'archive.part'
    size, digest = 0, hashlib.sha256()
    try:
        with temporary.open('wb') as output:
            temporary.chmod(0o600)
            async for block in request.stream():
                size += len(block)
                if size > imports.limits().compressed:
                    raise HTTPException(413, 'Compressed package exceeds configured upload limit')
                await asyncio.to_thread(output.write, block)
                digest.update(block)
        temporary.replace(root / 'archive.tar.gz')
        job = await owned(db, identifier, user.id, lock=True)
        if job.status == 'cancelled':
            raise HTTPException(409, 'Import cancelled')
        job.status, job.sha256, job.error = 'validating', digest.hexdigest(), None
        job.progress = {'phase': 'uploaded', 'bytes': size}
        await db.commit()
        return payload(job)
    finally:
        temporary.unlink(missing_ok=True)


@router.post('/{identifier}/confirm', status_code=202)
async def confirm(identifier: uuid.UUID, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    job = await owned(db, identifier, user.id, lock=True)
    if job.status == 'awaiting_confirmation':
        job.status = 'queued'
        job.progress = {'phase': 'confirmed'}
    elif job.status not in {'queued', 'activating', 'completed'}:
        raise HTTPException(409, 'Inspect a valid preflight before confirmation')
    await db.commit()
    return payload(job)


@router.post('/{identifier}/cancel')
async def cancel(identifier: uuid.UUID, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    job = await owned(db, identifier, user.id, lock=True)
    if job.status not in {'completed', 'expired'}:
        job.status = 'cancelled'
    await db.commit()
    return payload(job)


@router.post('/{identifier}/retry', status_code=202)
async def retry(identifier: uuid.UUID, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    job = await owned(db, identifier, user.id, lock=True)
    if job.status != 'failed' or job.expires_at <= datetime.now(UTC):
        raise HTTPException(409, 'Only an unexpired failed import can be retried')
    if not (imports.directory(identifier) / 'archive.tar.gz').is_file():
        raise HTTPException(409, 'Upload archive bytes first')
    # A confirmed failure returns to preflight: user sees the same requirements
    # again. Durable deterministic IDs prevent duplicates after interruption.
    job.status, job.error = 'validating', None
    job.generation += 1
    await db.commit()
    return payload(job)


@router.get('/{identifier}/files')
async def files(identifier: uuid.UUID, db: DbSession, user: CurrentUser,
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    job = await owned(db, identifier, user.id)
    if not job.preflight or job.status in {'expired', 'cancelled'}:
        raise HTTPException(409, 'Source evidence unavailable')
    index = imports.directory(identifier) / 'payload/files.jsonl'
    items = list(itertools.islice(records(index, imports.limits()), offset, offset + limit + 1))
    return {'items': items[:limit], 'next_offset': offset + limit if len(items) > limit else None}


@router.get('/{identifier}/identities')
async def identities(identifier: uuid.UUID, db: DbSession, user: CurrentUser,
                     offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)) -> list[dict[str, Any]]:
    await owned(db, identifier, user.id)
    rows = (await db.scalars(select(ImportedIdentity).where(ImportedIdentity.import_id == identifier)
                            .order_by(ImportedIdentity.kind, ImportedIdentity.source_id)
                            .offset(offset).limit(limit))).all()
    return [{'kind': row.kind, 'source_id': row.source_id, 'local_id': row.local_id,
             'provenance': row.provenance} for row in rows]


@router.get('/{identifier}/source', response_class=FileResponse)
async def source(identifier: uuid.UUID, db: DbSession, user: CurrentUser, path: str | None = None) -> FileResponse:
    job = await owned(db, identifier, user.id)
    return source_response(job, path)


def source_response(job: PackageImport, path: str | None) -> FileResponse:
    if not job.preflight or job.status in {'expired', 'cancelled'}:
        raise HTTPException(409, 'Source evidence unavailable')
    root = imports.directory(job.id)
    try:
        target = root / 'payload' / member_path(path) if path else root / 'archive.tar.gz'
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(404, 'Source file unavailable')
    return FileResponse(target, media_type='application/octet-stream', filename=target.name,
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/{identifier}/download', response_class=FileResponse)
async def download(identifier: uuid.UUID, token: str, db: DbSession, path: str | None = None) -> FileResponse:
    try:
        claims = jwt.decode(token, get_settings().auth_secret_key, algorithms=['HS256'])
        if claims.get('type') != 'package_import_source' or claims.get('job') != str(identifier):
            raise ValueError('Invalid source token scope')
        owner_id = uuid.UUID(claims['sub'])
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise HTTPException(403, 'Source download link expired or invalid') from exc
    return source_response(await owned(db, identifier, owner_id), path)


class Configuration(BaseModel):
    connections_configured: bool
    dependencies_resolved: bool


@router.post('/{identifier}/configuration')
async def configuration(
    identifier: uuid.UUID, command: Configuration, db: DbSession, user: CurrentUser,
) -> dict[str, Any]:
    job = await owned(db, identifier, user.id, lock=True)
    if job.status != 'completed' or not command.connections_configured or not command.dependencies_resolved:
        raise HTTPException(409, 'Configure the editable draft and resolve dependencies before acknowledging')
    try:
        await validate_local_dependencies(db, job)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    job.configured_at = datetime.now(UTC)
    await db.commit()
    return payload(job)
