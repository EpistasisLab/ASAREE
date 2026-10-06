from __future__ import annotations

import copy
import uuid

import pytest

from asaree.services.dataset_row_inputs import DatasetRowInputError, resolve_dataset_row_plan

DATASET_ID = "11111111-1111-4111-8111-111111111111"


def graph(*, configs=None, edges=None, extra_nodes=None):
    configs = configs or {"d": {"dataset_id": DATASET_ID, "dataset_name": "cohort"}}
    nodes = [{"id": node_id, "type": "dataset", "data": {"config": config}} for node_id, config in configs.items()]
    nodes.extend({"id": agent, "type": "agent", "data": {}} for agent in {e["target"] for e in edges or []})
    nodes.extend(extra_nodes or [])
    return {"nodes": nodes, "edges": edges or []}


def edge(source="d", target="a", *, handle="dataset", mode="per_row", columns=None, edge_id="e", **data):
    config = {"mode": mode, "columns": columns or ["question"]} if mode == "per_row" else {"mode": mode}
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "targetHandle": handle,
        "data": {"dataset_input": config, **data},
    }


def test_legacy_handles_and_order_are_preserved_without_mutation():
    edges = [edge(edge_id="first", handle="tool"), edge(edge_id="second", handle="resource", target="b")]
    value = graph(edges=edges)
    before = copy.deepcopy(value)

    plan = resolve_dataset_row_plan(value)

    assert plan == {
        "driver_dataset_id": DATASET_ID,
        "bindings": [
            {"edge_id": "first", "dataset_node_id": "d", "agent_node_id": "a", "columns": ["question"]},
            {"edge_id": "second", "dataset_node_id": "d", "agent_node_id": "b", "columns": ["question"]},
        ],
    }
    assert value == before


def test_disabled_source_is_ignored_but_its_edge_config_is_validated():
    value = graph(configs={"d": {"dataset_id": DATASET_ID, "enabled": False}}, edges=[edge()])
    assert resolve_dataset_row_plan(value) is None
    value["edges"][0]["data"]["dataset_input"] = None
    with pytest.raises(DatasetRowInputError, match="invalid_configuration"):
        resolve_dataset_row_plan(value)


def test_misplaced_dataset_input_is_rejected_even_when_disabled():
    value = graph(edges=[edge()])
    value["edges"][0]["source"] = "ordinary"
    value["nodes"].append({"id": "ordinary", "type": "script", "data": {}})
    with pytest.raises(DatasetRowInputError, match="misplaced_configuration"):
        resolve_dataset_row_plan(value)


def test_alias_nodes_with_same_uuid_can_drive_different_agent_views():
    value = graph(
        configs={"d1": {"dataset_id": DATASET_ID}, "d2": {"dataset_id": DATASET_ID}},
        edges=[edge("d1", columns=["question"]), edge("d2", target="b", columns=["grade"], edge_id="e2")],
    )
    plan = resolve_dataset_row_plan(value)
    assert plan is not None
    assert plan["driver_dataset_id"] == DATASET_ID
    assert [b["columns"] for b in plan["bindings"]] == [["question"], ["grade"]]


def test_two_drivers_and_mixed_access_are_rejected():
    other = str(uuid.uuid4())
    value = graph(
        configs={"d": {"dataset_id": DATASET_ID}, "d2": {"dataset_id": other}},
        edges=[edge(), edge("d2", target="b", edge_id="e2")],
    )
    with pytest.raises(DatasetRowInputError, match="multiple_drivers"):
        resolve_dataset_row_plan(value)
    value = graph(edges=[edge(), edge(mode="whole_dataset", edge_id="whole")])
    with pytest.raises(DatasetRowInputError, match="mixed_driver_modes"):
        resolve_dataset_row_plan(value)


def test_duplicate_edges_collapse_only_when_columns_match():
    value = graph(edges=[edge(), edge(edge_id="e2")])
    assert len(resolve_dataset_row_plan(value)["bindings"]) == 2
    value["edges"][1]["data"]["dataset_input"]["columns"] = ["answer"]
    with pytest.raises(DatasetRowInputError, match="conflicting_agent_columns"):
        resolve_dataset_row_plan(value)


def test_row_mode_requires_dataset_uuid_not_only_name():
    value = graph(configs={"d": {"dataset_name": "cohort"}}, edges=[edge()])
    with pytest.raises(DatasetRowInputError, match="invalid_dataset_id"):
        resolve_dataset_row_plan(value)


def test_prompt_factor_is_allowed_but_driver_factor_is_forbidden():
    value = graph(edges=[edge()])
    value["nodes"].append(
        {
            "id": "a",
            "type": "agent",
            "data": {
                "config": {"system_prompt": "base"},
                "factor_bindings": {"config.system_prompt": "Prompt"},
            },
        }
    )
    plan = resolve_dataset_row_plan(value, {"factors": [{"name": "Prompt", "levels": ["base", "other"]}]})
    assert plan is not None
    value["nodes"][0]["data"]["factor_bindings"] = {"config": "Dataset"}
    with pytest.raises(DatasetRowInputError, match="factorized_driver"):
        resolve_dataset_row_plan(value)


def test_factor_applied_graph_must_keep_driver_and_ordered_binding_columns():
    base = graph(edges=[edge()])
    plan = resolve_dataset_row_plan(base)
    changed = copy.deepcopy(base)
    changed["edges"][0]["data"]["dataset_input"]["columns"] = ["answer"]
    applied_plan = resolve_dataset_row_plan(changed)
    assert applied_plan is not None
    assert applied_plan["driver_dataset_id"] == plan["driver_dataset_id"]
    assert applied_plan["bindings"][0]["columns"] != plan["bindings"][0]["columns"]
    assert plan["bindings"][0]["columns"] == ["question"]
