"""The parsing and naming rules an uploaded OKF concept has to pass.

Only the pure functions: what makes a file acceptable, and what it's stored
as. Registration itself spawns an MCP server and writes to disk, which is
Motoro's mcp_service to exercise, not this module's.
"""

from __future__ import annotations

import pytest

from asaree.services import okf_documents as od

CONCEPT = """---
title: Spinal cord
type: anatomy
description: The cord itself
tags:
  - neuro
  - cord
---

The spinal cord is a bundle of nerves.
"""


def test_parse_document_returns_frontmatter_and_body() -> None:
    frontmatter, body = od.parse_document(CONCEPT)
    assert frontmatter["title"] == "Spinal cord"
    assert frontmatter["type"] == "anatomy"
    assert body.strip() == "The spinal cord is a bundle of nerves."


def test_parse_document_without_frontmatter_raises() -> None:
    with pytest.raises(od.OkfDocumentError, match="YAML frontmatter block"):
        od.parse_document("Just some markdown, no delimiters.\n")


def test_parse_document_without_title_raises() -> None:
    # `title` is the one required field -- every OKF listing projects it, so a
    # titleless concept is invisible in the listing meant to surface it.
    with pytest.raises(od.OkfDocumentError, match="non-empty `title`"):
        od.parse_document("---\ntype: anatomy\n---\n\nBody.\n")


def test_parse_document_with_blank_title_raises() -> None:
    with pytest.raises(od.OkfDocumentError, match="non-empty `title`"):
        od.parse_document('---\ntitle: "   "\n---\n\nBody.\n')


def test_parse_document_with_non_mapping_frontmatter_raises() -> None:
    with pytest.raises(od.OkfDocumentError, match="must be a mapping"):
        od.parse_document("---\n- one\n- two\n---\n\nBody.\n")


def test_parse_document_with_invalid_yaml_raises() -> None:
    with pytest.raises(od.OkfDocumentError, match="isn't valid YAML"):
        od.parse_document("---\ntitle: [unclosed\n---\n\nBody.\n")


def test_meta_from_frontmatter_maps_type_to_concept_type_and_sorts_tags() -> None:
    frontmatter, _body = od.parse_document(CONCEPT)
    meta = od.meta_from_frontmatter(frontmatter)
    assert meta.title == "Spinal cord"
    assert meta.concept_type == "anatomy"
    assert meta.description == "The cord itself"
    assert meta.tags == ["cord", "neuro"]


def test_meta_from_frontmatter_ignores_wrongly_typed_fields() -> None:
    # An agent rewrites this file mid-run, so the frontmatter it produces is
    # not guaranteed to keep the shapes the upload had.
    meta = od.meta_from_frontmatter({"title": 3, "description": ["a"], "type": None, "tags": "neuro"})
    assert meta.title is None
    assert meta.description is None
    assert meta.concept_type is None
    assert meta.tags == []


def test_slugify_normalises_a_title() -> None:
    assert od.slugify("Spinal Cord: Anatomy & Function") == "spinal-cord-anatomy-function"


def test_slugify_falls_back_when_nothing_survives() -> None:
    assert od.slugify("!!!") == "concept"


def test_slugify_caps_length() -> None:
    assert len(od.slugify("word " * 100)) <= 64


def test_slugify_avoids_okf_reserved_stems() -> None:
    # index.md/log.md at a bundle root are never concepts, so a document
    # slugging to one would be invisible to every tool the agent has.
    assert od.slugify("Index") == "index-concept"
    assert od.slugify("Log") == "log-concept"


def test_server_name_is_prefixed_and_owner_scoped(tmp_path) -> None:
    import uuid

    directory = tmp_path / "spinal-cord"
    one = od.server_name_for(uuid.uuid4(), directory)
    two = od.server_name_for(uuid.uuid4(), directory)
    assert one.startswith(od.DOCUMENT_SERVER_NAME_PREFIX)
    assert one.startswith(f"{od.DOCUMENT_SERVER_NAME_PREFIX}spinal-cord-")
    # Two users can each hold a "spinal-cord" concept, and the name column is
    # unique deployment-wide.
    assert one != two
