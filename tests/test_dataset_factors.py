"""Dataset connector factors control run inputs without changing reusable nodes."""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from asaree.services.dataset_factors import test_dataset_factor_values as preview_values
from asaree.services.dataset_factors import validate_dataset_factors
from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.protocol_execution import (
    _ambient_meta_for,
    _build_user_input,
    _resolve_dataset_configs,
    _resolve_dataset_tool_config,
    apply_factor_bindings,
)


def dataset_graph(mode="dataset_selection"):
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    levels = [[value] for value in ids] if mode == "dataset_selection" else [ids, []]
    graph = {
        "nodes": [
            {
                "id": "agent",
                "type": "agent",
                "data": {
                    "factor_bindings": {"dataset_selection": "Datasets"},
                    "dataset_selection": levels[0],
                    "dataset_factor_mode": mode,
                },
            },
            *[
                {
                    "id": f"dataset-{index}",
                    "type": "dataset",
                    "data": {
                        "config": {
                            "dataset_id": value,
                            "dataset_name": f"Dataset {index}",
                            "enabled": False,
                        }
                    },
                }
                for index, value in enumerate(ids)
            ],
        ],
        "edges": [{"source": f"dataset-{index}", "target": "agent", "targetHandle": "dataset"} for index in range(2)],
    }
    spec = {"factors": [{"name": "Datasets", "level_type": mode, "levels": levels}]}
    return graph, spec, ids


@pytest.mark.parametrize("mode", ["dataset_selection", "dataset_toggle"])
def test_cell_inputs_override_disabled_nodes_without_mutating_graph(mode):
    graph, spec, ids = dataset_graph(mode)
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    for selected in [[ids[1]]] if mode == "dataset_selection" else [ids, []]:
        patched = apply_factor_bindings(graph, {"Datasets": selected})
        assert [config["dataset_id"] for config in _resolve_dataset_configs(patched, "agent")] == selected
    assert graph == original


def test_shared_dataset_remains_independently_disabled_for_other_agent():
    graph, spec, ids = dataset_graph("dataset_toggle")
    graph["nodes"].append({"id": "other", "type": "agent", "data": {}})
    graph["edges"].append({"source": "dataset-0", "target": "other", "targetHandle": "resource"})
    validate_factor_bindings(spec, graph)
    patched = apply_factor_bindings(graph, {"Datasets": ids})
    assert len(_resolve_dataset_configs(patched, "agent")) == 2
    assert _resolve_dataset_configs(patched, "other") == []


@pytest.mark.parametrize("enabled", [False, True])
def test_individual_on_off_controls_dataset_prompt_metadata_and_implicit_tools(enabled):
    graph, _, _ = dataset_graph()
    graph["nodes"][0]["data"] = {"label": "Agent", "config": {"goal": "Analyze the available data"}}
    graph["edges"] = graph["edges"][:1]
    graph["nodes"][1]["data"]["factor_bindings"] = {"config.enabled": "Enabled"}
    patched = apply_factor_bindings(graph, {"Enabled": enabled})
    prompt = _build_user_input(
        patched["nodes"][0], patched, {}, experiment_id=uuid.uuid4(), effective_cell_label="cell"
    )
    assert ("Dataset 0" in prompt) is enabled
    assert ("Dataset context:" in prompt) is enabled
    assert bool(_ambient_meta_for(patched, "agent").get("dataset_names")) is enabled
    assert bool(_resolve_dataset_tool_config(patched, "agent")["tool_names"]) is enabled


@pytest.mark.parametrize("mode", ["dataset_selection", "dataset_toggle"])
def test_connector_selection_filters_dataset_prompt_metadata_and_tools(mode):
    graph, _, ids = dataset_graph(mode)
    graph["nodes"][0]["data"]["config"] = {"goal": "Analyze the available data"}
    choices = [[ids[0]], [ids[1]]] if mode == "dataset_selection" else [ids, []]
    for selected in choices:
        patched = apply_factor_bindings(graph, {"Datasets": selected})
        prompt = _build_user_input(
            patched["nodes"][0], patched, {}, experiment_id=uuid.uuid4(), effective_cell_label="cell"
        )
        expected_names = [f"Dataset {index}" for index, value in enumerate(ids) if value in selected]
        assert _ambient_meta_for(patched, "agent").get("dataset_names", []) == expected_names
        for index, value in enumerate(ids):
            assert (f"Dataset {index}" in prompt) is (value in selected)
        assert bool(_resolve_dataset_tool_config(patched, "agent")["tool_names"]) is bool(selected)


@pytest.mark.parametrize("mode", ["dataset_selection", "dataset_toggle"])
def test_rejects_individual_bindings_and_row_inputs(mode):
    graph, spec, _ = dataset_graph(mode)
    graph["nodes"][1]["data"]["factor_bindings"] = {"config.enabled": "Enabled"}
    with pytest.raises(ValueError, match="individual dataset factor bindings"):
        validate_factor_bindings(spec, graph)
    graph["nodes"][1]["data"].pop("factor_bindings")
    graph["edges"][0]["data"] = {"dataset_input": {"mode": "per_row", "columns": ["question"]}}
    with pytest.raises(ValueError, match="Whole dataset"):
        validate_factor_bindings(spec, graph)


@pytest.mark.parametrize("levels", ["partial", "duplicate", "foreign"])
def test_rejects_invalid_toggle_levels(levels):
    graph, spec, ids = dataset_graph("dataset_toggle")
    spec["factors"][0]["levels"] = {
        "partial": [[ids[0]], []],
        "duplicate": [ids + [ids[0]], []],
        "foreign": [[ids[0], str(uuid.uuid4())], []],
    }[levels]
    with pytest.raises(ValueError, match="every connected dataset or none"):
        validate_factor_bindings(spec, graph)


@pytest.mark.asyncio
async def test_previews_preserve_choices_and_reject_unavailable_or_foreign_datasets(monkeypatch):
    graph, spec, ids = dataset_graph()
    owner = uuid.uuid4()
    db = AsyncMock()
    lookup = AsyncMock(return_value=SimpleNamespace(owner_id=owner))
    monkeypatch.setattr("asaree.services.dataset_factors.get_dataset", lookup)
    await validate_dataset_factors(spec, graph, owner, db)
    assert await preview_values(graph, {"agent": ids[1]}, owner, db) == {"Datasets": [ids[1]]}
    with pytest.raises(ValueError, match="available connected dataset"):
        await preview_values(graph, {"agent": str(uuid.uuid4())}, owner, db)
    lookup.return_value = SimpleNamespace(owner_id=uuid.uuid4())
    with pytest.raises(ValueError, match="unavailable dataset"):
        await preview_values(graph, None, owner, db)
    lookup.return_value = None
    with pytest.raises(ValueError, match="unavailable dataset"):
        await validate_dataset_factors(spec, graph, owner, db)


@pytest.mark.asyncio
async def test_all_or_none_preview_uses_baseline_and_explicit_choices(monkeypatch):
    graph, _, ids = dataset_graph("dataset_toggle")
    owner, db = uuid.uuid4(), AsyncMock()
    monkeypatch.setattr(
        "asaree.services.dataset_factors.get_dataset", AsyncMock(return_value=SimpleNamespace(owner_id=owner))
    )
    assert await preview_values(graph, None, owner, db) == {"Datasets": ids}
    assert await preview_values(graph, {"agent": "none"}, owner, db) == {"Datasets": []}
    graph["nodes"][0]["data"]["dataset_selection"] = []
    assert await preview_values(graph, None, owner, db) == {"Datasets": []}
