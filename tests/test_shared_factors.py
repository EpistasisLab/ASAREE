"""Shared canvas resources form one treatment for every connected recipient."""

import copy

import pytest

from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.protocol_execution import ProtocolValidationError, apply_factor_bindings, topological_order
from asaree.services.shared_factors import GROUP_SPECS, shared_test_selections, validate_shared_factor_groups


def shared_graph(kind):
    types, handles, path = GROUP_SPECS[kind]
    node_type = sorted(types)[0]
    handle = sorted(handles)[0]
    pattern = {"execution_pattern": "reason_act", "pattern_params": {"reason_act": {"max_iterations": 30}}}
    values = (
        [
            pattern,
            {
                "execution_pattern": "single_agent_baseline",
                "pattern_params": {"single_agent_baseline": {"max_iterations": 10}},
            },
        ]
        if kind == "pattern"
        else [["a"], ["b"]]
    )
    mode = "pattern" if kind == "pattern" else f"{kind}_selection"
    data = {path: values[0], "factor_bindings": {path: "Shared"}}
    if kind != "pattern":
        data[f"{kind}_factor_mode"] = mode
    graph = {
        "nodes": [{"id": id, "type": "agent", "data": copy.deepcopy(data)} for id in ["one", "two"]]
        + [
            {
                "id": id,
                "type": node_type,
                "data": {
                    "config": {"skill_id": id, "dataset_id": id, "bundle_id": id, "document_id": id, "code": "print(1)"}
                },
            }
            for id in (["a"] if kind == "pattern" else ["a", "b"])
        ],
        "edges": [
            {"source": source, "target": target, "targetHandle": handle}
            for source in (["a"] if kind == "pattern" else ["a", "b"])
            for target in ["one", "two"]
        ],
    }
    return graph, {"factors": [{"name": "Shared", "level_type": mode, "levels": values}]}, path


@pytest.mark.parametrize("kind", GROUP_SPECS)
def test_one_treatment_applies_to_every_recipient(kind):
    graph, spec, path = shared_graph(kind)
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    alternative = spec["factors"][0]["levels"][1]
    patched = apply_factor_bindings(graph, {"Shared": alternative})
    assert [node["data"][path] for node in patched["nodes"][:2]] == [alternative, alternative]
    assert graph == original
    assert shared_test_selections(graph, {"two": "b"}, kind) == {"one": "b", "two": "b"}
    assert shared_test_selections(graph, {"two": "b"}, kind, "two") == {"two": "b"}
    with pytest.raises(ValueError, match="different levels"):
        shared_test_selections(graph, {"one": "a", "two": "b"}, kind)


@pytest.mark.parametrize("kind", GROUP_SPECS)
@pytest.mark.parametrize(
    "change", ["disconnect", "unbound-recipient", "different-factor", "different-default", "disconnected-group"]
)
def test_wiring_edits_remain_drafts_but_block_generation_and_preview(kind, change):
    graph, spec, path = shared_graph(kind)
    if change == "disconnect":
        graph["edges"].pop()
    elif change == "unbound-recipient":
        graph["nodes"][1]["data"]["factor_bindings"] = {}
    elif change == "different-factor":
        graph["nodes"][1]["data"]["factor_bindings"][path] = "Other"
    elif change == "different-default":
        graph["nodes"][1]["data"][path] = spec["factors"][0]["levels"][1]
    else:
        graph["edges"] = []
    with pytest.raises(ValueError, match="Shared"):
        validate_factor_bindings(spec, graph)
    with pytest.raises(ValueError, match="Shared"):
        shared_test_selections(graph, None, kind)
    with pytest.raises(ProtocolValidationError, match="Shared"):
        topological_order(graph)


@pytest.mark.parametrize("kind", [kind for kind in GROUP_SPECS if kind != "pattern"])
def test_asymmetric_group_is_invalid_regardless_of_source_order(kind):
    graph, _, _ = shared_graph(kind)
    graph["edges"].pop()
    for edges in [graph["edges"], list(reversed(graph["edges"]))]:
        with pytest.raises(ValueError, match="exactly the same Agents"):
            validate_shared_factor_groups({**graph, "edges": edges}, kind)
