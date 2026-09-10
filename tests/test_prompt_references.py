"""The reference syntax, and the graph rules that decide what may be referenced.

Two layers, tested in one file because they are one feature: the pure string
parser (:mod:`asaree.services.prompt_references`) and the graph questions the
parser deliberately refuses to answer -- which nodes are in scope
(``referenceable_node_ids``) and which prompts are refused before a run starts
(``validate_prompt_references``).

The property the whole thing exists for: a reference the picker offers is a
reference the validator accepts, and a reference the validator accepts is one
that can actually resolve. Both sides call ``referenceable_node_ids``, so the
tests below pin the rule rather than each side's copy of it.
"""

from __future__ import annotations

from typing import Any

import pytest

from asaree.services import prompt_references as pr
from asaree.services.protocol_execution import (
    ProtocolValidationError,
    _build_system_prompt,
    referenceable_node_ids,
    validate_prompt_references,
)


def _agent(node_id: str, label: str = "", prompt: str = "Do the thing.") -> dict[str, Any]:
    return {"id": node_id, "type": "agent", "data": {"label": label, "config": {"prompt": prompt}}}


def _edge(source: str, target: str, handle: str | None = None) -> dict[str, Any]:
    edge = {"id": f"{source}-{target}", "source": source, "target": target}
    if handle:
        edge["targetHandle"] = handle
    return edge


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------


def test_each_documented_form_parses_to_its_kind() -> None:
    kinds = [ref.kind for ref in pr.iter_references("{{previous}} {{node:x}}")]
    assert kinds == ["previous", "node"]


def test_the_withdrawn_prose_tokens_are_no_longer_references() -> None:
    """``{{audience}}`` and ``{{upstream_instructions}}`` resolved to
    platform-composed sentences rather than to data. Every surviving form
    resolves to data, so these parse as nothing and survive substitution as the
    literal text they are -- a prompt saved while they existed still renders."""
    assert list(pr.iter_references("{{audience}} {{upstream_instructions}}")) == []
    assert not pr.has_references("{{audience}} {{upstream_instructions}}")


def test_whitespace_inside_the_braces_is_tolerated() -> None:
    """Prompts are hand-authored in the notebook and the SDK with no picker to
    normalize them, so a stray space must not silently turn a reference into
    literal text -- the quiet failure this design exists to avoid."""
    assert pr.referenced_node_ids("{{  node:abc  }}") == ["abc"]
    assert pr.uses("{{ previous }}", pr.PREVIOUS)
    assert pr.uses("{{previous | raw}}", pr.PREVIOUS)


def test_the_token_name_is_case_insensitive_but_the_node_id_is_not() -> None:
    """Opposite defaults on purpose: ``Previous`` is a word a human mistypes,
    while a node id is a case-sensitive identifier that lowercasing would turn
    into a reference to a node that does not exist."""
    assert pr.uses("{{Previous|RAW}}", pr.PREVIOUS)
    assert pr.referenced_node_ids("{{NODE:AbC}}") == ["AbC"]


def test_raw_is_recorded_and_defaults_off() -> None:
    plain, raw = list(pr.iter_references("{{previous}} {{previous|raw}}"))
    assert (plain.raw, raw.raw) == (False, True)


def test_the_token_keeps_the_spelling_it_was_authored_with() -> None:
    """Substitution replaces the matched text, so it must not have to re-derive
    how the author happened to write it."""
    (ref,) = pr.iter_references("x {{ Node:a1 | raw }} y")
    assert ref.token == "{{ Node:a1 | raw }}"


def test_ids_are_reported_distinct_and_in_first_appearance_order() -> None:
    assert pr.referenced_node_ids("{{node:b}} {{node:a}} {{node:b}}") == ["b", "a"]


def test_canvas_minted_ids_and_older_shorter_ones_both_match() -> None:
    assert pr.referenced_node_ids("{{node:node-msza682j-w2vslmwn}} {{node:dndnode_3}} {{node:a}}") == [
        "node-msza682j-w2vslmwn",
        "dndnode_3",
        "a",
    ]


def test_anything_else_in_double_braces_is_left_alone() -> None:
    """A prompt instructing an agent to *emit* a template has to survive the
    substitution pass unharmed."""
    text = "{{ user.name }} {{}} {{ node }} {{previous!}}"
    assert not pr.has_references(text)
    assert pr.substitute(text, lambda ref: "SUBSTITUTED") == text


