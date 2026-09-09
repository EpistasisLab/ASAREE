"""The prompt format is a versioned, pinned input -- not whatever the code says today.

The property under test is that **v1 is frozen**. An experiment created before
prompt versioning existed must keep getting byte-for-byte the prompt it has
always got, no matter what v2 improves, because its published numbers were
produced under that text. ``tests/test_spinal_compat.py`` asserts the same thing
against the real spinal graph; this file asserts the mechanism.
"""

from __future__ import annotations

import uuid
from typing import Any

from asaree.api.experiments import _preserved_prompt_contract_version
from asaree.services.prompt_contract import (
    DEFAULT_PROMPT_CONTRACT_VERSION,
    LATEST_PROMPT_CONTRACT_VERSION,
    prompt_contract_version,
)
from asaree.services.protocol_execution import _build_user_input

EXPERIMENT_ID = uuid.uuid4()


def _agent(node_id: str, label: str, prompt: str = "Do the thing.") -> dict[str, Any]:
    return {"id": node_id, "type": "agent", "data": {"label": label, "config": {"prompt": prompt}}}


def _two_step() -> dict[str, Any]:
    return {
        "nodes": [_agent("a", "Analyst"), _agent("b", "Reviewer")],
        "edges": [{"id": "e1", "source": "a", "target": "b"}],
    }


def _prompt(graph: dict[str, Any], node_id: str, node_runs: dict[str, Any], version: int) -> str:
    node = next(n for n in graph["nodes"] if n["id"] == node_id)
    return _build_user_input(node, graph, node_runs, prompt_contract_version=version)


# ----------------------------------------------------------------------
# Resolving the version
# ----------------------------------------------------------------------


def test_an_absent_version_is_v1() -> None:
    """Every experiment that predates this field. Non-negotiable."""
    assert prompt_contract_version(None) == 1
    assert prompt_contract_version({}) == 1
    assert prompt_contract_version({"factors": []}) == 1


def test_a_declared_version_is_honored() -> None:
    assert prompt_contract_version({"prompt_contract_version": 2}) == 2
    assert prompt_contract_version({"prompt_contract_version": "2"}) == 2


def test_an_unusable_version_falls_back_to_v1_rather_than_raising() -> None:
    """A corrupted value must not silently reformat a published experiment's
    prompts, and refusing to run at all is a worse failure than running the
    format the experiment has always used."""
    assert prompt_contract_version({"prompt_contract_version": "latest"}) == 1
    assert prompt_contract_version({"prompt_contract_version": None}) == 1
    assert prompt_contract_version({"prompt_contract_version": 0}) == 1
    assert prompt_contract_version({"prompt_contract_version": -3}) == 1


def test_the_default_is_never_moved_off_v1() -> None:
    """A guard on the constant itself: bumping this would retroactively change
    every pre-versioning experiment, which is the one thing this module exists
    to prevent."""
    assert DEFAULT_PROMPT_CONTRACT_VERSION == 1
    assert LATEST_PROMPT_CONTRACT_VERSION >= DEFAULT_PROMPT_CONTRACT_VERSION


# ----------------------------------------------------------------------
# v1 -- frozen
# ----------------------------------------------------------------------


def test_v1_labels_upstream_output_with_the_raw_node_id() -> None:
    graph = _two_step()
    text = _prompt(graph, "b", {"a": {"status": "completed", "output_text": "Findings."}}, 1)
    assert text == "Do the thing.\n\nUpstream context:\n[a]: Findings."


def test_v1_is_what_an_unknown_version_gets() -> None:
    """A future version number reaching old code resolves down, not up: v1 is
    the format every experiment can be run under."""
    graph = _two_step()
    node_runs = {"a": {"status": "completed", "output_text": "Findings."}}
    assert _prompt(graph, "b", node_runs, 99) == _prompt(graph, "b", node_runs, 1)


