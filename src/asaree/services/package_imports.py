"""Recoverable PostgreSQL import commands, relayed by the existing results worker."""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from motoro.models.database import system_session
from motoro.models.mcp_server import MCPServerConfig
from motoro.models.skill import Skill
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.dataset import RegisteredDataset
from asaree.models.experiment import ResearchExperiment
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.persona import Persona
from asaree.models.protocol import Protocol
from asaree.services import managed_storage, package_source
from asaree.services.datasets import discard_registration_files
from asaree.services.package_reconstruction import Reconstruction, identity, registration_error
from asaree.services.package_validation import Limits, PackageError, digest, inspect

logger = logging.getLogger(__name__)


def limits() -> Limits:
    settings = get_settings()
    return Limits(settings.package_import_max_compressed, settings.package_import_max_expanded,
                  settings.package_import_max_members, settings.package_import_max_document)


def directory(identifier: uuid.UUID) -> Path:
    return package_source.scratch_path(identifier)


def archive_path(identifier: uuid.UUID) -> Path:
    return package_source.archive_path(identifier)


async def joined(operation: Any, *args: Any, stopping: threading.Event, identifier: uuid.UUID) -> Any:
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.5)
            async with get_session() as db:
                state = await db.scalar(select(PackageImport.status).where(PackageImport.id == identifier))
                if state in {'cancelled', 'expired'}:
                    stopping.set()
        return await task
    except BaseException:
        stopping.set()
        with suppress(BaseException):
            await asyncio.shield(task)
        raise


async def compensate(job: PackageImport) -> None:
    """Delete only this command's deterministic Motoro records, never library matches."""
    # No resources can be staged until inspection has persisted both preflight
    # and archive hash. Failed/partial extraction is untrusted cleanup input.
    if not job.sha256 or not job.preflight.get('manifest'):
        return
    if not archive_path(job.id).is_file():
        return
    async with system_session(reason='compensate incomplete owned package import') as db:
        for dep in package_source.records(job.id, 'dependencies.jsonl', limits()):
            if dep.get('kind') == 'skill':
                await db.execute(delete(Skill).where(Skill.id == identity(job, 'skill', dep['id']),
                                                    Skill.owner_id == job.owner_id, Skill.is_system.is_(False)))
            if dep.get('kind') == 'mcp':
                local = identity(job, 'mcp', dep['id'])
                server = await db.scalar(select(MCPServerConfig).where(MCPServerConfig.id == local,
                    MCPServerConfig.owner_id == job.owner_id, MCPServerConfig.is_system.is_(False)))
                if server is not None and server.command:
                    from asaree.services.okf_bundles import bundle_path_from_command

                    raw = bundle_path_from_command(server.command)
                    if raw:
                        destination = Path(raw).resolve()
                        roots = managed_storage.roots()
                        if any(destination.is_relative_to(roots[namespace] / str(job.owner_id))
                               for namespace in ('bundles', 'documents')):
                            await asyncio.to_thread(shutil.rmtree, destination, ignore_errors=True)
                await db.execute(delete(MCPServerConfig).where(MCPServerConfig.id == local,
                    MCPServerConfig.owner_id == job.owner_id, MCPServerConfig.is_system.is_(False)))
    # Dataset files use the ordinary registration layout, even when a worker
    # died before its product transaction (and identity ledger) could commit.
    if 'inputs/bindings.json' in package_source.members(job.id, 'inputs/'):
        bindings = package_source.document(job.id, 'inputs/bindings.json', limits())
        for pin in bindings.get('inputs', []):
            await asyncio.to_thread(discard_registration_files, identity(job, 'dataset', pin['dataset_id']))


async def checkpoint(identifier: uuid.UUID, phase: str, **progress: Any) -> None:
    async with get_session() as db:
        job = await db.get(PackageImport, identifier)
        if job is None or job.status in {'cancelled', 'expired'}:
            raise PackageError('Import cancelled or expired')
        job.progress = {'phase': phase, **progress}


