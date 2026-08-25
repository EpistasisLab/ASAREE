"""Single-concept OKF documents, UPLOADED rather than pointed at.

:mod:`asaree.services.okf_bundles` covers the case where the knowledge already
exists on the server's disk: the user browses to a directory and ASAREE points
an OKF MCP server at it. This module covers the other case -- the user has one
concept file on their own machine and wants an agent to use it. They upload
it, exactly the way an Agent Skill is registered (``POST /skills/upload``), and
ASAREE stores it.

Storage is a directory, not a database row, because the OKF server reads
markdown off a filesystem and nothing else -- so an uploaded document becomes
a bundle of exactly one concept:

    <okf_document_dir>/<owner id>/<slug>/<slug>.md

One directory per document, one MCP server per directory, for the same reason
bundles work that way (see that module's docstring): the OKF server jails
itself to a single directory read from its own environment, so a per-document
server is the only way an agent wired to THIS document can't also read and
rewrite the user's other documents. A shared per-user folder would be one
server over all of them, which is a scope leak, not an optimisation.

Consequences worth being explicit about:

* The document stays **read-write**. The agent gets the same ``get_concept`` /
  ``update_concept`` / ``mark_verified`` tools a folder-picked bundle exposes,
  and its edits land in the stored file -- an uploaded document is a living
  concept, not a frozen attachment. ``GET /okf/documents/{id}/markdown`` reads
  the file back off disk for exactly that reason.
* Deleting one **does** remove the directory, unlike deleting a bundle
  registration. ASAREE created this storage; there's no user-owned folder left
  behind to preserve.
* ``okf_document_dir`` is ASAREE-owned storage and deliberately outside
  ``okf_bundle_root`` -- nothing here resolves a caller-supplied path at all,
  so the containment machinery bundles need has no job to do.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from motoro.services import mcp_service

from asaree.config import get_settings
from asaree.services.okf_bundles import bundle_path_from_command, command_for_bundle, ensure_command_safe

# Distinct from BUNDLE_SERVER_NAME_PREFIX so the two registries stay separable
# with a string check: ``/okf/bundles`` must not list (or delete, or refresh) a
# document, and vice versa, even though both are ordinary mcp_server_configs
# rows running the same entry point.
DOCUMENT_SERVER_NAME_PREFIX = "okf-doc-"

# OKF's reserved root stems (motoro.mcp_servers.okf._RESERVED_ROOT_STEMS) --
# a file called index.md or log.md at a bundle root is never a concept, so a
# document slugging down to one of these would be invisible to every tool the
# agent has. Suffixed rather than rejected: the user named their concept, not
# their storage layout.
_RESERVED_STEMS = frozenset({"index", "log"})

# Enough to hold any hand-written concept many times over. A cap at all
# because the whole file is parsed here and re-read on every list call.
MAX_DOCUMENT_BYTES = 1_000_000

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n?(.*)\Z", re.S)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# How many "-2", "-3" ... suffixes to try before giving up on de-duplicating a
# slug. A user with 200 documents whose titles all slug identically has a
# naming problem this module can't fix.
_MAX_SLUG_ATTEMPTS = 200


class OkfDocumentError(ValueError):
    """A document that can't be stored -- not markdown, no frontmatter, too
    big, or un-spawnable. A ``ValueError`` so the API layer reports it as a
    422 alongside ``OkfBundleError``."""


@dataclass(frozen=True)
class DocumentMeta:
    """What the frontmatter of a stored concept says about itself.

    Read off disk on every list, never cached in the registration row: the
    agent rewrites this file during a run, so a cached title would be a
    snapshot of upload time rather than of what the document now is.
    """

    title: str | None
    description: str | None
    concept_type: str | None
    tags: list[str]


def document_root() -> Path:
    return Path(get_settings().okf_document_dir).expanduser().resolve()


def owner_root(owner_id: uuid.UUID) -> Path:
    return document_root() / str(owner_id)


def parse_document(text: str) -> tuple[dict[str, Any], str]:
    """``(frontmatter, body)`` for an uploaded concept, or raise.

    The same shape check Motoro's own ``_parse_concept_file`` applies when the
    agent later reads this file -- run here so a malformed document fails at
    upload, next to the file picker that caused it, rather than mid-run as an
    opaque tool error.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise OkfDocumentError(
            "An OKF concept starts with a YAML frontmatter block delimited by --- lines. This file has none."
        )
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise OkfDocumentError(f"The frontmatter isn't valid YAML: {exc}") from exc
    if frontmatter is None:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        raise OkfDocumentError("The frontmatter must be a mapping of fields, not a list or a bare value.")
    # `title` specifically, and nothing else: it's the one field the agent
    # sees for every concept without opening it (list_concepts/search_concepts
    # project it), so a document without one is anonymous in the very listing
    # that's supposed to make it findable. Everything else the spec defines --
    # type, description, tags, generated -- is genuinely optional.
    title = frontmatter.get("title")
    if not isinstance(title, str) or not title.strip():
        raise OkfDocumentError("The frontmatter needs a non-empty `title` -- it's how the agent finds this concept.")
    return frontmatter, match.group(2)