def test_no_upstream_output_means_no_upstream_block_in_either_version() -> None:
    graph = _two_step()
    for version in (1, 2):
        assert _prompt(graph, "a", {}, version) == "Do the thing."
        # A node that ran but produced nothing contributes nothing.
        assert _prompt(graph, "b", {"a": {"status": "completed", "output_text": ""}}, version) == "Do the thing."


# ----------------------------------------------------------------------
# v2 -- named sender
# ----------------------------------------------------------------------


def test_v2_names_the_upstream_agent() -> None:
    graph = _two_step()
    text = _prompt(graph, "b", {"a": {"status": "completed", "output_text": "Findings."}}, 2)
    assert text == "Do the thing.\n\nUpstream context:\n[Analyst]: Findings."


def test_v2_falls_back_to_the_type_placeholder_when_a_node_is_unlabelled() -> None:
    graph = _two_step()
    graph["nodes"][0]["data"]["label"] = ""
    text = _prompt(graph, "b", {"a": {"status": "completed", "output_text": "Findings."}}, 2)
    assert "[Agent]: Findings." in text


def test_v2_disambiguates_two_upstream_nodes_with_the_same_name() -> None:
    """The node id earns its space for exactly one question -- which of the two
    -- so it appears only then."""
    graph = {
        "nodes": [_agent("a1", "Worker"), _agent("a2", "Worker"), _agent("b", "Reviewer")],
        "edges": [{"id": "e1", "source": "a1", "target": "b"}, {"id": "e2", "source": "a2", "target": "b"}],
    }
    node_runs = {
        "a1": {"status": "completed", "output_text": "First."},
        "a2": {"status": "completed", "output_text": "Second."},
    }
    text = _prompt(graph, "b", node_runs, 2)
    assert "[Worker (a1)]: First." in text
    assert "[Worker (a2)]: Second." in text


def test_v2_keeps_two_distinct_names_bare() -> None:
    graph = {
        "nodes": [_agent("a1", "Analyst"), _agent("a2", "Statistician"), _agent("b", "Reviewer")],
        "edges": [{"id": "e1", "source": "a1", "target": "b"}, {"id": "e2", "source": "a2", "target": "b"}],
    }
    node_runs = {
        "a1": {"status": "completed", "output_text": "First."},
        "a2": {"status": "completed", "output_text": "Second."},
    }
    text = _prompt(graph, "b", node_runs, 2)
    assert "[Analyst]: First." in text
    assert "[Statistician]: Second." in text
    assert "(a1)" not in text


# ----------------------------------------------------------------------
# Keeping the version across writes
# ----------------------------------------------------------------------


def test_a_design_spec_patch_that_omits_the_version_keeps_the_recorded_one() -> None:
    """``design_spec`` is a full replacement, so the SDK, the notebook and any
    older client all PATCH specs they built from scratch. Dropping the version
    there would move a v2 experiment back to v1 and change what its agents are
    told on the next run."""
    kept = _preserved_prompt_contract_version({"prompt_contract_version": 2, "factors": []}, {"factors": [{"x": 1}]})
    assert kept == {"factors": [{"x": 1}]} | {"prompt_contract_version": 2}


def test_an_explicit_version_in_the_patch_still_wins() -> None:
    """How an import restores an experiment at the version it was run under."""
    kept = _preserved_prompt_contract_version(
        {"prompt_contract_version": 2}, {"factors": [], "prompt_contract_version": 1}
    )
    assert kept == {"factors": [], "prompt_contract_version": 1}


def test_an_experiment_with_no_recorded_version_gains_none() -> None:
    """v1 stays *absent* rather than being written in, so a legacy experiment's
    spec is not rewritten by an unrelated PATCH."""
    assert _preserved_prompt_contract_version({"factors": []}, {"factors": [{"x": 1}]}) == {"factors": [{"x": 1}]}
    assert _preserved_prompt_contract_version(None, {"factors": []}) == {"factors": []}


def test_clearing_the_design_spec_entirely_is_left_alone() -> None:
    assert _preserved_prompt_contract_version({"prompt_contract_version": 2}, None) is None