def test_substitute_replaces_every_occurrence_with_what_render_returns() -> None:
    text = "A {{previous}} B {{node:x}} C"
    assert pr.substitute(text, lambda ref: f"<{ref.kind}>") == "A <previous> B <node> C"


def test_serialize_is_the_one_place_the_spelling_is_defined() -> None:
    """The picker's output. Round-tripping it is what stops a second, subtly
    different spelling appearing in the frontend."""
    assert pr.referenced_node_ids(pr.serialize_node_reference("n1")) == ["n1"]
    (ref,) = pr.iter_references(pr.serialize_node_reference("n1", raw=True))
    assert ref.raw


def test_a_field_reference_splits_into_the_node_and_the_field() -> None:
    (ref,) = pr.iter_references("{{node:node-msza682j-w2vslmwn.n_rows}}")
    assert (ref.kind, ref.node_id, ref.field) == ("node", "node-msza682j-w2vslmwn", "n_rows")


def test_a_whole_node_reference_has_no_field() -> None:
    (ref,) = pr.iter_references("{{node:a}}")
    assert ref.field == ""


def test_a_field_reference_still_counts_as_referencing_its_node() -> None:
    """The property everything downstream leans on: "which senders does this
    prompt name" must not have to know that fields exist. The canvas's
    `no reference` edge chip and the Receives panel's `not referenced` chip
    both ask exactly that question."""
    assert pr.referenced_node_ids("{{node:a.n_rows}} {{node:b}}") == ["a", "b"]


def test_fields_are_reported_per_node_distinct_and_in_order() -> None:
    assert pr.referenced_node_fields("{{node:a.b}} {{node:c}} {{node:a.x}} {{node:a.b}}") == {"a": ["b", "x"]}


def test_a_node_referenced_only_as_a_whole_is_absent_from_the_field_map() -> None:
    """Absent, not an empty list: the caller is checking names against a
    declared contract, and `[]` would read as "declares nothing" rather than
    "asked for nothing"."""
    assert pr.referenced_node_fields("{{node:a}}") == {}


def test_a_field_reference_takes_raw_and_keeps_its_case() -> None:
    (ref,) = pr.iter_references("{{ NODE:AbC.nRows | raw }}")
    assert (ref.node_id, ref.field, ref.raw) == ("AbC", "nRows", True)


def test_a_field_name_that_is_not_an_identifier_is_not_a_reference() -> None:
    """Narrower than an id on purpose -- the field becomes an attribute on the
    model Motoro builds from the contract. A near-miss stays literal text
    rather than being guessed at."""
    assert not pr.has_references("{{node:a.9rows}}")
    assert not pr.has_references("{{node:a.n-rows}}")
    assert not pr.has_references("{{node:a.}}")


def test_serialize_spells_the_field_form_too() -> None:
    assert pr.serialize_node_reference("n1", field="n_rows") == "{{node:n1.n_rows}}"
    (ref,) = pr.iter_references(pr.serialize_node_reference("n1", field="n_rows", raw=True))
    assert (ref.node_id, ref.field, ref.raw) == ("n1", "n_rows", True)


def test_empty_and_missing_text_are_not_errors() -> None:
    assert not pr.has_references("")
    assert pr.referenced_node_ids("") == []
    assert pr.substitute("", lambda ref: "x") == ""


# ----------------------------------------------------------------------
# What is in scope
# ----------------------------------------------------------------------


def test_scope_is_every_ancestor_not_just_the_direct_predecessor() -> None:
    """Reaching back past an intermediate step is the capability references
    were added for, so the scope has to be transitive."""
    graph = {
        "nodes": [_agent("a"), _agent("b"), _agent("c")],
        "edges": [_edge("a", "b"), _edge("b", "c")],
    }
    assert referenceable_node_ids(graph, "c") == ["a", "b"]
    assert referenceable_node_ids(graph, "a") == []


def test_scope_is_returned_in_canvas_declaration_order() -> None:
    """So the picker's list is stable between renders rather than reflecting
    whatever order the walk happened to visit."""
    graph = {
        "nodes": [_agent("c"), _agent("a"), _agent("b")],
        "edges": [_edge("a", "b"), _edge("b", "c")],
    }
    assert referenceable_node_ids(graph, "c") == ["a", "b"]


def test_a_parallel_branch_is_out_of_scope() -> None:
    """Ancestry, not walk position, is what establishes "runs before". Two
    parallel branches have an arbitrary relative order, so a reference across
    them would resolve or not depending on scheduling -- the opposite of what
    this feature is for."""
    graph = {
        "nodes": [_agent("root"), _agent("left"), _agent("right")],
        "edges": [_edge("root", "left"), _edge("root", "right")],
    }
    assert referenceable_node_ids(graph, "left") == ["root"]
    assert "right" not in referenceable_node_ids(graph, "left")


