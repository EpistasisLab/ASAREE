"""Persona factors choose immutable node snapshots and share one level across recipients."""

from __future__ import annotations

import copy
import uuid
from unittest.mock import AsyncMock

import pytest

from asaree.services.design_generation import get_design_impact
from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.persona_factors import test_persona_factor_values as preview_values
from asaree.services.protocol_execution import _resolve_persona_configs, apply_factor_bindings


def persona_graph():
    ids = ["persona-0", "persona-1"]
    graph = {
        "nodes": [
            {"id": "agent", "type": "agent", "data": {
                "factor_bindings": {"persona_selection": "Personas"}, "persona_selection": [ids[0]],
            }},
            *[{"id": id, "type": "persona", "data": {
                "config": {"persona_id": "deleted-library-record", "persona_text": id, "enabled": False},
            }} for id in ids],
        ],
        "edges": [{"source": id, "target": "agent", "targetHandle": "persona"} for id in ids],
    }
    spec = {"factors": [{"name": "Personas", "level_type": "persona_selection", "levels": [[id] for id in ids]}]}
    return graph, spec, ids


def test_cell_selection_overrides_individual_switches_without_mutating_graph():
    graph, spec, ids = persona_graph()
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    assert _resolve_persona_configs(graph, "agent") == [ids[0]]
    patched = apply_factor_bindings(graph, {"Personas": [ids[1]]})
    assert _resolve_persona_configs(patched, "agent") == [ids[1]]
    assert graph == original


def test_baseline_persona_is_a_valid_level_with_an_unmodified_prompt():
    graph, spec, ids = persona_graph()
    graph["nodes"][1]["data"]["config"]["persona_text"] = ""
    validate_factor_bindings(spec, graph)
    assert _resolve_persona_configs(graph, "agent") == []
    assert _resolve_persona_configs(apply_factor_bindings(graph, {"Personas": [ids[1]]}), "agent") == [ids[1]]


@pytest.mark.asyncio
async def test_incomplete_one_persona_design_can_be_published_and_previewed_but_not_generated():
    graph, spec, ids = persona_graph()
    graph["edges"].pop()
    spec["factors"][0]["levels"] = [[ids[0]]]
    validate_factor_bindings(spec, graph, complete_skills=False)
    assert await preview_values(graph, None, uuid.uuid4(), "agent") == {"Personas": [ids[0]]}
    with pytest.raises(ValueError, match="two different personas"):
        validate_factor_bindings(spec, graph)


@pytest.mark.parametrize("enabled", [False, True])
def test_individual_on_off_excludes_or_includes_only_the_bound_persona(enabled):
    graph, _, ids = persona_graph()
    graph["nodes"][0]["data"] = {}
    graph["nodes"][1]["data"]["factor_bindings"] = {"config.enabled": "Enabled"}
    graph["nodes"][2]["data"]["config"]["enabled"] = True
    patched = apply_factor_bindings(graph, {"Enabled": enabled})
    assert _resolve_persona_configs(patched, "agent") == (ids if enabled else [ids[1]])


@pytest.mark.parametrize("none_first", [False, True])
def test_all_or_none_runs_override_disabled_personas_and_preserve_graph(none_first):
    graph, spec, ids = persona_graph()
    graph["nodes"][0]["data"]["persona_factor_mode"] = "persona_toggle"
    spec["factors"][0].update(level_type="persona_toggle", levels=[[], ids] if none_first else [ids, []])
    graph["nodes"][0]["data"]["persona_selection"] = spec["factors"][0]["levels"][0]
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    assert _resolve_persona_configs(apply_factor_bindings(graph, {"Personas": ids}), "agent") == ids
    assert _resolve_persona_configs(apply_factor_bindings(graph, {"Personas": []}), "agent") == []
    assert graph == original


