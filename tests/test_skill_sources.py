"""The URL parsing and archive walking behind fetching a skill from GitHub.

Only the pure functions: which URLs resolve to which repo/ref/path, and what a
tarball's bytes turn into. The download itself is one `httpx` call against
codeload, which isn't this module's logic to exercise -- the tarballs below are
built in memory and handed straight to the walker.
"""

from __future__ import annotations

import io
import tarfile

import pytest

from asaree.services import skill_sources as ss

SKILL = """---
name: code-simplification
description: Simplifies overwrought code. Use when a diff reads like a puzzle.
---

Read the code. Delete the clever parts.
"""


def make_archive(files: dict[str, str], root: str = "repo-main") -> bytes:
    """A gzipped tar shaped like codeload's: everything under one top folder."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, text in files.items():
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(name=f"{root}/{path}")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return buffer.getvalue()


# --- parse_github_url ---------------------------------------------------


def test_parses_a_bare_repo_url() -> None:
    source = ss.parse_github_url("https://github.com/anthropics/skills")
    assert (source.owner, source.repo, source.ref, source.subdirectory) == ("anthropics", "skills", None, "")


def test_a_bare_repo_url_leaves_the_ref_unknown() -> None:
    # Not defaulted to "main" here: _download_archive tries main then master,
    # and pinning a guess this early would make an older repo unreachable.
    assert ss.parse_github_url("github.com/owner/repo").ref is None


def test_accepts_a_url_pasted_without_a_scheme() -> None:
    assert ss.parse_github_url("github.com/owner/repo").repo == "repo"


def test_parses_a_tree_url_into_ref_and_subdirectory() -> None:
    source = ss.parse_github_url("https://github.com/anthropics/skills/tree/v2/document-skills/pdf")
    assert (source.ref, source.subdirectory) == ("v2", "document-skills/pdf")


def test_a_blob_url_drops_the_skill_md_and_keeps_its_folder() -> None:
    # What is registered is the directory; the file is just what the user
    # happened to be looking at when they copied the address bar.
    source = ss.parse_github_url("https://github.com/owner/repo/blob/main/skills/foo/SKILL.md")
    assert source.subdirectory == "skills/foo"


def test_strips_a_dot_git_suffix() -> None:
    assert ss.parse_github_url("https://github.com/owner/repo.git").repo == "repo"


def test_label_reads_as_owner_repo_path() -> None:
    source = ss.parse_github_url("https://github.com/owner/repo/tree/main/skills/foo")
    assert source.label == "owner/repo/skills/foo"


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/owner/repo",
        "https://github.com.evil.test/owner/repo",
        "https://raw.githubusercontent.com/owner/repo/main/SKILL.md",
    ],
)
def test_rejects_hosts_that_are_not_github(url: str) -> None:
    # An allowlist, not a denylist: the feature's whole scope is github.com, so
    # naming what it can reach sidesteps DNS rebinding rather than racing it.
    with pytest.raises(ss.SkillSourceError, match="GitHub"):
        ss.parse_github_url(url)


def test_rejects_a_non_https_url() -> None:
    with pytest.raises(ss.SkillSourceError, match="https"):
        ss.parse_github_url("http://github.com/owner/repo")


def test_rejects_a_url_with_no_repository_in_it() -> None:
    with pytest.raises(ss.SkillSourceError, match="doesn't name a repository"):
        ss.parse_github_url("https://github.com/anthropics")


def test_rejects_a_github_page_that_is_not_a_repo_or_folder() -> None:
    with pytest.raises(ss.SkillSourceError, match="GitHub page"):
        ss.parse_github_url("https://github.com/owner/repo/issues/12")


def test_rejects_an_empty_url() -> None:
    with pytest.raises(ss.SkillSourceError, match="Paste a GitHub URL"):
        ss.parse_github_url("   ")


# --- _read_archive ------------------------------------------------------


def test_reads_files_relative_to_the_repo_root() -> None:
    files = ss._read_archive(make_archive({"SKILL.md": SKILL, "docs/REF.md": "ref"}))
    assert sorted(files) == ["SKILL.md", "docs/REF.md"]


def test_drops_files_core_would_refuse() -> None:
    # Scripts, binaries and dotfiles are dropped rather than raised over: a
    # repository is full of them and none is the user's mistake. The rule is
    # core's validate_bundle_path, not a second copy of it here.
    files = ss._read_archive(
        make_archive({"SKILL.md": SKILL, "scripts/run.py": "print(1)", ".github/workflows/ci.yml": "on: push"})
    )
    assert sorted(files) == ["SKILL.md"]


def test_drops_files_that_are_not_utf8() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, raw in (("repo-main/SKILL.md", SKILL.encode()), ("repo-main/logo.md", b"\xff\xfe\x00")):
            info = tarfile.TarInfo(name=name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    assert sorted(ss._read_archive(buffer.getvalue())) == ["SKILL.md"]


def test_ignores_symlinks_and_other_non_files() -> None:
    # Nothing in the archive should be able to point *out* of it.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        raw = SKILL.encode()
        info = tarfile.TarInfo(name="repo-main/SKILL.md")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
        link = tarfile.TarInfo(name="repo-main/escape.md")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../etc/passwd"
        archive.addfile(link)
    assert sorted(ss._read_archive(buffer.getvalue())) == ["SKILL.md"]


def test_refuses_an_archive_that_unpacks_past_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Checked as the tar is read, not against the download size: the ratio
    # between the two is attacker-controlled.
    monkeypatch.setattr(ss, "MAX_EXTRACTED_BYTES", 10)
    with pytest.raises(ss.SkillSourceError, match="more than the fetch limit"):
        ss._read_archive(make_archive({"SKILL.md": SKILL}))


def test_refuses_an_archive_with_too_many_members(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "MAX_ARCHIVE_MEMBERS", 2)
    with pytest.raises(ss.SkillSourceError, match="too many files"):
        ss._read_archive(make_archive({f"doc{i}.md": "x" for i in range(5)}))


# --- _skill_dirs and _bundle_at -----------------------------------------


def test_finds_every_skill_directory_in_a_collection() -> None:
    files = ss._read_archive(
        make_archive({"README.md": "hi", "skills/foo/SKILL.md": SKILL, "skills/bar/SKILL.md": SKILL})
    )
    assert ss._skill_dirs(files, "") == ["skills/bar", "skills/foo"]


def test_a_skill_at_the_repo_root_is_the_empty_directory() -> None:
    files = ss._read_archive(make_archive({"SKILL.md": SKILL}))
    assert ss._skill_dirs(files, "") == [""]


def test_ignores_a_skill_buried_deeper_than_the_discovery_depth() -> None:
    files = ss._read_archive(make_archive({"a/b/c/SKILL.md": SKILL}))
    assert ss._skill_dirs(files, "") == []


def test_searching_a_subdirectory_only_looks_inside_it() -> None:
    files = ss._read_archive(make_archive({"skills/foo/SKILL.md": SKILL, "other/bar/SKILL.md": SKILL}))
    assert ss._skill_dirs(files, "skills") == ["skills/foo"]


def test_a_bundle_is_re_rooted_at_the_skill_directory() -> None:
    # core's parse_skill_bundle wants paths relative to the skill itself, so
    # skills/foo/references/x.md has to arrive as references/x.md.
    files = ss._read_archive(
        make_archive({"skills/foo/SKILL.md": SKILL, "skills/foo/references/x.md": "ref", "README.md": "hi"})
    )
    assert ss._bundle_at(files, "skills/foo") == [("SKILL.md", SKILL), ("references/x.md", "ref")]


def test_a_skill_does_not_swallow_a_nested_skill() -> None:
    # A repo with a SKILL.md at its root AND a skills/ folder would otherwise
    # read as one giant skill holding all the others' files. The nesting says
    # they are separate skills, so the outer one stops where an inner one
    # starts.
    files = ss._read_archive(make_archive({"SKILL.md": SKILL, "NOTES.md": "notes", "skills/foo/SKILL.md": SKILL}))
    directories = ss._skill_dirs(files, "")
    assert directories == ["", "skills/foo"]
    assert ss._bundle_at(files, "", directories) == [("NOTES.md", "notes"), ("SKILL.md", SKILL)]
