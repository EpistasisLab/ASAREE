"""Protocols -- the executable agent/tool graph a visual canvas edits.

V1 is data-at-rest only: the graph is freely editable JSON the canvas reads
and writes whole. No execution endpoint exists yet.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from asaree.deps import CurrentUser, DbSession
from asaree.services.experiments import get_experiment
from asaree.services.protocols import (
    create_protocol,
    delete_protocol,
    get_protocol,
    get_protocol_by_name,
    list_protocols,
    update_protocol,
)

router = APIRouter(prefix="/protocols", tags=["protocols"])


class CreateProtocolRequest(BaseModel):
    name: str
    description: str | None = None
    experiment_id: uuid.UUID | None = None
    graph: dict[str, Any] | None = None


class UpdateProtocolRequest(BaseModel):
    """All fields optional; only the ones actually set are written -- same
    "unset vs. null" convention ``UpdateExperimentRequest``/``UpsertCellRequest``
    use. ``graph`` is a full replacement, not a merge (see
    ``services.protocols.update_protocol``)."""

    name: str | None = None
    description: str | None = None
    experiment_id: uuid.UUID | None = None
    graph: dict[str, Any] | None = None


class ProtocolResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    experiment_id: uuid.UUID | None
    graph: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


async def _get_owned_protocol(db: DbSession, protocol_id: uuid.UUID, user: CurrentUser) -> Any:
    protocol = await get_protocol(db, protocol_id)
    if protocol is None or protocol.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such protocol")
    return protocol


async def _validated_experiment_id(
    experiment_id: uuid.UUID | None, db: DbSession, user: CurrentUser
) -> uuid.UUID | None:
    if experiment_id is None:
        return None
    experiment = await get_experiment(db, experiment_id)
    if experiment is None or experiment.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such experiment")
    return experiment_id


@router.post("", response_model=ProtocolResponse, status_code=201)
async def create_protocol_endpoint(
    body: CreateProtocolRequest, user: CurrentUser, db: DbSession
) -> ProtocolResponse:
    if await get_protocol_by_name(db, body.name, owner_id=user.id) is not None:
        raise HTTPException(status_code=409, detail="A protocol with this name already exists")
    experiment_id = await _validated_experiment_id(body.experiment_id, db, user)
    protocol = await create_protocol(
        db,
        name=body.name,
        owner_id=user.id,
        description=body.description,
        experiment_id=experiment_id,
        graph=body.graph,
    )
    return ProtocolResponse.model_validate(protocol)


@router.get("", response_model=list[ProtocolResponse])
async def list_protocols_endpoint(
    user: CurrentUser, db: DbSession, experiment_id: uuid.UUID | None = None
) -> list[ProtocolResponse]:
    protocols = await list_protocols(db, owner_id=user.id, experiment_id=experiment_id)
    return [ProtocolResponse.model_validate(p) for p in protocols]


@router.get("/{protocol_id}", response_model=ProtocolResponse)
async def get_protocol_endpoint(protocol_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ProtocolResponse:
    protocol = await _get_owned_protocol(db, protocol_id, user)
    return ProtocolResponse.model_validate(protocol)


@router.patch("/{protocol_id}", response_model=ProtocolResponse)
async def update_protocol_endpoint(
    protocol_id: uuid.UUID, body: UpdateProtocolRequest, user: CurrentUser, db: DbSession
) -> ProtocolResponse:
    await _get_owned_protocol(db, protocol_id, user)
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        existing = await get_protocol_by_name(db, fields["name"], owner_id=user.id)
        if existing is not None and existing.id != protocol_id:
            raise HTTPException(status_code=409, detail="A protocol with this name already exists")
    if "experiment_id" in fields:
        fields["experiment_id"] = await _validated_experiment_id(fields["experiment_id"], db, user)
    protocol = await update_protocol(db, protocol_id, fields=fields)
    assert protocol is not None  # existence already checked above
    return ProtocolResponse.model_validate(protocol)


@router.delete("/{protocol_id}", status_code=204)
async def delete_protocol_endpoint(protocol_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await _get_owned_protocol(db, protocol_id, user)
    await delete_protocol(db, protocol_id)