def test_connector_nodes_are_out_of_scope() -> None:
    """An LLM, dataset, memory or tool node has no output of its own -- it
    configures the agent it hangs off. Offering one would be offering a
    reference that always resolves empty."""
    graph = {
        "nodes": [
            _agent("a"),
            {"id": "llm", "type": "llm_anthropic", "data": {"label": "", "config": {}}},
            _agent("b"),
        ],
        "edges": [_edge("a", "b"), _edge("llm", "b", "ai")],
    }
    assert referenceable_node_ids(graph, "b") == ["a"]


def test_a_cycle_does_not_hang_the_walk() -> None:
    """Conversation strategies run cyclic canvases, and this function is called
    from the validator that runs before the acyclic check.

    A node is never its own ancestor even when the cycle says so: it has no
    output of its own yet at the moment its prompt is assembled.
    """
    graph = {"nodes": [_agent("a"), _agent("b")], "edges": [_edge("a", "b"), _edge("b", "a")]}
    assert referenceable_node_ids(graph, "a") == ["b"]


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def _validate(prompt: str) -> None:
    graph = {
        "nodes": [_agent("a", "Analyst"), _agent("side", "Sidebar"), _agent("b", "Reviewer", prompt)],
        "edges": [_edge("a", "b")],
    }
    validate_prompt_references(graph=graph)


def test_a_valid_reference_passes() -> None:
    _validate("Review {{node:a}} and {{previous}}.")


def test_a_prompt_with_no_references_passes_even_though_it_has_a_predecessor() -> None:
    """An agent that starts fresh is a legitimate design, and this is how it is
    expressed now that nothing is automatic. It is also the "wired but nothing
    flows" case -- which the canvas marks, because refusing it here would refuse
    a valid experiment."""
    _validate("Do the thing.")


def test_a_reference_to_a_node_that_no_longer_exists_is_refused() -> None:
    with pytest.raises(ProtocolValidationError, match="no longer exists"):
        _validate("Review {{node:deleted}}.")


def test_a_reference_to_a_node_that_does_not_run_first_is_refused() -> None:
    """The failure that would otherwise surface as an empty variable halfway
    through a batch, when the user is no longer looking at the canvas."""
    with pytest.raises(ProtocolValidationError, match="does not run before it"):
        _validate("Review {{node:side}}.")


def test_previous_with_nothing_connected_is_refused() -> None:
    graph = {"nodes": [_agent("only", "Solo", "Review {{previous}}.")], "edges": []}
    with pytest.raises(ProtocolValidationError, match="nothing is connected"):
        validate_prompt_references(graph=graph)


def test_the_error_names_the_nodes_by_label_because_that_is_what_the_user_sees() -> None:
    with pytest.raises(ProtocolValidationError) as exc:
        _validate("Review {{node:side}}.")
    assert "Reviewer" in str(exc.value)
    assert "Sidebar" in str(exc.value)


def _parser(parser_id: str, agent_id: str, *fields: str, enabled: bool = True) -> dict[str, Any]:
    """An Output Parser node wired to *agent_id*, declaring *fields* as strings."""
    return {
        "node": {
            "id": parser_id,
            "type": "output_parser",
            "data": {
                "label": "Parser",
                "config": {
                    "enabled": enabled,
                    "output_contract": {
                        "name": "report",
                        "fields": [{"name": f, "type": "string"} for f in fields],
                    },
                },
            },
        },
        "edge": {
            "id": f"{parser_id}-{agent_id}",
            "source": parser_id,
            "target": agent_id,
            "sourceHandle": "output_parser",
            "targetHandle": "output_parser",
        },
    }


def _validate_with_parser(prompt: str, *fields: str) -> None:
    parser = _parser("p", "a", *fields)
    graph = {
        "nodes": [_agent("a", "Analyst"), parser["node"], _agent("b", "Reviewer", prompt)],
        "edges": [_edge("a", "b"), parser["edge"]],
    }
    validate_prompt_references(graph=graph)


def test_a_field_the_producers_parser_declares_passes() -> None:
    _validate_with_parser("There were {{node:a.n_rows}} rows.", "n_rows", "target_column")


def test_a_field_the_producers_parser_does_not_declare_is_refused() -> None:
    """Design time, against the declaration -- nothing has run yet. A typo
    would otherwise surface as an empty substitution halfway through a batch."""
    with pytest.raises(ProtocolValidationError, match="does not declare"):
        _validate_with_parser("There were {{node:a.n_row}} rows.", "n_rows")


