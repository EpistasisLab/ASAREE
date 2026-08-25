"""Fetching Agent Skills out of a public GitHub repository.

The third way into :func:`motoro.services.skill_service.create_skill_from_bundle`,
alongside the single-file and picked-folder uploads in :mod:`asaree.api.skills`.
It exists because skills are *distributed* as repositories -- the `npx`
installers in the wild do nothing but copy a repo's `SKILL.md` and its bundled
files onto disk where an agent can see them, which is exactly what ASAREE's
skill library already is.

**A repo is usually a collection, not a skill.** ``owner/repo`` typically holds
``skills/code-simplification/SKILL.md``, ``skills/pdf-forms/SKILL.md`` and a
dozen more, so this module is split into :func:`discover_skills` (what is in
there?) and :func:`fetch_skill_bundle` (give me that one). The GUI runs the
first to populate a checklist and the second per chosen skill. Both re-fetch
the archive rather than caching it between the two calls: the download is
capped small, and a server-side cache keyed by URL is state with a TTL to get
wrong for no gain at this size.

**Nothing is ever written to disk and nothing is executed.** The archive is
walked in memory and only regular files whose paths pass core's
``validate_bundle_path`` survive, so a skill from a stranger's repository
arrives as exactly the same rows as one the user picked out of a folder --
scripts refused, text only, the same caps. That is the whole security posture
for the *contents*; the fetch itself is guarded below.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from motoro.services.skill_service import (
    SKILL_MD,
    SkillFormatError,
    parse_skill_markdown,
    validate_bundle_path,
)

# A host allowlist rather than the general private-IP check in Motoro's
# security.ssrf_guard. This feature's entire scope is GitHub, and naming the
# two hosts it can reach is strictly stronger than filtering the ones it
# can't: it sidesteps DNS rebinding rather than racing it, and there is no
# redirect or user-controlled hostname that can widen it later.
ALLOWED_HOSTS = frozenset({"github.com", "www.github.com", "codeload.github.com"})

# The compressed download. Small because a skills repo is markdown: anything
# near this is a repo that was never a skill collection.
MAX_ARCHIVE_BYTES = 10_000_000
# Checked separately as the tar is read, because the ratio between these two
# is attacker-controlled -- a kilobyte of gzip expands to gigabytes if nothing
# is counting on the way out.
MAX_EXTRACTED_BYTES = 40_000_000
# Guards the walk itself, so a tarball of a million empty files costs a bounded
# number of iterations rather than a bounded number of bytes.
MAX_ARCHIVE_MEMBERS = 20_000
# How deep below the requested path a SKILL.md is looked for. 2 covers both
# real layouts -- SKILL.md at the root of what was linked, and the usual
# skills/<name>/SKILL.md one level down -- without walking a monorepo.
MAX_DISCOVERY_DEPTH = 2

# The default branch is not knowable without an API call, so both are tried in
# turn. Cheap: a miss is one 404 against a CDN.
DEFAULT_REFS = ("main", "master")


class SkillSourceError(ValueError):
    """A URL that can't be fetched, or an archive with no skill in it.

    A ``ValueError`` subclass so ``api/skills.py`` can turn it into the same
    422 that a ``SkillFormatError`` from core already becomes -- from the
    caller's side "that repo has no SKILL.md" and "that SKILL.md has no
    description" are the same kind of mistake.
    """


@dataclass(frozen=True)
class GithubSource:
    """A parsed GitHub URL: which repo, which ref, and which path inside it."""

    owner: str
    repo: str
    # None means "unknown, try the defaults" -- a bare repo URL says nothing
    # about the branch, and guessing 'main' outright breaks older repos.
    ref: str | None
    # "" means the repo root. Always forward-slashed, never leading/trailing.
    subdirectory: str

    @property
    def label(self) -> str:
        """``owner/repo/path``, for showing provenance back to the user."""
        return f"{self.owner}/{self.repo}" + (f"/{self.subdirectory}" if self.subdirectory else "")


@dataclass(frozen=True)
class DiscoveredSkill:
    """One ``SKILL.md`` found in an archive, with enough to choose it by.

    ``subdirectory`` is what to pass back to :func:`fetch_skill_bundle` -- it
    is repo-relative, so it round-trips through the API without the client
    having to reconstruct a URL.
    """

    subdirectory: str
    name: str
    description: str
    file_count: int


def parse_github_url(url: str) -> GithubSource:
    """Parse the URL forms a user actually pastes.

    Three of them::

        https://github.com/owner/repo
        https://github.com/owner/repo/tree/main/skills/code-simplification
        https://github.com/owner/repo/blob/main/skills/foo/SKILL.md

    The ``/tree/`` form is the common one, since it is what the browser's
    address bar holds while looking at a skill -- and it carries the ref and
    the subdirectory, so neither has to be a separate field the user could
    disagree with. The ``/blob/`` form is the same thing with a file on the
    end; the file is dropped, because what is being registered is its
    directory.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        raise SkillSourceError("Paste a GitHub URL.")
    if "://" not in cleaned:
        # "github.com/owner/repo" is what a copy-paste from prose looks like.
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)
    if parsed.scheme != "https":
        raise SkillSourceError("Only https:// GitHub URLs can be fetched.")
    if (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise SkillSourceError(
            f"{parsed.hostname or 'That host'} isn't GitHub. Skills can only be fetched from github.com -- "
            "for anything else, download the folder and upload it."
        )

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise SkillSourceError("That URL doesn't name a repository -- it should look like github.com/owner/repo.")

    owner, repo = parts[0], parts[1].removesuffix(".git")
    rest = parts[2:]
    if not rest:
        return GithubSource(owner=owner, repo=repo, ref=None, subdirectory="")

    if rest[0] in ("tree", "blob") and len(rest) >= 2:
        ref, path_parts = rest[1], rest[2:]
        if rest[0] == "blob" and path_parts and path_parts[-1].lower() == SKILL_MD.lower():
            path_parts = path_parts[:-1]
        return GithubSource(owner=owner, repo=repo, ref=ref, subdirectory="/".join(path_parts))

    raise SkillSourceError(
        "That looks like a GitHub page rather than a repo or a folder in one -- paste the repo URL, or the "
        "URL of the skill's own folder."
    )


async def _download_archive(source: GithubSource) -> tuple[bytes, str]:
    """The repo as a gzipped tar, plus the ref it actually came from.

    ``codeload.github.com`` serves this without auth and without a ``git``
    binary in the image -- one request, no clone, no working tree.
    """
    refs: tuple[str, ...] = (source.ref,) if source.ref else DEFAULT_REFS
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        for ref in refs:
            url = f"https://codeload.github.com/{source.owner}/{source.repo}/tar.gz/{ref}"
            try:
                async with client.stream("GET", url) as response:
                    if response.status_code == 404:
                        continue
                    if response.status_code != 200:
                        raise SkillSourceError(
                            f"GitHub answered {response.status_code} for {source.owner}/{source.repo}."
                        )
                    # Accumulated chunk by chunk so an oversized body is
                    # abandoned mid-flight; Content-Length is the server's
                    # claim, not a limit.
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_ARCHIVE_BYTES:
                            raise SkillSourceError(
                                f"{source.owner}/{source.repo} is larger than the "
                                f"{MAX_ARCHIVE_BYTES // 1_000_000}MB fetch limit."
                            )
                        chunks.append(chunk)
                    return b"".join(chunks), ref
            except httpx.HTTPError as exc:
                raise SkillSourceError(f"Could not reach GitHub: {exc}") from exc

    tried = " or ".join(refs)
    raise SkillSourceError(
        f"No {tried} branch in {source.owner}/{source.repo} -- check the URL, or link the branch directly."
    )


def _read_archive(raw: bytes) -> dict[str, str]:
    """``{repo-relative path: text}`` for every readable file in the tarball.

    Three things are dropped rather than raised over, because a repository is
    full of them and none is the user's mistake: non-regular members
    (symlinks, hardlinks, devices -- nothing here should be able to point
    *out* of the archive), files that are not UTF-8, and anything
    ``validate_bundle_path`` refuses. That last one is what keeps a fetched
    skill identical to an uploaded one: scripts, images and hidden paths never
    become rows, and the rule lives in core rather than being restated here.
    """
    files: dict[str, str] = {}
    extracted = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for index, member in enumerate(archive):
            if index >= MAX_ARCHIVE_MEMBERS:
                raise SkillSourceError("That repository has too many files to scan for skills.")
            if not member.isfile():
                continue
            extracted += member.size
            if extracted > MAX_EXTRACTED_BYTES:
                raise SkillSourceError("That repository unpacks to more than the fetch limit allows.")
            # GitHub wraps everything in a "<repo>-<ref>/" top-level directory.
            relative = member.name.split("/", 1)[1] if "/" in member.name else ""
            if not relative:
                continue
            try:
                validate_bundle_path(relative)
            except SkillFormatError:
                continue
            handle = archive.extractfile(member)
            if handle is None:  # pragma: no cover -- isfile() already excludes these
                continue
            try:
                files[relative] = handle.read().decode("utf-8")
            except UnicodeDecodeError:
                continue
    return files


def _skill_dirs(files: dict[str, str], subdirectory: str) -> list[str]:
    """Directories holding a ``SKILL.md``, at or under *subdirectory*."""
    prefix = f"{subdirectory}/" if subdirectory else ""
    found: list[str] = []
    for path in files:
        if not path.startswith(prefix):
            continue
        segments = path[len(prefix) :].split("/")
        if segments[-1].lower() != SKILL_MD.lower():
            continue
        if len(segments) - 1 > MAX_DISCOVERY_DEPTH:
            continue
        found.append("/".join([*([subdirectory] if subdirectory else []), *segments[:-1]]).strip("/"))
    return sorted(found)


def _bundle_at(files: dict[str, str], subdirectory: str, siblings: Sequence[str] = ()) -> list[tuple[str, str]]:
    """One skill directory's subtree, re-rooted so ``SKILL.md`` is at the top.

    Re-rooting is the point: core's ``parse_skill_bundle`` wants paths relative
    to the skill itself, so ``skills/foo/references/schema.md`` has to arrive as
    ``references/schema.md`` -- the same normalisation the folder upload does by
    stripping ``webkitRelativePath``'s leading segment.

    *siblings* is every other discovered skill directory, and subtrees under one
    are excluded. A repo with a ``SKILL.md`` at its root *and* a ``skills/``
    folder is otherwise read as one giant skill that swallows all the others'
    files -- the nesting says they are separate skills, so the outer one stops
    where an inner one starts.
    """
    prefix = f"{subdirectory}/" if subdirectory else ""
    nested = tuple(f"{other}/" for other in siblings if other != subdirectory and other.startswith(prefix))
    return sorted(
        (path[len(prefix) :], text)
        for path, text in files.items()
        if path.startswith(prefix) and path != prefix and not path.startswith(nested)
    )


async def discover_skills(url: str) -> tuple[GithubSource, list[DiscoveredSkill]]:
    """Every skill in the repo (or folder) *url* names.

    Returns the parsed source alongside the findings so the caller can show
    which repo and ref actually answered -- a bare URL resolves its own
    branch, and the user should see which one.
    """
    source = parse_github_url(url)
    raw, ref = await _download_archive(source)
    files = _read_archive(raw)
    resolved = GithubSource(owner=source.owner, repo=source.repo, ref=ref, subdirectory=source.subdirectory)

    directories = _skill_dirs(files, source.subdirectory)
    if not directories:
        where = f"{source.subdirectory} in " if source.subdirectory else ""
        raise SkillSourceError(
            f"No SKILL.md in {where}{source.owner}/{source.repo}. An Agent Skill is a folder whose entry "
            "point is a SKILL.md -- link the repo holding them, or one skill's own folder."
        )

    discovered: list[DiscoveredSkill] = []
    for directory in directories:
        bundle = _bundle_at(files, directory, directories)
        entry = next((text for path, text in bundle if path.lower() == SKILL_MD.lower()), "")
        try:
            parsed = parse_skill_markdown(entry)
        except SkillFormatError:
            # Listed, not refused: a repo may hold one malformed skill among
            # ten good ones, and dropping it silently would look like it
            # simply is not there. Registering it is what surfaces the reason.
            discovered.append(
                DiscoveredSkill(
                    subdirectory=directory,
                    name=directory.rsplit("/", 1)[-1],
                    description="This skill's frontmatter can't be read -- registering it will say why.",
                    file_count=max(len(bundle) - 1, 0),
                )
            )
            continue
        discovered.append(
            DiscoveredSkill(
                subdirectory=directory,
                name=parsed.name,
                description=parsed.description,
                file_count=max(len(bundle) - 1, 0),
            )
        )
    return resolved, discovered


async def fetch_skill_bundle(url: str, subdirectory: str) -> tuple[GithubSource, list[tuple[str, str]]]:
    """One skill's files, ready for ``create_skill_from_bundle``.

    *subdirectory* is repo-relative and comes from a prior
    :func:`discover_skills` call, so it is re-validated here rather than
    trusted: it arrives back over HTTP and a client is free to send anything.
    """
    source = parse_github_url(url)
    raw, ref = await _download_archive(source)
    files = _read_archive(raw)

    wanted = (subdirectory or source.subdirectory).strip("/")
    directories = _skill_dirs(files, source.subdirectory)
    if wanted not in directories:
        raise SkillSourceError(f"No SKILL.md at {wanted or '/'} in {source.owner}/{source.repo}.")

    resolved = GithubSource(owner=source.owner, repo=source.repo, ref=ref, subdirectory=wanted)
    return resolved, _bundle_at(files, wanted, directories)