async def activate_resources(job: PackageImport) -> None:
    """Retryable visibility flip; no server registration or connection calls."""
    async with system_session(reason='activate staged package resources without execution') as db:
        for dep in package_source.records(job.id, 'dependencies.jsonl', limits()):
            if dep.get('kind') == 'skill':
                local = identity(job, 'skill', dep['id'])
                name = await db.scalar(select(Skill.name).where(Skill.id == local, Skill.owner_id == job.owner_id))
                try:
                    await db.execute(update(Skill).where(Skill.id == local,
                        Skill.owner_id == job.owner_id).values(deleted_at=None))
                except IntegrityError as exc:
                    raise registration_error('skill', name or dep['id'], exc) from exc


async def remove_staged_product(db: Any, job: PackageImport) -> None:
    if job.experiment_id is None:
        return
    await db.execute(delete(Protocol).where(Protocol.experiment_id == job.experiment_id,
                                           Protocol.owner_id == job.owner_id))
    await db.execute(delete(ResearchExperiment).where(ResearchExperiment.id == job.experiment_id,
                                                     ResearchExperiment.owner_id == job.owner_id))
    identifiers = select(ImportedIdentity.local_id).where(ImportedIdentity.import_id == job.id,
                                                         ImportedIdentity.kind == 'dataset')
    await db.execute(delete(RegisteredDataset).where(RegisteredDataset.id.in_(identifiers),
                                                    RegisteredDataset.owner_id == job.owner_id))
    await db.execute(delete(Persona).where(Persona.owner_id == job.owner_id, Persona.id.in_(
        select(ImportedIdentity.local_id).where(ImportedIdentity.import_id == job.id,
                                               ImportedIdentity.kind == 'persona'))))
    await db.execute(delete(ImportedIdentity).where(ImportedIdentity.import_id == job.id))
    job.experiment_id = None


async def execute_package_import(_ctx: dict[str, Any], job_id: str) -> None:
    identifier = uuid.UUID(job_id)
    stopping = threading.Event()
    try:
        async with get_session() as db:
            # Session lifetime owns the command lock. Worker death releases it;
            # the relay retries durable validating/activating commands.
            lock = int.from_bytes(identifier.bytes[:8], 'big', signed=True)
            if not await db.scalar(select(func.pg_try_advisory_xact_lock(lock))):
                return
            job = await db.get(PackageImport, identifier)
            if job is None or job.status not in {'validating', 'queued', 'activating'}:
                return
            root = directory(identifier)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            archive, payload = archive_path(identifier), root / 'payload'
            if job.status == 'validating':
                for path in (payload, root / 'payload-verified'):
                    await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
                await checkpoint(identifier, 'validating')
                preflight = await joined(inspect, archive, payload, limits(), stopping,
                                         stopping=stopping, identifier=identifier)
                sha256 = await asyncio.to_thread(digest, archive)
                async with get_session() as status_db:
                    await status_db.execute(update(PackageImport).where(
                        PackageImport.id == identifier, PackageImport.status == 'validating',
                    ).values(status='queued', preflight=preflight, sha256=sha256,
                             progress={'phase': 'validated'}, error=None))
                return
            await checkpoint(identifier, 'reconstructing')
            if await asyncio.to_thread(digest, archive) != job.sha256:
                raise PackageError('Uploaded archive changed since preflight')
            # A committed product staging transaction only needs the final
            # resource visibility flip after an interrupted activation.
            if job.experiment_id is not None:
                await activate_resources(job)
                current = await db.scalar(select(PackageImport).where(PackageImport.id == identifier)
                    .with_for_update().execution_options(populate_existing=True))
                assert current is not None
                if current.status == 'cancelled':
                    raise PackageError('Import cancelled')
                current.status, current.error = 'completed', None
                current.progress = {'phase': 'completed'}
                await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)
                return
            # Re-inspect verified bytes for every activation/retry, including
            # partial extraction left by a terminated worker.
            for path in (payload, root / 'payload-verified'):
                await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
            await joined(inspect, archive, payload, limits(), stopping, stopping=stopping, identifier=identifier)
            async def progress(phase: str, **counts: Any) -> None:
                await checkpoint(identifier, phase, **counts)

            async def io(operation: Any, *args: Any) -> Any:
                return await joined(operation, *args, stopping=stopping, identifier=identifier)

            async with system_session(reason='reconstruct user-owned portable package without execution') as motoro:
                reconstruction = Reconstruction(db, job, payload, limits(), progress=progress,
                                                io=io, stopping=stopping)
                experiment_id = await reconstruction.activate(motoro)
                await checkpoint(identifier, 'activating', experiment_id=str(experiment_id))
                # Motoro commits first. On a crash before ASAREE commit, retry
                # adopts only deterministic owned records from this request.
            # Lock the command only at activation, allowing cancellation during
            # IO and reconstruction. Serialize the final cancel/activate race.
            current = await db.scalar(select(PackageImport).where(PackageImport.id == identifier)
                                      .with_for_update().execution_options(populate_existing=True))
            if current.status == 'cancelled':
                raise PackageError('Import cancelled')
            current.experiment_id = experiment_id
            current.status = 'activating'
            current.progress = {'phase': 'activating_resources'}
            current.error = None
            await db.commit()
            # Native records remain hidden while the cross-database activation
            # finishes. The relay can recover this boundary after worker death.
            await db.scalar(select(func.pg_advisory_xact_lock(lock)))
            await activate_resources(current)
            current = await db.scalar(select(PackageImport).where(PackageImport.id == identifier)
                .with_for_update().execution_options(populate_existing=True))
            if current.status == 'cancelled':
                raise PackageError('Import cancelled')
            current.status = 'completed'
            current.progress = {'phase': 'completed'}
            await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)
    except (Exception, asyncio.CancelledError) as exc:
        logger.exception('package_import_failed', extra={'import_id': job_id})
        async with get_session() as db:
            job = await db.get(PackageImport, identifier)
            if job is not None and job.status != 'completed':
                # Compensation is retryable independently if Motoro is down.
                try:
                    if job.experiment_id is None or job.status in {'cancelled', 'expired'}:
                        await compensate(job)
                        await remove_staged_product(db, job)
                except Exception:
                    job.progress = {'phase': 'compensation_required'}
                if job.status not in {'cancelled', 'expired'}:
                    job.status = 'failed'
                job.error = (str(exc)[:2000] if isinstance(exc, ValueError)
                             else 'Import interrupted; retry this request.')
        if isinstance(exc, asyncio.CancelledError):
            raise


