"""User-chosen OKF bundles, each served by its own MCP server process.

An OKF bundle is a directory of markdown concept files (Motoro's
``motoro.mcp_servers.okf`` module docstring links the spec) that agents read
*and write* through MCP tools. ASAREE already registers one deployment-wide
``motoro-okf`` system server over ``AGENTIC_OKF_BUNDLE_DIR``
(:mod:`asaree.services.system_mcp_servers`); this module is the other half --
a researcher pointing the same server code at a bundle of their own.

Why a server per bundle rather than a path argument: the OKF server jails
itself to one directory read from its own environment and refuses to take a
path from a caller, so "which bundle" is a property of the process. Each
registration here is therefore an ordinary per-owner ``mcp_server_configs``
row whose ``command`` runs :mod:`asaree.mcp_servers.okf_bundle` with
``--bundle <path>`` -- persisted in a column, so the worker's own
``hydrate_registry`` spawns it too.

Everything is jailed inside ``settings.okf_bundle_root``. That single knob is
the whole reach of this feature: browsing can't see outside it, and a
registration can't point outside it, so an agent's OKF tools can't either.
The path is the *server's* -- there is no client-machine filesystem access
here, which is why the browse endpoint exists at all: on a single-machine
install the server's filesystem is the user's, and picking from what the
server can see beats typing a path it can't resolve.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from motoro.services import mcp_service

from asaree.config import get_settings
from asaree.services.system_mcp_servers import command_for

# Every bundle server's name starts with this. Names are globally unique in
# ``mcp_server_configs``, so the suffix has to disambiguate both between users
# and between two bundles one user registered -- hence the digest below rather
# than the folder name alone. The prefix is also how list_bundles tells its own
# rows apart from a user's hand-registered servers, so it needs to be
# distinctive enough that nobody types it by accident.
BUNDLE_SERVER_NAME_PREFIX = "okf-bundle-"

# The module the per-bundle command runs -- see asaree.mcp_servers.okf_bundle.
_BUNDLE_SERVER_MODULE = "asaree.mcp_servers.okf_bundle"

# The spec's two reserved root files. Either one present is a strong signal a
# directory really is a bundle rather than just some folder with markdown in
# it -- used only to LABEL entries in the browser, never to reject a
# registration: a bundle an agent hasn't written to yet is legitimately empty,
# and refusing it would make "start a new knowledge base here" impossible.
_BUNDLE_MARKER_FILES = ("index.md", "log.md")

# Directory names never worth showing in the browser -- dotfiles are excluded
# separately (they're hidden by convention), these are the noisy ones that
# aren't.
_UNINTERESTING_DIR_NAMES = frozenset({"node_modules", "__pycache__", "venv", ".venv", "site-packages", "dist", "build"})

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Motoro's stdio command validator rejects any token containing a shell
# metacharacter, AFTER shlex splitting -- so quoting doesn't rescue a path that
# contains one, the character is still there in the resulting argv token. Catch
# it here instead, where the message can say which path and why, rather than
# letting register_server raise a generic MCPCommandError about a "command
# token".
_PATH_META_RE = re.compile(r"[;&|`$<>()\\\n\r]")


class OkfBundleError(ValueError):
    """A bad bundle path -- outside the root, missing, not a directory, or
    un-spawnable. A ``ValueError`` so the API layer can report it as a 422
    alongside core's own registration errors, which are ``ValueError`` too."""


@dataclass(frozen=True)
class DirectoryEntry:
    """One row in the folder browser."""

    name: str
    # Root-relative, "" for the root itself -- what the client passes back to
    # browse into it or register it. Never absolute: an absolute path is the
    # server's own business, and echoing one back only invites a client to
    # invent a different one.
    path: str
    is_bundle: bool


def bundle_root() -> Path:
    """The one directory this module may ever look inside."""
    return Path(get_settings().okf_bundle_root).expanduser().resolve()


def resolve_within_root(relative: str | None) -> Path:
    """Resolve a root-relative path, refusing anything that escapes the root.

    ``resolve()`` before the containment check, not after, so a symlink
    pointing out of the root is caught too -- the check has to run against the
    path the OS will actually open.
    """
    root = bundle_root()
    candidate = (root / (relative or "")).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover -- e.g. a symlink loop
        raise OkfBundleError(f"Could not resolve {relative!r}: {exc}") from exc
    if resolved != root and root not in resolved.parents:
        raise OkfBundleError(f"{relative!r} is outside the bundle root ({root}).")
    return resolved


def looks_like_bundle(path: Path) -> bool:
    """Whether *path* has the shape of an OKF bundle -- advisory only."""
    return any((path / marker).is_file() for marker in _BUNDLE_MARKER_FILES)


def list_directories(relative: str | None) -> tuple[Path, list[DirectoryEntry]]:
    """``(resolved directory, its child directories)`` for the folder browser.

    Directories only: a bundle is a directory, and listing the markdown files
    inside one would be a file browser -- a different feature, and a much
    larger read surface, for no gain here.
    """
    directory = resolve_within_root(relative)
    if not directory.is_dir():
        raise OkfBundleError(f"{relative or '.'!r} is not a directory.")
    root = bundle_root()
    entries: list[DirectoryEntry] = []
    try:
        children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise OkfBundleError(f"Not allowed to read {directory}: {exc}") from exc
    for child in children:
        if not child.is_dir() or child.name.startswith(".") or child.name in _UNINTERESTING_DIR_NAMES:
            continue
        entries.append(
            DirectoryEntry(
                name=child.name,
                path=str(child.relative_to(root)),
                is_bundle=looks_like_bundle(child),
            )
        )
    return directory, entries


