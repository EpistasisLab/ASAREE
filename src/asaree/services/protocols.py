"""Protocol creation, lookup, and graph updates."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.protocol import Protocol

_DEFAULT_GRAPH: dict[str, Any] = {"nodes": [], "edges": []}
_SETTABLE_FIELDS = frozenset({"name", "description", "experiment_id", "graph"})


async def create_protocol(
    db: AsyncSession,
    *,
    name: str,
    owner_id: uuid.UUID,
    description: str | None = None,
    experiment_id: uuid.UUID | None = None,
    graph: dict[str, Any] | None = None,
) -> Protocol:
    protocol = Protocol(
        name=name,
        description=description,
        experiment_id=experiment_id,
        graph=graph if graph is not None else dict(_DEFAULT_GRAPH),
        owner_id=owner_id,
    )
    db.add(protocol)
    await db.flush()
    await db.refresh(protocol)
    return protocol


async def get_protocol(db: AsyncSession, protocol_id: uuid.UUID) -> Protocol | None:
    return (await db.execute(select(Protocol).where(Protocol.id == protocol_id))).scalar_one_or_none()


async def get_protocol_by_name(db: AsyncSession, name: str, *, owner_id: uuid.UUID) -> Protocol | None:
    """Fetch an owner's protocol by name, or ``None``.

    Names are unique per owner (uq_protocols_owner_name) -- every call site
    here is a per-owner conflict pre-check, matching
    ``get_experiment_by_name``.
    """
    return (
        await db.execute(select(Protocol).where(Protocol.name == name, Protocol.owner_id == owner_id))
    ).scalar_one_or_none()


async def list_protocols(
    db: AsyncSession, *, owner_id: uuid.UUID, experiment_id: uuid.UUID | None = None
) -> Sequence[Protocol]:
    stmt = select(Protocol).where(Protocol.owner_id == owner_id)
    if experiment_id is not None:
        stmt = stmt.where(Protocol.experiment_id == experiment_id)
    return (await db.execute(stmt)).scalars().all()


async def update_protocol(
    db: AsyncSession, protocol_id: uuid.UUID, *, fields: dict[str, Any]
) -> Protocol | None:
    """Set whichever fields the caller passed (an ``exclude_unset`` dump from
    the API layer), each a full replacement.

    Unlike ``upsert_cell``'s JSONB partial-merge idiom, ``graph`` here is
    always a full replacement, not a merge -- a canvas save is one editor
    session persisting its whole current state atomically, not a partial
    patch against concurrent writers. This also lets ``description``/
    ``experiment_id`` be explicitly set back to ``None`` (clear/detach),
    which a plain ``if value is not None`` check would have made impossible.
    """
    unknown = set(fields) - _SETTABLE_FIELDS
    if unknown:
        raise ValueError(f"not settable on a protocol: {sorted(unknown)}")

    protocol = await get_protocol(db, protocol_id)
    if protocol is None:
        return None
    for key, value in fields.items():
        setattr(protocol, key, value)
    await db.flush()
    await db.refresh(protocol)
    return protocol


async def delete_protocol(db: AsyncSession, protocol_id: uuid.UUID) -> bool:
    protocol = await get_protocol(db, protocol_id)
    if protocol is None:
        return False
    await db.delete(protocol)
    await db.flush()
    return True
