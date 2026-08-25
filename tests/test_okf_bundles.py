"""The rules an uploaded OKF bundle folder has to pass, and who owns its files.

Only the pure functions: what a browser's `webkitRelativePath` list has to
look like to be storable, how a folder name becomes a directory name, and
which stored directories deleting is allowed to destroy. Registration itself
spawns an MCP server, which is Motoro's mcp_service to exercise.
"""

from __future__ import annotations

import uuid

import pytest

from asaree.config import get_settings
from asaree.services import okf_bundles as ob


def test_normalise_upload_paths_strips_the_picked_folder() -> None:
    folder, relatives = ob.normalise_upload_paths(["spine/index.md", "spine/concepts/cord.md"])
    assert folder == "spine"
    assert relatives == ["index.md", "concepts/cord.md"]


def test_normalise_upload_paths_rejects_loose_files() -> None:
    # A single segment means the user picked files, not a folder -- there'd be
    # no name to store the bundle under.
    with pytest.raises(ob.OkfBundleError, match="pick a folder"):
        ob.normalise_upload_paths(["cord.md"])


def test_normalise_upload_paths_rejects_hidden_segments() -> None:
    with pytest.raises(ob.OkfBundleError, match="hidden or relative"):
        ob.normalise_upload_paths(["spine/.git/config.md"])


def test_normalise_upload_paths_rejects_parent_traversal() -> None:
    with pytest.raises(ob.OkfBundleError, match="hidden or relative"):
        ob.normalise_upload_paths(["spine/../escape.md"])


def test_normalise_upload_paths_rejects_non_markdown() -> None:
    with pytest.raises(ob.OkfBundleError, match="isn't a .md file"):
        ob.normalise_upload_paths(["spine/index.md", "spine/diagram.png"])


def test_normalise_upload_paths_rejects_two_folders() -> None:
    with pytest.raises(ob.OkfBundleError, match="more than one folder"):
        ob.normalise_upload_paths(["spine/index.md", "brain/index.md"])


def test_normalise_upload_paths_rejects_duplicate_paths() -> None:
    # Two files landing on the same destination would silently lose one.
    with pytest.raises(ob.OkfBundleError, match="same path"):
        ob.normalise_upload_paths(["spine/a/cord.md", "spine//a/cord.md"])


def test_normalise_upload_paths_rejects_an_empty_upload() -> None:
    with pytest.raises(ob.OkfBundleError, match="no Markdown files"):
        ob.normalise_upload_paths([])


def test_normalise_upload_paths_rejects_too_many_files() -> None:
    names = [f"spine/c{i}.md" for i in range(ob.MAX_UPLOAD_FILES + 1)]
    with pytest.raises(ob.OkfBundleError, match="pick the bundle folder itself"):
        ob.normalise_upload_paths(names)


def test_slugify_normalises_a_folder_name() -> None:
    assert ob.slugify("Spinal Cord Notes!") == "spinal-cord-notes"


def test_slugify_falls_back_when_nothing_survives() -> None:
    assert ob.slugify("***") == "bundle"


def test_allocate_storage_dir_suffixes_a_taken_name(tmp_path) -> None:
    first = ob.allocate_storage_dir(tmp_path, "spine")
    second = ob.allocate_storage_dir(tmp_path, "spine")
    assert first.name == "spine"
    # Never reused: the first copy may already have been rewritten by an agent.
    assert second.name == "spine-2"


def test_is_uploaded_path_only_claims_asaree_owned_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "okf_bundle_upload_dir", str(tmp_path / "uploads"), raising=False)
    owner = uuid.uuid4()
    inside = ob.owner_upload_root(owner) / "spine"
    assert ob.is_uploaded_path(inside) is True
    assert ob.is_uploaded_path(tmp_path / "somewhere-else" / "spine") is False
    assert ob.is_uploaded_path(None) is False
