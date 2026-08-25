"""OKF knowledge — two ways to give an agent Markdown concepts.

``/okf/browse`` + ``/okf/bundles`` are the *bundle* half: the knowledge is
already a directory on the server's disk, and the user picks it. Thin over
:mod:`asaree.services.okf_bundles`, which holds every rule about which paths
are reachable; this layer only resolves ``owner_id`` and maps that module's
:class:`OkfBundleError` to a 422, the same way ``api/mcp_servers.py`` maps
core's own ``ValueError``s.

``/okf/documents`` is the *document* half: the user has one concept ``.md``
file on their own machine and uploads it, exactly the way an Agent Skill is
registered. :mod:`asaree.services.okf_documents` stores it as a one-concept
bundle and owns the storage; see that module for why an upload still becomes
its own directory and its own server process.

Either way the registration IS an ``mcp_server_configs`` row, so it's also
visible and deletable through ``/mcp-servers``. This router exists anyway
because what a user actually does — find the folder or hand over the file, and
turn it into a server without composing a ``uv run ...`` command by hand —
isn't expressible there. The two halves never cross: a document id 404s on
every ``/bundles`` route and vice versa (see ``_owned_bundle``/
``_owned_document``), since bundle semantics applied to ASAREE-owned storage
(or document semantics — including a real ``rmtree`` — applied to a user's own
folder) would be a surprise in both directions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from motoro.services import mcp_service
from pydantic import BaseModel

from asaree.deps import CurrentUser
from asaree.services import okf_bundles, okf_documents

router = APIRouter(prefix="/okf", tags=["okf"])


class DirectoryEntryResponse(BaseModel):
    name: str
    path: str
    is_bundle: bool


class BrowseResponse(BaseModel):
    # Where the listing came from, root-relative ("" at the root) — echoed
    # back because the client may have sent ".." segments that resolved
    # somewhere else, and it needs the canonical form to build child paths.
    path: str
    # Absolute, display-only: the whole point of this screen is reassuring the
    # user that the server sees the same disk they do, and a bare "okf/spine"
    # doesn't do that.
    absolute_path: str
    parent: str | None
    entries: list[DirectoryEntryResponse]


class BundleResponse(BaseModel):
    id: uuid.UUID
    name: str
    path: str | None
    # Whether the files are a copy ASAREE stored (an upload) rather than a
    # folder on the server the registration merely points at. Drives what
    # deleting means, so the UI has to be able to say which one this is.
    uploaded: bool
    status: str
    error_message: str | None
    # Bare tool names. The canvas namespaces them "{server}.{tool}" when it
    # builds a run's allow-list — see services/protocol_execution.py.
    tool_names: list[str]
    created_at: datetime


class DocumentResponse(BaseModel):
    id: uuid.UUID
    # The generated okf-doc-* server name — what a run's tool allow-list is
    # namespaced against, exactly as for a bundle.
    name: str
    # Read out of the stored file's frontmatter on every request, never
    # cached: the agent rewrites this file during a run, so a stored copy
    # would show the document as it was uploaded rather than as it now is.
    title: str | None
    description: str | None
    concept_type: str | None
    tags: list[str]
    # Absolute path to the stored .md, display-only. Answers "where did my
    # upload actually go" on a local install, and is the only way to reach the
    # file outside ASAREE.
    path: str | None
    status: str
    error_message: str | None
    tool_names: list[str]
    created_at: datetime


class RegisterBundleRequest(BaseModel):
    # Root-relative, as returned by /okf/browse. "" is the root itself, which
    # is a legitimate bundle location on a deployment whose root IS the mount.
    path: str


def _to_response(config: Any) -> BundleResponse:
    path = okf_bundles.bundle_path_from_command(config.command)
    return BundleResponse(
        id=config.id,
        name=config.name,
        path=path,
        uploaded=okf_bundles.is_uploaded_path(Path(path) if path else None),
        status=config.status.value,
        error_message=config.error_message,
        tool_names=okf_bundles.tool_names_for(config),
        created_at=config.created_at,
    )


def _to_document_response(config: Any) -> DocumentResponse:
    meta = okf_documents.meta_for(config)
    concept_file = okf_documents.concept_file_for(config)
    return DocumentResponse(
        id=config.id,
        name=config.name,
        title=meta.title,
        description=meta.description,
        concept_type=meta.concept_type,
        tags=meta.tags,
        path=str(concept_file) if concept_file else None,
        status=config.status.value,
        error_message=config.error_message,
        tool_names=okf_documents.tool_names_for(config),
        created_at=config.created_at,
    )


@router.get("/browse", response_model=BrowseResponse)
async def browse_endpoint(
    user: CurrentUser,
    path: str = Query("", description="Directory to list, relative to the server's OKF bundle root."),
) -> BrowseResponse:
    """List the sub-directories of one directory inside the bundle root.

    Authenticated but not per-user: the root is a deployment-wide setting, not
    a per-account home, so every signed-in user browses the same tree. That is
    the intended shape for the case this exists for — one researcher running
    ASAREE on their own machine — and the reason the root defaults to
    something narrow and is meant to be narrowed further on any shared
    deployment (see ``AsareeSettings.okf_bundle_root``).
    """
    try:
        directory, entries = okf_bundles.list_directories(path)
    except okf_bundles.OkfBundleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    here = okf_bundles.relative_to_root(directory)
    return BrowseResponse(
        path=here,
        absolute_path=str(directory),
        # None at the root, so the client can't offer an "up" that would 422.
        parent=None if here == "" else str(directory.parent.relative_to(okf_bundles.bundle_root())),
        entries=[DirectoryEntryResponse(name=e.name, path=e.path, is_bundle=e.is_bundle) for e in entries],
    )


@router.get("/bundles", response_model=list[BundleResponse])
async def list_bundles_endpoint(user: CurrentUser) -> list[BundleResponse]:
    return [_to_response(c) for c in await okf_bundles.list_bundles(user.id)]


@router.post("/bundles", response_model=BundleResponse, status_code=201)
async def register_bundle_endpoint(body: RegisterBundleRequest, user: CurrentUser) -> BundleResponse:
    """Point an OKF MCP server at a directory and persist the registration.

    Spawns the server to discover its tools, so a bad path fails here rather
    than mid-run. Registering a path twice is idempotent — see
    ``okf_bundles.register_bundle``.
    """
    try:
        config = await okf_bundles.register_bundle(owner_id=user.id, relative_path=body.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(config)


@router.post("/bundles/upload", response_model=BundleResponse, status_code=201)
async def upload_bundle_endpoint(user: CurrentUser, files: Annotated[list[UploadFile], File()]) -> BundleResponse:
    """Store a folder of concepts the user picked in their browser.

    What the GUI uses, because a browser never reveals a real path: a
    ``<input webkitdirectory>`` hands over the folder's files and their
    relative paths, and nothing else. So this copies them — the agent then
    reads and writes ASAREE's copy, and the user's own folder stops being
    involved the moment the upload finishes.

    Each file's ``filename`` carries its ``webkitRelativePath``; the leading
    folder segment is stripped server-side rather than taken from a separate
    field, so the two can't disagree (see ``normalise_upload_paths``).
    """
    payload: list[tuple[str, str]] = []
    for upload in files:
        try:
            text = (await upload.read()).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422, detail=f"{upload.filename or 'A file'} isn't UTF-8 text, so it isn't a concept."
            ) from exc
        payload.append((upload.filename or "", text))
    try:
        config = await okf_bundles.register_uploaded_bundle(owner_id=user.id, files=payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(config)


@router.post("/bundles/{bundle_id}/refresh", response_model=BundleResponse)
async def refresh_bundle_endpoint(bundle_id: uuid.UUID, user: CurrentUser) -> BundleResponse:
    """Re-discover the bundle server's tools (and clear a stale error)."""
    config = await _owned_bundle(bundle_id, user)
    refreshed = await mcp_service.refresh_server(config.id)
    return _to_response(refreshed or config)


