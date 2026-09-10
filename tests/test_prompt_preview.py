"""The design-time prompt preview.

The point of a preview is that it is the *same* prompt, so almost every test
here is an equality against what ``_build_user_input`` produces on the real run
path. A preview that quietly diverges is worse than no preview: it would be
read as evidence about a run it does not describe.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from asaree.services import protocol_execution as pe
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    _build_user_input,
    preview_node_prompt,
)

OWNER_ID = uuid.uuid4()
EXPERIMENT_ID = uuid.uuid4()
CURRENT = {"prompt_contract_version": 2}


def _agent(node_id: str, label: str, prompt: str) -> dict[str, Any]:
    return {"id": node_id, "type": "agent", "data": {"label": label, "config": {"prompt": prompt}}}


def _edge(source: str, target: str) -> dict[str, Any]:
    return {"id": f"{source}-{target}", "source": source, "target": target}


def _chain(*prompts: str) -> dict[str, Any]:
    """A → B → C..., each agent named after its position."""
    ids = [chr(ord("a") + i) for i in range(len(prompts))]
    return {
        "nodes": [_agent(i, f"Agent {i.upper()}", p) for i, p in zip(ids, prompts, strict=True)],
        "edges": [_edge(ids[i], ids[i + 1]) for i in range(len(ids) - 1)],
    }


def _wire_dataset(graph: dict[str, Any], name: str = "spinal") -> None:
    graph["nodes"].append({"id": "ds", "type": "dataset", "data": {"label": name, "config": {"dataset_name": name}}})
    graph["edges"].append({"id": "e-ds", "source": "ds", "target": "a", "targetHandle": "dataset"})


def _placeholders(graph: dict[str, Any], *node_ids: str) -> dict[str, Any]:
    by_id = {n["id"]: n for n in graph["nodes"]}
    return {
        node_id: {
            "status": "completed",
            "output_text": f'<output of "{by_id[node_id]["data"]["label"]}">',
            "error": None,
            "payload": {},
        }
        for node_id in node_ids
    }


def _wire_parser(graph: dict[str, Any], target: str, *fields: str) -> None:
    graph["nodes"].append(
        {
            "id": f"parser-{target}",
            "type": "output_parser",
            "data": {
                "label": "Parser",
                "config": {
                    "output_contract": {
                        "name": "shape",
                        "fields": [{"name": f, "type": "integer"} for f in fields],
                    }
                },
            },
        }
    )
    graph["edges"].append(
        {"id": f"e-parser-{target}", "source": f"parser-{target}", "target": target, "targetHandle": "output_parser"}
    )


# ----------------------------------------------------------------------
# Field references
# ----------------------------------------------------------------------


async def test_a_field_reference_previews_as_that_field_not_as_a_hole() -> None:
    """The failure this replaces rendered `The dataset has  rows.` -- a sentence
    with a gap in it, which reads as a broken prompt rather than as a value that
    does not exist until the run."""
    graph = _chain("Profile it.", "It has {{node:a.n_rows}} rows.")
    _wire_parser(graph, "a", "n_rows", "n_cols")

    preview = await preview_node_prompt(graph, "b", owner_id=OWNER_ID, design_spec=CURRENT)

    assert 'It has <n_rows of "Agent A"> rows.' in preview


async def test_a_field_the_parser_does_not_declare_still_previews_as_the_gap_it_will_be() -> None:
    """Standing in for every field asked for would hide a wiring mistake the
    run would really hit -- the preview may be unfinished, never wrong."""
    graph = _chain("Profile it.", "It has {{node:a.n_columns}} columns.")
    _wire_parser(graph, "a", "n_rows")

    preview = await preview_node_prompt(graph, "b", owner_id=OWNER_ID, design_spec=CURRENT)

    assert "It has  columns." in preview


# ----------------------------------------------------------------------
# It is the real prompt
# ----------------------------------------------------------------------


async def test_a_first_step_previews_exactly_what_it_would_really_get() -> None:
    """No upstream means nothing is standing in for anything, so this one is a
    straight equality with no 'modulo the placeholder' escape hatch."""
    graph = _chain("Start here.")
    preview = await preview_node_prompt(graph, "a", owner_id=OWNER_ID, design_spec=CURRENT)
    real = _build_user_input(graph["nodes"][0], graph, {}, prompt_contract_version=2)
    assert preview == real
    assert preview == "Start here."


async def test_a_referenced_upstream_output_previews_as_a_named_placeholder() -> None:
    graph = _chain("Draft it.", "Polish this: {{previous}}")
    preview = await preview_node_prompt(graph, "b", owner_id=OWNER_ID, design_spec=CURRENT)
    assert '<output of "Agent A">' in preview
    assert preview == _build_user_input(
        graph["nodes"][1], graph, _placeholders(graph, "a"), prompt_contract_version=2
    )


async def test_the_placeholder_is_fenced_the_way_a_real_output_would_be() -> None:
    """The envelope is part of what a user is checking when they read a
    preview -- how much of the prompt is scaffolding, and where their own
    sentence ends."""
    graph = _chain("Draft it.", "Polish this: {{previous}}")
    fenced = await preview_node_prompt(graph, "b", owner_id=OWNER_ID, design_spec=CURRENT)
    graph["nodes"][1]["data"]["config"]["prompt"] = "Polish this: {{previous|raw}}"
    raw = await preview_node_prompt(graph, "b", owner_id=OWNER_ID, design_spec=CURRENT)
    assert raw == 'Polish this: <output of "Agent A">'
    assert fenced != raw
    assert '<output of "Agent A">' in fenced


async def test_a_reach_back_reference_previews_rather_than_leaving_a_gap() -> None:
    """Every ancestor gets a placeholder, not just the direct predecessor --
    the preview offers what the picker offers, so a reference the picker
    accepted never previews as though it resolved to nothing.

    B's block is here too, and unreferenced: it is what the edge into C will
    deliver on its own, and a preview that omitted it would understate the
    prompt.
    """
    graph = _chain("First.", "Second.", "Third, recalling {{node:a|raw}}.")
    preview = await preview_node_prompt(graph, "c", owner_id=OWNER_ID, design_spec=CURRENT)
    assert preview.startswith('Third, recalling <output of "Agent A">.')
    assert '[Agent B]' in preview


async def test_an_unwired_predecessor_previews_as_the_block_it_will_deliver() -> None:
    """The design-time half of the automatic block: B references nothing, and
    the preview still shows A's output arriving, because the edge is the
    request. This is where "I wired it, does anything actually flow?" gets
    answered before a cell is spent on it."""
    graph = _chain("Draft it.", "Write a poem.")
    preview = await preview_node_prompt(graph, "b", owner_id=OWNER_ID, design_spec=CURRENT)
    assert preview == _build_user_input(
        graph["nodes"][1], graph, _placeholders(graph, "a"), prompt_contract_version=2
    )
    assert '[Agent A]' in preview
    assert '<output of "Agent A">' in preview


async def test_the_legacy_contract_previews_its_own_format() -> None:
    """A pinned experiment must preview the prompt it will actually run, not
    the one the current contract would produce. Both contracts deliver A's
    output unasked; they disagree on how it is labelled and fenced."""
    graph = _chain("Draft it.", "Polish it.")
    legacy = await preview_node_prompt(graph, "b", owner_id=OWNER_ID, design_spec={"prompt_contract_version": 1})
    current = await preview_node_prompt(graph, "b", owner_id=OWNER_ID, design_spec=CURRENT)
    assert legacy == 'Polish it.\n\nUpstream context:\n[a]: <output of "Agent A">'
    assert legacy != current
    assert '[Agent A]' in current


# ----------------------------------------------------------------------
# What it refuses
# ----------------------------------------------------------------------


async def test_a_node_that_is_not_an_agent_has_no_prompt_to_preview() -> None:
    graph = _chain("Draft it.")
    graph["nodes"].append({"id": "llm", "type": "llm_anthropic", "data": {"label": "Claude", "config": {}}})
    with pytest.raises(ProtocolValidationError, match="not an agent"):
        await preview_node_prompt(graph, "llm", owner_id=OWNER_ID, design_spec=CURRENT)


async def test_a_node_that_is_not_on_the_canvas_is_refused() -> None:
    with pytest.raises(ProtocolValidationError, match="No node"):
        await preview_node_prompt(_chain("Draft it."), "ghost", owner_id=OWNER_ID, design_spec=CURRENT)


# ----------------------------------------------------------------------
# It creates nothing
# ----------------------------------------------------------------------


async def test_previewing_a_wired_dataset_reads_the_registration_but_seeds_no_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Dataset cue depends on whether the registration has a split, so the
    preview has to read it -- but ``seed_cell_workspace`` opens a real workspace
    on disk, and a preview that did that would be a run with extra steps."""
    graph = _chain("Analyze it.")
    _wire_dataset(graph)

    async def _registration(name: str, owner_id: uuid.UUID) -> dict[str, Any]:
        assert (name, owner_id) == ("spinal", OWNER_ID)
        return {"train_path": "/train.csv", "test_path": "/test.csv"}

    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a preview must not seed a workspace")

    monkeypatch.setattr(pe, "fetch_owned_registration", _registration)
    monkeypatch.setattr(pe, "seed_cell_workspace", _refuse)

    preview = await preview_node_prompt(
        graph, "a", owner_id=OWNER_ID, experiment_id=EXPERIMENT_ID, design_spec=CURRENT
    )
    assert "Dataset context:" in preview
    assert "already open" in preview


async def test_an_unsplit_dataset_previews_the_cue_it_would_really_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three Dataset outcomes read completely differently to an agent, so
    getting the classification right is most of what dataset fidelity means."""
    graph = _chain("Analyze it.")
    _wire_dataset(graph)

    async def _registration(name: str, owner_id: uuid.UUID) -> dict[str, Any]:
        return {"raw_path": "/raw.csv", "target_column": "class"}

    monkeypatch.setattr(pe, "fetch_owned_registration", _registration)
    preview = await preview_node_prompt(
        graph, "a", owner_id=OWNER_ID, experiment_id=EXPERIMENT_ID, design_spec=CURRENT
    )
    assert "has NOT been split" in preview
