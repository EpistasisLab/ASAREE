"""ASAREE's activation barrier for resources staged in another database."""
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from asaree.models.database import get_session
from asaree.models.package_import import ImportedIdentity, PackageImport


def active_identity(column: Any) -> Any:
    return ~select(ImportedIdentity.import_id).join(PackageImport).where(
        ImportedIdentity.local_id == column, PackageImport.status != 'completed',
    ).exists()


async def active_resources(resources: Sequence[Any]) -> list[Any]:
    if not resources:
        return []
    identifiers = [resource.id for resource in resources]
    async with get_session() as db:
        pending = set((await db.scalars(select(ImportedIdentity.local_id).join(PackageImport).where(
            ImportedIdentity.local_id.in_(identifiers), PackageImport.status != 'completed',
        ))).all())
    return [resource for resource in resources if resource.id not in pending]


async def resource_active(identifier: uuid.UUID) -> bool:
    async with get_session() as db:
        return not bool(await db.scalar(select(ImportedIdentity.import_id).join(PackageImport).where(
            ImportedIdentity.local_id == identifier, PackageImport.status != 'completed',
        ).limit(1)))
