"""Dataset registration and workspace lineage endpoints.

Every route requires auth (``CurrentUser``) — datasets have a real owner now,
unlike agentic-core's opaque, unenforced ``owner_id``, and every route below
enforces it: a dataset (or its workspace events) not owned by the caller is
a 404, the same convention ``experiments.py``'s ``_get_owned_experiment``
already uses, not just "authenticated is enough."
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from asaree.deps import CurrentUser, DbSession
from asaree.models.dataset_workspace_event import WorkspaceEventType
from asaree.services.dataset_workspace_events import list_events, record_event
from asaree.services.datasets import (
    DatasetValidationError,
    create_dataset,
    delete_dataset,
    get_dataset,
    get_dataset_by_name,
    list_datasets,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])


async def _get_owned_dataset(db: DbSession, dataset_id: uuid.UUID, user: CurrentUser) -> Any:
    dataset = await get_dataset(db, dataset_id)
    if dataset is None or dataset.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such dataset")
    return dataset


async def _get_owned_dataset_by_name(db: DbSession, name: str, user: CurrentUser) -> Any:
    dataset = await get_dataset_by_name(db, name)
    if dataset is None or dataset.owner_id != user.id:
        raise HTTPException(status_code=404, detail="No such dataset")
    return dataset


class DatasetResponse(BaseModel):
    id: uuid.UUID
    name: str
    train_path: str
    test_path: str
    train_sha256: str
    test_sha256: str
    target_column: str | None
    description: str | None = None
    # Opaque JSON-encoded string — ASAREE never parses this; a domain MCP server
    # (e.g. ares-sklearn-eda's get_data_dictionary) does, matching ARES's own
    # dictionary_json contract exactly.
    dictionary_json: str | None = None
    created_at: datetime | None = None


def _dataset_response(d: Any) -> DatasetResponse:
    return DatasetResponse(
        id=d.id,
        name=d.name,
        train_path=d.train_path,
        test_path=d.test_path,
        train_sha256=d.train_sha256,
        test_sha256=d.test_sha256,
        target_column=d.target_column,
        description=d.description,
        dictionary_json=d.dictionary_json,
        created_at=d.created_at,
    )


class WorkspaceEventRequest(BaseModel):
    workspace_id: str
    stage: str
    event_type: WorkspaceEventType
    sha256_train: str | None = None
    sha256_test: str | None = None


class WorkspaceEventResponse(BaseModel):
    id: uuid.UUID
    workspace_id: str
    stage: str
    event_type: WorkspaceEventType
    sha256_train: str | None
    sha256_test: str | None
    created_at: datetime


@router.post("", response_model=DatasetResponse, status_code=201)
async def create_dataset_endpoint(
    db: DbSession,
    user: CurrentUser,
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    target_column: Annotated[str | None, Form()] = None,
    group_column: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    dictionary_json: Annotated[str | None, Form()] = None,
    test_size: Annotated[float, Form()] = 0.2,
    seed: Annotated[int, Form()] = 0,
) -> DatasetResponse:
    if await get_dataset_by_name(db, name) is not None:
        raise HTTPException(status_code=409, detail="A dataset with this name already exists")
    try:
        dataset = await create_dataset(
            db,
            name=name,
            csv_bytes=await file.read(),
            owner_id=user.id,
            target_column=target_column,
            group_column=group_column,
            description=description,
            dictionary_json=dictionary_json,
            test_size=test_size,
            seed=seed,
        )
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _dataset_response(dataset)


@router.get("", response_model=list[DatasetResponse])
async def list_datasets_endpoint(user: CurrentUser, db: DbSession) -> list[DatasetResponse]:
    datasets = await list_datasets(db, owner_id=user.id)
    return [_dataset_response(d) for d in datasets]


@router.get("/by-name/{name}", response_model=DatasetResponse)
async def get_dataset_by_name_endpoint(name: str, db: DbSession, user: CurrentUser) -> DatasetResponse:
    dataset = await _get_owned_dataset_by_name(db, name, user)
    return _dataset_response(dataset)


@router.get("/{dataset_id}", response_model=DatasetResponse)
async def get_dataset_endpoint(dataset_id: uuid.UUID, db: DbSession, user: CurrentUser) -> DatasetResponse:
    dataset = await _get_owned_dataset(db, dataset_id, user)
    return _dataset_response(dataset)


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset_endpoint(dataset_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    await _get_owned_dataset(db, dataset_id, user)
    await delete_dataset(db, dataset_id)


@router.post("/{dataset_id}/workspace-events", response_model=WorkspaceEventResponse, status_code=201)
async def record_workspace_event_endpoint(
    dataset_id: uuid.UUID, body: WorkspaceEventRequest, db: DbSession, user: CurrentUser
) -> WorkspaceEventResponse:
    await _get_owned_dataset(db, dataset_id, user)
    event = await record_event(
        db,
        dataset_id=dataset_id,
        workspace_id=body.workspace_id,
        stage=body.stage,
        event_type=body.event_type,
        sha256_train=body.sha256_train,
        sha256_test=body.sha256_test,
    )
    return WorkspaceEventResponse(
        id=event.id,
        workspace_id=event.workspace_id,
        stage=event.stage,
        event_type=event.event_type,
        sha256_train=event.sha256_train,
        sha256_test=event.sha256_test,
        created_at=event.created_at,
    )


@router.get("/{dataset_id}/workspace-events", response_model=list[WorkspaceEventResponse])
async def list_workspace_events_endpoint(
    dataset_id: uuid.UUID, db: DbSession, user: CurrentUser, workspace_id: str | None = None
) -> list[WorkspaceEventResponse]:
    await _get_owned_dataset(db, dataset_id, user)
    events = await list_events(db, dataset_id=dataset_id, workspace_id=workspace_id)
    return [
        WorkspaceEventResponse(
            id=e.id,
            workspace_id=e.workspace_id,
            stage=e.stage,
            event_type=e.event_type,
            sha256_train=e.sha256_train,
            sha256_test=e.sha256_test,
            created_at=e.created_at,
        )
        for e in events
    ]