def test_all_or_none_accepts_one_persona_and_applies_to_all_connected_agents():
    graph, spec, ids = persona_graph()
    graph["edges"].pop()
    graph["nodes"][0]["data"].update(persona_factor_mode="persona_toggle", persona_selection=[ids[0]])
    graph["nodes"].append({"id": "other-agent", "type": "agent", "data": copy.deepcopy(graph["nodes"][0]["data"])})
    graph["edges"].append({"source": "persona-0", "target": "other-agent", "targetHandle": "persona"})
    spec["factors"][0].update(level_type="persona_toggle", levels=[[ids[0]], []])
    validate_factor_bindings(spec, graph)
    patched = apply_factor_bindings(graph, {"Personas": [ids[0]]})
    assert _resolve_persona_configs(patched, "agent") == [ids[0]]
    assert _resolve_persona_configs(patched, "other-agent") == [ids[0]]
    disabled = apply_factor_bindings(graph, {"Personas": []})
    assert _resolve_persona_configs(disabled, "agent") == _resolve_persona_configs(disabled, "other-agent") == []


@pytest.mark.parametrize("levels", ["partial", "duplicate", "all-only", "foreign"])
def test_all_or_none_rejects_invalid_levels(levels):
    graph, spec, ids = persona_graph()
    graph["nodes"][0]["data"]["persona_factor_mode"] = "persona_toggle"
    spec["factors"][0].update(
        level_type="persona_toggle",
        levels={
            "partial": [[ids[0]], []],
            "duplicate": [ids + [ids[0]], []],
            "all-only": [ids, ids],
            "foreign": [[ids[0], str(uuid.uuid4())], []],
        }[levels],
    )
    with pytest.raises(ValueError, match="every connected persona or none"):
        validate_factor_bindings(spec, graph)


@pytest.mark.asyncio
async def test_all_or_none_preview_default_and_explicit_choice():
    graph, _, ids = persona_graph()
    owner = uuid.uuid4()
    graph["nodes"][0]["data"].update(persona_factor_mode="persona_toggle", persona_selection=ids)
    assert await preview_values(graph, None, owner) == {"Personas": ids}
    assert await preview_values(graph, {"agent": "none"}, owner) == {"Personas": []}
    graph["nodes"][0]["data"]["persona_selection"] = []
    assert await preview_values(graph, None, owner) == {"Personas": []}
    with pytest.raises(ValueError, match="all enabled"):
        await preview_values(graph, {"agent": ids[0]}, owner)


@pytest.mark.asyncio
async def test_shared_preview_choice_is_not_overwritten_by_another_agents_default():
    graph, _, ids = persona_graph()
    owner = uuid.uuid4()
    graph["nodes"].append({"id": "other", "type": "agent", "data": copy.deepcopy(graph["nodes"][0]["data"])})
    graph["edges"].extend([
        {"source": f"persona-{index}", "target": "other", "targetHandle": "persona"} for index in range(2)
    ])
    assert await preview_values(graph, {"agent": ids[1]}, owner) == {"Personas": [ids[1]]}
    assert await preview_values(graph, {"other": ids[1]}, owner) == {"Personas": [ids[1]]}
    with pytest.raises(ValueError, match="different levels"):
        await preview_values(graph, {"agent": ids[0], "other": ids[1]}, owner)


@pytest.mark.parametrize("change", ["one", "missing-level", "duplicate", "conflict"])
def test_incomplete_or_conflicting_factor_blocks_cells(change):
    graph, spec, ids = persona_graph()
    if change == "one":
        graph["edges"].pop()
        spec["factors"][0]["levels"] = [[ids[0]]]
    elif change == "missing-level":
        spec["factors"][0]["levels"].pop()
    elif change == "duplicate":
        spec["factors"][0]["levels"] = [[ids[0]], [ids[0]]]
    else:
        graph["nodes"][1]["data"]["factor_bindings"] = {"config.enabled": "On"}
    with pytest.raises(ValueError):
        validate_factor_bindings(spec, graph)


@pytest.mark.asyncio
@pytest.mark.parametrize("level_type,levels", [("persona_selection", []), ("persona_toggle", [[], []])])
async def test_empty_persona_factor_has_readable_design_impact_instead_of_error(monkeypatch, level_type, levels):
    monkeypatch.setattr("asaree.services.design_generation.get_current_revision", AsyncMock(return_value=None))
    impact = await get_design_impact(
        AsyncMock(),
        experiment_id=uuid.uuid4(),
        design_spec={"factors": [{"name": "Personas", "level_type": level_type, "levels": levels}]},
    )
    assert impact.proposed_cell_count == 0
    assert impact.proposed_replicate_count == 0
