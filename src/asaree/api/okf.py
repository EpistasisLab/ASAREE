"""OKF bundles — browse the server's disk, register one as an MCP server.

Thin over :mod:`asaree.services.okf_bundles`, which holds every rule about
which paths are reachable; this layer only resolves ``owner_id`` and maps that
module's :class:`OkfBundleError` to a 422, the same way ``api/mcp_servers.py``
maps core's own ``ValueError``s.

A registered bundle IS an ``mcp_server_configs`` row, so it's also visible and
deletable through ``/mcp-servers``. This router exists anyway because the two
things a user does with a bundle — find the folder, and turn it into a server
without composing a ``uv run ...`` command by hand — aren't expressible there.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from motoro.services import mcp_service
from pydantic import BaseModel

from asaree.deps import CurrentUser
from asaree.services import okf_bundles

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
    status: str
    error_message: str | None
    # Bare tool names. The canvas namespaces them "{server}.{tool}" when it
    # builds a run's allow-list — see services/protocol_execution.py.
    tool_names: list[str]
    created_at: datetime


class RegisterBundleRequest(BaseModel):
    # Root-relative, as returned by /okf/browse. "" is the root itself, which
    # is a legitimate bundle location on a deployment whose root IS the mount.
    path: str


def _to_response(config: Any) -> BundleResponse:
    return BundleResponse(
        id=config.id,
        name=config.name,
        path=okf_bundles.bundle_path_from_command(config.command),
        status=config.status.value,
        error_message=config.error_message,
        tool_names=okf_bundles.tool_names_for(config),
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


@router.post("/bundles/{bundle_id}/refresh", response_model=BundleResponse)
async def refresh_bundle_endpoint(bundle_id: uuid.UUID, user: CurrentUser) -> BundleResponse:
    """Re-discover the bundle server's tools (and clear a stale error)."""
    config = await _owned_bundle(bundle_id, user)
    refreshed = await mcp_service.refresh_server(config.id)
    return _to_response(refreshed or config)


@router.delete("/bundles/{bundle_id}", status_code=204)
async def delete_bundle_endpoint(bundle_id: uuid.UUID, user: CurrentUser) -> None:
    """Forget the registration. The directory itself is never touched."""
    config = await _owned_bundle(bundle_id, user)
    await mcp_service.delete_server(config.id)


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