async def relay_imports(ctx: dict[Any, Any], *args: Any, **kwargs: Any) -> None:
    from asaree.worker.enqueue import _get_pool

    async with get_session() as db:
        identifiers = (await db.scalars(select(PackageImport.id).where(
            PackageImport.status.in_(['validating', 'queued', 'activating']),
        ).order_by(PackageImport.created_at).limit(4))).all()
        expired = (await db.scalars(select(PackageImport.id).where(
            PackageImport.status.not_in(['completed', 'expired']),
            or_(PackageImport.status == 'cancelled', PackageImport.expires_at < datetime.now(UTC)),
        ).limit(10))).all()
    for identifier in expired:
        try:
            # Each request has its own rollback/lock boundary. A broken resource
            # or unavailable resource database must not poison the whole relay.
            async with get_session() as db:
                lock = int.from_bytes(identifier.bytes[:8], 'big', signed=True)
                if not await db.scalar(select(func.pg_try_advisory_xact_lock(lock))):
                    continue
                job = await db.get(PackageImport, identifier)
                if job is None or job.status in {'completed', 'expired'}:
                    continue
                if job.status != 'cancelled' and job.expires_at >= datetime.now(UTC):
                    continue
                await compensate(job)
                await remove_staged_product(db, job)
                await asyncio.to_thread(shutil.rmtree, directory(job.id), ignore_errors=True)
                await asyncio.to_thread(archive_path(job.id).unlink, missing_ok=True)
                job.status = 'expired'
        except Exception:
            logger.exception('package_import_cleanup_failed', extra={'import_id': str(identifier)})
            try:
                async with get_session() as db:
                    job = await db.get(PackageImport, identifier)
                    if job is not None and job.status not in {'completed', 'expired'}:
                        job.progress = {'phase': 'compensation_required'}
            except Exception:
                logger.exception('package_import_cleanup_status_failed', extra={'import_id': str(identifier)})
    if identifiers:
        pool = await _get_pool()
        for identifier in identifiers:
            try:
                await pool.enqueue_job('execute_package_import', str(identifier),
                                       _job_id=f'package-import:{identifier}', _queue_name='arq:results')
            except Exception:
                logger.exception('package_import_enqueue_failed', extra={'import_id': str(identifier)})