def relative_to_root(path: Path) -> str:
    """*path* expressed the way the browser/API talks about it (root-relative,
    "" for the root itself)."""
    root = bundle_root()
    return "" if path == root else str(path.relative_to(root))


def server_name_for(owner_id: uuid.UUID, path: Path) -> str:
    """A unique, readable server name for one owner's bundle.

    The folder name makes it recognisable in the MCP server list; the digest
    (over owner + absolute path) makes it unique, since ``name`` is unique
    across the whole deployment and two users may well each have a
    ``~/okf`` -- or one user two bundles both called ``knowledge``.
    """
    slug = _SLUG_RE.sub("-", path.name.lower()).strip("-") or "bundle"
    digest = hashlib.sha256(f"{owner_id}:{path}".encode()).hexdigest()[:8]
    return f"{BUNDLE_SERVER_NAME_PREFIX}{slug}-{digest}"


def command_for_bundle(path: Path) -> str:
    """The stored stdio ``command`` that serves *path*.

    ``shlex.quote`` handles spaces; a path carrying a shell metacharacter is
    rejected outright by :func:`validate_bundle_path` before reaching here,
    since quoting cannot save it (Motoro validates the post-split tokens).
    """
    return f"{command_for(_BUNDLE_SERVER_MODULE)} --bundle {shlex.quote(str(path))}"


def bundle_path_from_command(command: str | None) -> str | None:
    """The ``--bundle`` argument back out of a stored command, for display.

    The path lives in the command because there's no column for it (see the
    module docstring), so reading it back is a parse rather than a field
    lookup. Anything unparseable yields ``None`` rather than raising: a row
    whose command was hand-edited should still list, just without a path.
    """
    if not command:
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if "--bundle" not in parts:
        return None
    index = parts.index("--bundle")
    return parts[index + 1] if index + 1 < len(parts) else None


def validate_bundle_path(relative: str | None) -> Path:
    """Resolve and check a path the user picked, or raise :class:`OkfBundleError`.

    Writability is checked, not just existence: OKF is a read-*write* format
    (agents create and update concepts), so a bundle the server can only read
    would fail later, mid-run, as a tool error -- much further from the
    decision that caused it.
    """
    path = resolve_within_root(relative)
    if not path.exists():
        raise OkfBundleError(f"{path} does not exist.")
    if not path.is_dir():
        raise OkfBundleError(f"{path} is not a directory -- an OKF bundle is a folder of .md files.")
    if not os.access(path, os.W_OK | os.X_OK):
        raise OkfBundleError(f"{path} is not writable by the server -- an agent could read it but never update it.")
    ensure_command_safe(path)
    return path


def ensure_command_safe(path: Path) -> None:
    """Raise unless *path* can survive being embedded in a stored MCP command.

    Shared with :mod:`asaree.services.okf_documents`, which builds the same
    ``--bundle <path>`` command for a directory ASAREE created itself -- the
    rule belongs to the command shape, not to who chose the path.
    """
    meta = _PATH_META_RE.search(str(path))
    if meta:
        raise OkfBundleError(
            f"{path} contains the character {meta.group()!r}, which isn't allowed in an MCP server command. "
            "Rename or move the folder, or symlink it somewhere without it."
        )


def is_bundle_server(config: Any) -> bool:
    return bool(config.name.startswith(BUNDLE_SERVER_NAME_PREFIX))


async def list_bundles(owner_id: uuid.UUID) -> list[Any]:
    """Every bundle server this user registered.

    Owner-scoped with no system fallback, unlike ``GET /mcp-servers``: the
    deployment's own ``motoro-okf`` is a *system* server over
    ``AGENTIC_OKF_BUNDLE_DIR`` and isn't one of these rows, so there's nothing
    shared to include.
    """
    servers = await mcp_service.list_servers(owner_id=owner_id)
    return [s for s in servers if is_bundle_server(s) and s.owner_id == owner_id]


async def register_bundle(*, owner_id: uuid.UUID, relative_path: str | None) -> Any:
    """Spawn and persist an OKF server for one bundle directory.

    Re-registering a path this owner already has returns the existing row
    untouched rather than failing: the name is derived from (owner, path), so
    a second attempt is the same server, and a 409 would be a confusing answer
    to "add this folder" when the folder is already there.
    """
    path = validate_bundle_path(relative_path)
    name = server_name_for(owner_id, path)
    existing = await mcp_service.get_server_by_name(name)
    if existing is not None:
        return existing
    return await mcp_service.register_server(
        name=name,
        transport="stdio",
        command=command_for_bundle(path),
        owner_id=owner_id,
    )


def tool_names_for(config: Any) -> list[str]:
    """The bundle server's discovered tool names, bare (not namespaced).

    Read off the registration's own ``capabilities``, which
    ``register_server`` fills in from the live connection -- so a bundle that
    failed to spawn yields ``[]`` and the canvas node it backs shows up as
    unconfigured rather than silently contributing nothing at run time.
    """
    tools = ((config.capabilities or {}).get("tools")) or []
    return [t["name"] for t in tools if isinstance(t, dict) and t.get("name")]
