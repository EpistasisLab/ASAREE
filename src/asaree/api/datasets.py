"""Dataset registration and workspace lineage endpoints.

Every route requires auth (``CurrentUser``) — datasets have a real owner now,
unlike agentic-core's opaque, unenforced ``owner_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from asaree.deps import CurrentUser, DbSession
from asaree.models.dataset_workspace_event import WorkspaceEventType
from asaree.services.dataset_workspace_events import list_events, record_event
from asaree.services.datasets import DatasetValidationError, create_dataset, delete_dataset, get_dataset_by_name

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetResponse(BaseModel):
    id: uuid.UUID
    name: str
    train_path: str
    test_path: str
    train_sha256: str
    test_sha256: str
    target_column: str | None
    # Opaque JSON-encoded string — ASAREE never parses this; a domain MCP server
    # (e.g. ares-sklearn-eda's get_data_dictionary) does, matching ARES's own
    # dictionary_json contract exactly.
    dictionary_json: str | None = None


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
        )
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DatasetResponse(
        id=dataset.id,
        name=dataset.name,
        train_path=dataset.train_path,
        test_path=dataset.test_path,
        train_sha256=dataset.train_sha256,
        test_sha256=dataset.test_sha256,
        target_column=dataset.target_column,
        dictionary_json=dataset.dictionary_json,
    )


@router.get("/by-name/{name}", response_model=DatasetResponse)
async def get_dataset_by_name_endpoint(name: str, db: DbSession, _user: CurrentUser) -> DatasetResponse:
    dataset = await get_dataset_by_name(db, name)
    if dataset is None:
        raise HTTPException(status_code=404, detail="No such dataset")
    return DatasetResponse(
        id=dataset.id,
        name=dataset.name,
        train_path=dataset.train_path,
        test_path=dataset.test_path,
        train_sha256=dataset.train_sha256,
        test_sha256=dataset.test_sha256,
        target_column=dataset.target_column,
        dictionary_json=dataset.dictionary_json,
    )


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset_endpoint(dataset_id: uuid.UUID, db: DbSession, _user: CurrentUser) -> None:
    if not await delete_dataset(db, dataset_id):
        raise HTTPException(status_code=404, detail="No such dataset")


@router.post("/{dataset_id}/workspace-events", response_model=WorkspaceEventResponse, status_code=201)
async def record_workspace_event_endpoint(
    dataset_id: uuid.UUID, body: WorkspaceEventRequest, db: DbSession, _user: CurrentUser
) -> WorkspaceEventResponse:
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
    dataset_id: uuid.UUID, db: DbSession, _user: CurrentUser, workspace_id: str | None = None
) -> list[WorkspaceEventResponse]:
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
