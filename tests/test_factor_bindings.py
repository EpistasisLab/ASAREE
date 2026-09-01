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