@router.delete("/bundles/{bundle_id}", status_code=204)
async def delete_bundle_endpoint(bundle_id: uuid.UUID, user: CurrentUser) -> None:
    """Forget the registration, and delete the files if they were uploaded.

    Destructive for an uploaded bundle (ASAREE's copy is the only one) and not
    for a pointed-at one (the user's folder predates the registration) — see
    ``okf_bundles.delete_bundle``. The ``uploaded`` flag on every response is
    there so the UI can say which of those is about to happen.
    """
    config = await _owned_bundle(bundle_id, user)
    await okf_bundles.delete_bundle(config)


@router.get("/bundles/{bundle_id}/concepts")
async def list_concepts_endpoint(bundle_id: uuid.UUID, user: CurrentUser) -> dict[str, Any]:
    """A peek at what's actually in the bundle, for the node inspector.

    Calls the bundle server's own ``list_concepts`` rather than reading the
    directory here: the server is the thing that defines what counts as a
    concept, and going through it means the preview shows exactly what the
    agent will see. Returned as the tool's raw text — the OKF tools' output
    shape is Motoro's to change, and parsing it here would just add a second
    place to keep in sync.
    """
    config = await _owned_bundle(bundle_id, user)
    try:
        outcome = await mcp_service.call_server_tool(config.id, "list_concepts", {})
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail="No such bundle")
    is_error, content = outcome
    return {"is_error": is_error, "content": content}


