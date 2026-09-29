from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class RegisteredDataset(BaseModel):
    id: uuid.UUID
    name: str
    raw_path: str | None = None
    raw_sha256: str | None = None
    train_path: str | None = None
    test_path: str | None = None
    train_sha256: str | None = None
    test_sha256: str | None = None
    split_method: str | None = None
    split_group_column: str | None = None
    split_test_size: float | None = None
    split_seed: int | None = None
    target_column: str | None
    description: str | None = None
    dictionary_json: str | None = None
    created_at: datetime | None = None


class WorkspaceEvent(BaseModel):
    id: uuid.UUID
    workspace_id: str
    stage: str
    event_type: str
    sha256_train: str | None
    sha256_test: str | None
    created_at: datetime
