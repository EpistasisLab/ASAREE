"""Reusable personas, imported as plain text without required frontmatter."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from asaree.deps import CurrentUser, DbSession
from asaree.services import personas

router = APIRouter(prefix="/personas", tags=["personas"])


class PersonaCreate(BaseModel):
    prompt: str = Field(max_length=personas.MAX_PROMPT_BYTES)
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    traits: dict[str, Any] = Field(default_factory=dict)


class PersonaUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=255)
    prompt: str | None = Field(default=None, max_length=personas.MAX_PROMPT_BYTES)
    description: str | None = None
    traits: dict[str, Any] | None = None

    @model_validator(mode="after")
    def non_nullable_fields(self) -> PersonaUpdate:
        for key in ("prompt", "traits"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


class PersonaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    prompt: str
    source_filename: str | None
    traits: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def prompt_sha256(self) -> str:
        return personas.prompt_sha256(self.prompt)


@router.get("", response_model=list[PersonaResponse])
async def list_personas(db: DbSession, user: CurrentUser) -> Any:
    return await personas.list_personas(db, owner_id=user.id)


@router.post("", response_model=PersonaResponse, status_code=201)
async def create_persona(body: PersonaCreate, db: DbSession, user: CurrentUser) -> Any:
    try:
        return await personas.create_persona(db, owner_id=user.id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/upload", response_model=PersonaResponse, status_code=201)
async def upload_persona(
    db: DbSession, user: CurrentUser, file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form(max_length=255)] = None,
) -> Any:
    try:
        filename, prompt = personas.decode_persona_upload(
            file.filename, await file.read(personas.MAX_PROMPT_BYTES + 1)
        )
        return await personas.create_persona(db, owner_id=user.id, prompt=prompt, name=name, source_filename=filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{persona_id}", response_model=PersonaResponse)
async def get_persona(persona_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Any:
    persona = await personas.get_persona(db, persona_id, owner_id=user.id)
    if persona is None:
        raise HTTPException(status_code=404, detail="No such persona")
    return persona


@router.patch("/{persona_id}", response_model=PersonaResponse)
async def update_persona(persona_id: uuid.UUID, body: PersonaUpdate, db: DbSession, user: CurrentUser) -> Any:
    try:
        persona = await personas.update_persona(
            db, persona_id, owner_id=user.id, expected_revision=body.expected_revision,
            fields=body.model_dump(exclude_unset=True, exclude={"expected_revision"}),
        )
    except personas.PersonaRevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if persona is None:
        raise HTTPException(status_code=404, detail="No such persona")
    return persona


@router.delete("/{persona_id}", status_code=204)
async def delete_persona(persona_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    if not await personas.delete_persona(db, persona_id, owner_id=user.id):
        raise HTTPException(status_code=404, detail="No such persona")