@router.get("/documents", response_model=list[DocumentResponse])
async def list_documents_endpoint(user: CurrentUser) -> list[DocumentResponse]:
    return [_to_document_response(c) for c in await okf_documents.list_documents(user.id)]


@router.post("/documents", response_model=DocumentResponse, status_code=201)
async def upload_document_endpoint(user: CurrentUser, file: Annotated[UploadFile, File()]) -> DocumentResponse:
    """Store one uploaded concept ``.md`` and serve it over MCP.

    No ``title``/``description`` form overrides, unlike ``POST
    /skills/upload``: a skill's frontmatter is metadata ABOUT a document
    ASAREE stores in columns, so overriding it is editing a field. An OKF
    concept's frontmatter is part of the file the agent reads and rewrites,
    so an override would mean silently editing the user's document on the way
    in.
    """
    try:
        text = (await file.read()).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="An OKF concept file must be UTF-8 text") from exc
    try:
        config = await okf_documents.register_document(owner_id=user.id, text=text, filename=file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_document_response(config)


@router.post("/documents/{document_id}/refresh", response_model=DocumentResponse)
async def refresh_document_endpoint(document_id: uuid.UUID, user: CurrentUser) -> DocumentResponse:
    """Re-discover the document server's tools (and clear a stale error)."""
    config = await _owned_document(document_id, user)
    refreshed = await mcp_service.refresh_server(config.id)
    return _to_document_response(refreshed or config)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document_endpoint(document_id: uuid.UUID, user: CurrentUser) -> None:
    """Forget the registration and delete the stored file.

    Genuinely destructive, unlike deleting a bundle: this file only ever
    existed inside ASAREE's own storage, so there's no original left on disk
    once it's gone.
    """
    config = await _owned_document(document_id, user)
    await okf_documents.delete_document(config)


@router.get("/documents/{document_id}/markdown")
async def read_document_endpoint(document_id: uuid.UUID, user: CurrentUser) -> dict[str, Any]:
    """The stored concept's current text, straight off disk.

    Read rather than reconstructed, and not cached anywhere, because the agent
    rewrites this file during a run -- the point of showing it in the node
    inspector is to see what the knowledge has BECOME, not what was uploaded.
    """
    config = await _owned_document(document_id, user)
    markdown = okf_documents.read_document(config)
    if markdown is None:
        raise HTTPException(status_code=404, detail="This document's file is missing from the server's storage.")
    return {"markdown": markdown}


async def _owned_bundle(bundle_id: uuid.UUID, user: Any) -> Any:
    """The caller's bundle row, or a 404.

    Rejects a server that isn't a bundle even when the caller owns it: a
    hand-registered MCP server reached through this router would get bundle
    semantics (a parsed ``--bundle`` path, a ``list_concepts`` call) it never
    agreed to. ``/mcp-servers`` is where those are managed.
    """
    config = await mcp_service.get_server(bundle_id)
    if config is None or config.owner_id != user.id or not okf_bundles.is_bundle_server(config):
        raise HTTPException(status_code=404, detail="No such bundle")
    return config


async def _owned_document(document_id: uuid.UUID, user: Any) -> Any:
    """The caller's document row, or a 404.

    The mirror of ``_owned_bundle``, and strict for a sharper reason: this
    router's delete removes the served directory outright, so letting a
    folder-picked bundle (or any hand-registered server) through here would
    ``rmtree`` a directory the user owns and never agreed to hand over.
    """
    config = await mcp_service.get_server(document_id)
    if config is None or config.owner_id != user.id or not okf_documents.is_document_server(config):
        raise HTTPException(status_code=404, detail="No such document")
    return config
