"""Protocol creation, lookup, and graph updates."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.protocol import Protocol

_DEFAULT_GRAPH: dict[str, Any] = {"nodes": [], "edges": []}
_SETTABLE_FIELDS = frozenset({"name", "description", "experiment_id", "graph"})


def generated_protocol_name(experiment_name: str, experiment_id: uuid.UUID | str) -> str:
    """The name the canvas auto-assigns a protocol it creates for an experiment.

    The ``[shortid]`` suffix is not decoration: protocol names are unique per
    owner (uq_protocols_owner_name), and two experiments sharing a name is the
    normal case (every new one starts life as "Untitled Experiment"), so
    without it the create would 409 forever. The experiment id is unique by
    construction, so suffixing it makes the name collision-proof.

    The frontend builds the same string when it creates the protocol (see
    ProtocolCanvasPage.tsx's protocolQuery); this is the canonical definition
    the rename sync below matches against.
    """
    return f"Protocol: {experiment_name} [{str(experiment_id)[:8]}]"


def _is_generated_protocol_name(name: str, experiment_id: uuid.UUID | str) -> bool:
    """Whether *name* is still the auto-generated one for this experiment.

    Matched by shape (``Protocol: <anything> [<this experiment's shortid>]``)
    rather than by comparing against the experiment's previous name: a
    protocol created before some earlier rename carries a name we no longer
    have on hand, and it should still be re-synced. A name that doesn't fit
    the shape is one a user deliberately typed, so it is left alone.
    """
    return re.fullmatch(rf"Protocol: .*\[{re.escape(str(experiment_id)[:8])}\]", name or "") is not None


async def sync_protocol_names_to_experiment(
    db: AsyncSession, *, experiment_id: uuid.UUID, experiment_name: str, owner_id: uuid.UUID
) -> list[Protocol]:
    """Re-point an experiment's auto-named protocols at its current name.

    Called on rename so the canvas, the export payload, and the download
    filename don't keep showing the name the experiment had when its protocol
    was first created. Protocols a user renamed by hand are skipped, as is any
    protocol whose target name is already taken by a *different* protocol of
    the same owner (two protocols on one experiment would otherwise both want
    the same string and trip uq_protocols_owner_name).

    Returns the protocols actually renamed.
    """
    target = generated_protocol_name(experiment_name, experiment_id)
    renamed: list[Protocol] = []
    for protocol in await list_protocols(db, owner_id=owner_id, experiment_id=experiment_id):
        if protocol.name == target or not _is_generated_protocol_name(protocol.name, experiment_id):
            continue
        clash = await get_protocol_by_name(db, target, owner_id=owner_id)
        if clash is not None and clash.id != protocol.id:
            continue
        protocol.name = target
        renamed.append(protocol)
    if renamed:
        await db.flush()
    return renamed


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