def meta_from_frontmatter(frontmatter: dict[str, Any]) -> DocumentMeta:
    tags = frontmatter.get("tags") or []
    return DocumentMeta(
        title=frontmatter.get("title") if isinstance(frontmatter.get("title"), str) else None,
        description=frontmatter.get("description") if isinstance(frontmatter.get("description"), str) else None,
        concept_type=frontmatter.get("type") if isinstance(frontmatter.get("type"), str) else None,
        tags=sorted(str(t) for t in tags) if isinstance(tags, list) else [],
    )


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    # Trimmed because the slug becomes both a directory name and a file name,
    # and a 300-character title would otherwise push the whole path near the
    # filesystem's own limit.
    slug = slug[:64].strip("-") or "concept"
    return f"{slug}-concept" if slug in _RESERVED_STEMS else slug


def server_name_for(owner_id: uuid.UUID, path: Path) -> str:
    """A unique, readable server name for one stored document.

    Same construction as ``okf_bundles.server_name_for`` -- readable stem plus
    an (owner, path) digest -- because ``mcp_server_configs.name`` is unique
    across the whole deployment, and two users can each have a "protocol"
    concept.
    """
    digest = hashlib.sha256(f"{owner_id}:{path}".encode()).hexdigest()[:8]
    return f"{DOCUMENT_SERVER_NAME_PREFIX}{path.name}-{digest}"


def is_document_server(config: Any) -> bool:
    return bool(config.name.startswith(DOCUMENT_SERVER_NAME_PREFIX))


def document_dir_for(config: Any) -> Path | None:
    """The stored directory back out of the registration's command.

    Parsed rather than looked up for the same reason bundles do it: there's no
    column for a path (see ``okf_bundles``' module docstring). ``None`` for a
    row whose command was hand-edited into something unparseable -- it still
    lists, just without a path.
    """
    raw = bundle_path_from_command(config.command)
    return Path(raw) if raw else None


def concept_file_for(config: Any) -> Path | None:
    """The one ``.md`` concept inside a document's directory.

    Found by scanning rather than by rebuilding the slug, so a file the agent
    renamed (or a directory restored from a backup) still resolves. Reserved
    root files are skipped -- ``log.md`` appears the moment an agent first
    writes, and it is not the document.
    """
    directory = document_dir_for(config)
    if directory is None or not directory.is_dir():
        return None
    for child in sorted(directory.glob("*.md")):
        if child.stem not in _RESERVED_STEMS:
            return child
    return None


def read_document(config: Any) -> str | None:
    """The document's current text, straight off disk -- ``None`` if the file
    is gone. Not a cached copy: the agent may have rewritten it since upload,
    and showing the pre-run version would be a lie about what's stored."""
    path = concept_file_for(config)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def meta_for(config: Any) -> DocumentMeta:
    """The stored document's frontmatter, or an empty meta if it's unreadable.

    Never raises: a document whose file went missing or was rewritten into
    something unparseable should still appear in the list (so it can be
    deleted) rather than taking the whole listing down with it.
    """
    text = read_document(config)
    if text is None:
        return DocumentMeta(title=None, description=None, concept_type=None, tags=[])
    try:
        frontmatter, _body = parse_document(text)
    except OkfDocumentError:
        return DocumentMeta(title=None, description=None, concept_type=None, tags=[])
    return meta_from_frontmatter(frontmatter)


