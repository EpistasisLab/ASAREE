"""Skill connector factors select one skill without changing reusable nodes."""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from asaree.services.design_generation import get_design_impact
from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.protocol_execution import _resolve_skill_config, apply_factor_bindings
from asaree.services.skill_factors import test_skill_factor_values as preview_values
from asaree.services.skill_factors import validate_skill_factors


def skill_graph():
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    graph = {
        "nodes": [
            {
                "id": "agent",
                "type": "agent",
                "data": {
                    "factor_bindings": {"skill_selection": "Skills"},
                    "skill_selection": [ids[0]],
                },
            },
            *[
                {
                    "id": f"skill-{index}",
                    "type": "skill",
                    "data": {
                        "config": {"skill_id": skill_id, "enabled": False},
                    },
                }
                for index, skill_id in enumerate(ids)
            ],
        ],
        "edges": [{"source": f"skill-{index}", "target": "agent", "targetHandle": "skill"} for index in range(2)],
    }
    spec = {"factors": [{"name": "Skills", "level_type": "skill_selection", "levels": [[id] for id in ids]}]}
    return graph, spec, ids


def test_cell_selection_overrides_individual_switches_without_mutating_graph():
    graph, spec, ids = skill_graph()
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    assert _resolve_skill_config(graph, "agent") == {"skill_ids": [ids[0]]}
    patched = apply_factor_bindings(graph, {"Skills": [ids[1]]})
    assert _resolve_skill_config(patched, "agent") == {"skill_ids": [ids[1]]}
    assert graph == original


@pytest.mark.parametrize("change", ["one", "missing-level", "duplicate", "conflict"])
def test_incomplete_or_conflicting_factor_blocks_cells(change):
    graph, spec, ids = skill_graph()
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
async def test_preview_accepts_valid_alternative_even_when_other_skill_unavailable(monkeypatch):
    graph, _, ids = skill_graph()
    owner = uuid.uuid4()
    get_skill = AsyncMock(
        side_effect=lambda id: SimpleNamespace(owner_id=owner, is_system=False) if str(id) == ids[1] else None
    )
    monkeypatch.setattr("asaree.services.skill_factors.skill_service.get_skill", get_skill)
    assert await preview_values(graph, {"agent": ids[1]}, owner) == {"Skills": [ids[1]]}
    assert get_skill.await_count == 1
    with pytest.raises(ValueError, match="unavailable"):
        await preview_values(graph, None, owner)
    with pytest.raises(ValueError, match="belong"):
        await preview_values(graph, {"other-agent": ids[1]}, owner)


@pytest.mark.asyncio
async def test_generation_checks_every_skill_but_one_skill_can_be_published_and_tested(monkeypatch):
    graph, spec, ids = skill_graph()
    owner = uuid.uuid4()
    monkeypatch.setattr("asaree.services.skill_factors.skill_service.get_skill", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="unavailable"):
        await validate_skill_factors(spec, graph, owner)
    graph["edges"].pop()
    spec["factors"][0]["levels"] = [[ids[0]]]
    validate_factor_bindings(spec, graph, complete_skills=False)
    monkeypatch.setattr(
        "asaree.services.skill_factors.skill_service.get_skill",
        AsyncMock(return_value=SimpleNamespace(owner_id=owner, is_system=False)),
    )
    assert await preview_values(graph, None, owner, "agent") == {"Skills": [ids[0]]}


@pytest.mark.asyncio
async def test_empty_skill_factor_has_readable_design_impact_instead_of_error(monkeypatch):
    monkeypatch.setattr("asaree.services.design_generation.get_current_revision", AsyncMock(return_value=None))
    impact = await get_design_impact(
        AsyncMock(),
        experiment_id=uuid.uuid4(),
        design_spec={"factors": [{"name": "Skills", "level_type": "skill_selection", "levels": []}]},
    )
    assert impact.proposed_cell_count == 0
    assert impact.proposed_replicate_count == 0
