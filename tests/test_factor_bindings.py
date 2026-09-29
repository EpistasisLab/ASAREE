"""Pure readiness checks joining a factorial design to a protocol graph."""

from __future__ import annotations

import pytest

from asaree.services.factor_bindings import unbound_factor_names, validate_factor_bindings


def test_unbound_factors_are_reported_without_removing_them() -> None:
    design_spec = {"factors": [{"name": "Model", "levels": ["a", "b"]}, {"name": "Temperature", "levels": [0, 1]}]}
    graph = {
        "nodes": [
            {"id": "agent", "data": {"factor_bindings": {"config.model": "Model"}}},
        ],
        "edges": [],
    }

    assert unbound_factor_names(design_spec, graph) == ["Temperature"]
    with pytest.raises(ValueError, match="Temperature"):
        validate_factor_bindings(design_spec, graph)


def test_changed_published_value_must_be_reflected_in_bound_factor_levels() -> None:
    design_spec = {"factors": [{"name": "Prompt", "levels": ["original", "alternate"]}]}
    graph = {
        "nodes": [
            {
                "id": "agent",
                "data": {
                    "label": "Writer",
                    "config": {"system_prompt": "newly published"},
                    "factor_bindings": {"config.system_prompt": "Prompt"},
                },
            }
        ],
        "edges": [],
    }

    with pytest.raises(ValueError, match=r"published value does not match the first \(canvas baseline\) level"):
        validate_factor_bindings(design_spec, graph)


def test_bound_canvas_value_must_be_the_first_level() -> None:
    design_spec = {"factors": [{"name": "Prompt", "levels": ["baseline", "alternate"]}]}
    graph = {
        "nodes": [
            {
                "id": "agent",
                "data": {
                    "label": "Writer",
                    "config": {"system_prompt": "alternate"},
                    "factor_bindings": {"config.system_prompt": "Prompt"},
                },
            }
        ],
        "edges": [],
    }

    with pytest.raises(ValueError, match="canvas baseline"):
        validate_factor_bindings(design_spec, graph)


@pytest.mark.parametrize("levels", [[True, False], [False, True]])
def test_legacy_boolean_factor_accepts_either_level_order(levels: list[bool]) -> None:
    design_spec = {"factors": [{"name": "Gate", "levels": levels}]}
    graph = {
        "nodes": [
            {
                "id": "gate",
                "data": {"config": {"enabled": True}, "factor_bindings": {"config.enabled": "Gate"}},
            }
        ],
        "edges": [],
    }

    validate_factor_bindings(design_spec, graph)


def test_binding_to_missing_published_field_is_rejected() -> None:
    design_spec = {"factors": [{"name": "Prompt", "levels": ["one", "two"]}]}
    graph = {
        "nodes": [
            {
                "id": "agent",
                "data": {"label": "Writer", "config": {}, "factor_bindings": {"config.system_prompt": "Prompt"}},
            }
        ],
        "edges": [],
    }

    with pytest.raises(ValueError, match="no longer exists on the published canvas"):
        validate_factor_bindings(design_spec, graph)