def tool_names_for(config: Any) -> list[str]:
    """The document server's discovered tool names, bare -- see
    ``okf_bundles.tool_names_for``, which this mirrors exactly."""
    tools = ((config.capabilities or {}).get("tools")) or []
    return [t["name"] for t in tools if isinstance(t, dict) and t.get("name")]


async def list_documents(owner_id: uuid.UUID) -> list[Any]:
    """Every document this user uploaded. Owner-scoped with no system
    fallback, same as ``okf_bundles.list_bundles``."""
    servers = await mcp_service.list_servers(owner_id=owner_id)
    return [s for s in servers if is_document_server(s) and s.owner_id == owner_id]


def _allocate_directory(owner_id: uuid.UUID, slug: str) -> Path:
    """A fresh, empty directory for one document.

    A taken slug gets ``-2``, ``-3`` ... rather than replacing what's there:
    the existing document may already have been edited by an agent, and
    "upload a file" should never be a destructive act. Deleting the duplicate
    is one click in the documents panel.
    """
    base = owner_root(owner_id)
    for attempt in range(1, _MAX_SLUG_ATTEMPTS + 1):
        candidate = base / (slug if attempt == 1 else f"{slug}-{attempt}")
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        except OSError as exc:
            raise OkfDocumentError(f"Could not create document storage at {candidate}: {exc}") from exc
        return candidate
    raise OkfDocumentError(f"Too many documents already stored under the name {slug!r} -- delete some first.")


async def register_document(*, owner_id: uuid.UUID, text: str, filename: str | None = None) -> Any:
    """Store an uploaded concept and spawn an OKF server over it.

    Spawns during registration (via ``mcp_service.register_server``) so a
    storage path the server can't actually serve fails here rather than
    mid-run -- same contract as ``okf_bundles.register_bundle``. Not
    idempotent, unlike that function: two uploads of the same file are two
    documents, because the second upload's content may differ from what the
    first one has since become.
    """
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise OkfDocumentError(f"That file is larger than the {MAX_DOCUMENT_BYTES // 1000}kB limit for one concept.")
    frontmatter, _body = parse_document(text)
    # The title, not the upload's filename: the filename is often "Untitled
    # 3.md" or a temp name, while the title is what the user actually called
    # the concept and what every OKF listing shows. The filename is only the
    # fallback for the (already rejected) titleless case.
    stem = frontmatter.get("title") or (Path(filename).stem if filename else "")
    slug = slugify(str(stem))

    directory = _allocate_directory(owner_id, slug)
    try:
        ensure_command_safe(directory)
        (directory / f"{slug}.md").write_text(text, encoding="utf-8")
        return await mcp_service.register_server(
            name=server_name_for(owner_id, directory),
            transport="stdio",
            command=command_for_bundle(directory),
            owner_id=owner_id,
        )
    except Exception:
        # The directory only exists because this call created it, so an
        # upload that fails anywhere after mkdir leaves nothing behind --
        # otherwise a rejected registration would silently burn the slug and
        # push the next attempt to "-2".
        shutil.rmtree(directory, ignore_errors=True)
        raise


async def delete_document(config: Any) -> None:
    """Forget the registration AND remove the stored file.

    The opposite of ``okf_bundles``' delete, deliberately: there, the
    directory is the user's own and predates ASAREE. Here it's storage this
    module created, so leaving it behind would just accumulate orphans no
    screen in the app can reach.
    """
    directory = document_dir_for(config)
    await mcp_service.delete_server(config.id)
    if directory is not None and directory.is_dir() and directory.is_relative_to(document_root()):
        shutil.rmtree(directory, ignore_errors=True)