def test_the_undeclared_field_error_lists_what_is_declared() -> None:
    with pytest.raises(ProtocolValidationError) as exc:
        _validate_with_parser("{{node:a.nope}}", "n_rows", "target_column")
    assert "n_rows, target_column" in str(exc.value)


def test_a_field_reference_to_a_producer_with_no_parser_is_refused() -> None:
    with pytest.raises(ProtocolValidationError, match="no Output Parser"):
        _validate("There were {{node:a.n_rows}} rows.")


def test_a_disabled_parser_declares_nothing() -> None:
    """`enabled: false` suspends the extraction, so nothing will be there to
    read -- the same answer as no parser at all, caught before the run."""
    parser = _parser("p", "a", "n_rows", enabled=False)
    graph = {
        "nodes": [_agent("a", "Analyst"), parser["node"], _agent("b", "Reviewer", "{{node:a.n_rows}}")],
        "edges": [_edge("a", "b"), parser["edge"]],
    }
    with pytest.raises(ProtocolValidationError, match="no Output Parser"):
        validate_prompt_references(graph=graph)


def test_an_out_of_scope_field_reference_reports_the_scope_problem_first() -> None:
    """One message per prompt, and the wiring is the thing to fix -- naming a
    field on a node that never runs first is not a field problem."""
    with pytest.raises(ProtocolValidationError, match="does not run before it"):
        _validate("{{node:side.n_rows}}")




# ----------------------------------------------------------------------
# The System prompt field
# ----------------------------------------------------------------------
#
# Same picker, same syntax, same rules -- the tests below exist because that
# equivalence is the whole claim. A token that resolved in Prompt and arrived
# as literal `{{node:a}}` text in System prompt would be the worst outcome of
# offering the picker in both boxes.


def _validate_system(system_prompt: str) -> None:
    reviewer = _agent("b", "Reviewer")
    reviewer["data"]["config"]["system_prompt"] = system_prompt
    graph = {
        "nodes": [_agent("a", "Analyst"), _agent("side", "Sidebar"), reviewer],
        "edges": [_edge("a", "b")],
    }
    validate_prompt_references(graph=graph)


def test_a_system_prompt_reference_is_policed_like_a_prompt_reference() -> None:
    _validate_system("You review {{node:a}}.")
    with pytest.raises(ProtocolValidationError, match="does not run before it"):
        _validate_system("You review {{node:side}}.")


def test_a_refused_system_prompt_says_which_box_to_open() -> None:
    """The message is the only thing telling the user which of the node's two
    reference-bearing fields the bad token is in."""
    with pytest.raises(ProtocolValidationError) as exc:
        _validate_system("You review {{node:side}}.")
    assert "system prompt" in str(exc.value)


def test_a_system_prompt_resolves_its_references() -> None:
    reviewer = _agent("b", "Reviewer")
    reviewer["data"]["config"]["system_prompt"] = "You review the analyst's work: {{node:a}}"
    graph = {"nodes": [_agent("a", "Analyst"), reviewer], "edges": [_edge("a", "b")]}
    node_runs = {"a": {"status": "completed", "output_text": "42 rows, no nulls."}}

    rendered = _build_system_prompt(reviewer, graph, node_runs)
    assert rendered is not None
    assert "42 rows, no nulls." in rendered
    assert "{{node:a}}" not in rendered




def test_no_system_prompt_returns_none_so_the_caller_keeps_its_own_default() -> None:
    """Not the empty string: what an unset System prompt becomes is
    ``_run_agent_node``'s decision, and answering it here too would give two
    answers to drift apart."""
    assert _build_system_prompt(_agent("b", "Reviewer"), {"nodes": [], "edges": []}, {}) is None


def test_an_empty_system_prompt_reference_is_reported_not_raised() -> None:
    """Same out-parameter the user prompt uses, so the Runs tab reports the
    node rather than which of its fields had the gap."""
    reviewer = _agent("b", "Reviewer")
    reviewer["data"]["config"]["system_prompt"] = "You review {{node:a}}"
    graph = {"nodes": [_agent("a", "Analyst"), reviewer], "edges": [_edge("a", "b")]}
    unresolved: list[str] = []

    _build_system_prompt(
        reviewer,
        graph,
        {"a": {"status": "completed", "output_text": ""}},
        unresolved_out=unresolved,
    )
    assert unresolved == ["a"]
