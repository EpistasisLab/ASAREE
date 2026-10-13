"""Knowledge treatments cover both bundles and documents without mutating nodes."""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.knowledge_factors import test_knowledge_factor_values as preview_values
from asaree.services.knowledge_factors import validate_knowledge_factors
from asaree.services.protocol_execution import _build_user_input, _resolve_knowledge_config, apply_factor_bindings


def knowledge_graph(mode="knowledge_selection"):
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    levels = [[value] for value in ids] if mode == "knowledge_selection" else [ids, []]
    graph = {
        "nodes": [
            {
                "id": "agent",
                "type": "agent",
                "data": {
                    "factor_bindings": {"knowledge_selection": "Knowledge"},
                    "knowledge_selection": levels[0],
                    "knowledge_factor_mode": mode,
                },
            },
            *[
                {
                    "id": f"source-{index}",
                    "type": kind,
                    "data": {
                        "config": {
                            key: ids[index],
                            "server_name": f"server-{index}",
                            "tool_names": ["read_concept"],
                            "enabled": False,
                        }
                    },
                }
                for index, (kind, key) in enumerate(
                    [
                        ("okf_bundle", "bundle_id"),
                        ("okf_document", "document_id"),
                    ]
                )
            ],
        ],
        "edges": [{"source": f"source-{index}", "target": "agent", "targetHandle": "knowledge"} for index in range(2)],
    }
    return graph, {"factors": [{"name": "Knowledge", "level_type": mode, "levels": levels}]}, ids


@pytest.mark.parametrize("mode", ["knowledge_selection", "knowledge_toggle"])
def test_treatments_override_disabled_sources_and_preserve_graph(mode):
    graph, spec, ids = knowledge_graph(mode)
    original = copy.deepcopy(graph)
    validate_factor_bindings(spec, graph)
    for selected in [[ids[0]], [ids[1]]] if mode == "knowledge_selection" else [ids, []]:
        resolved = _resolve_knowledge_config(apply_factor_bindings(graph, {"Knowledge": selected}), "agent")
        assert resolved["server_names"] == [f"server-{index}" for index, value in enumerate(ids) if value in selected]
        assert resolved["tool_names"] == [
            f"server-{index}.read_concept" for index, value in enumerate(ids) if value in selected
        ]
    assert graph == original


@pytest.mark.parametrize("mode", ["knowledge_selection", "knowledge_toggle"])
@pytest.mark.parametrize("enabled", [False, True])
def test_prompt_only_describes_selected_knowledge_sources(mode, enabled):
    graph, _, ids = knowledge_graph(mode)
    for index, source in enumerate(graph["nodes"][1:]):
        source["data"]["config"].update(
            enabled=enabled,
            document_title=f"Knowledge source {index}",
            document_description=f"Description {index}",
            document_tags=[f"tag-{index}"],
        )
    original = copy.deepcopy(graph)
    choices = [[ids[0]], [ids[1]]] if mode == "knowledge_selection" else [ids, []]
    for selected in choices:
        patched = apply_factor_bindings(graph, {"Knowledge": selected})
        prompt = _build_user_input(patched["nodes"][0], patched, {})
        for index, source_id in enumerate(ids):
            for metadata in [f"Knowledge source {index}", f"Description {index}", f"tag-{index}"]:
                assert (metadata in prompt) is (source_id in selected)
    assert graph == original


@pytest.mark.parametrize("enabled", [False, True])
def test_individual_treatment_changes_only_one_source(enabled):
    graph, _, _ = knowledge_graph()
    graph["nodes"][0]["data"] = {}
    graph["nodes"][1]["data"]["factor_bindings"] = {"config.enabled": "Enabled"}
    graph["nodes"][2]["data"]["config"]["enabled"] = True
    resolved = _resolve_knowledge_config(apply_factor_bindings(graph, {"Enabled": enabled}), "agent")
    assert resolved["server_names"] == (["server-0", "server-1"] if enabled else ["server-1"])


@pytest.mark.parametrize("change", ["incomplete", "duplicate", "conflict", "unbound", "invalid-toggle"])
def test_invalid_design_blocks_generation(change):
    graph, spec, ids = knowledge_graph()
    if change == "incomplete":
        graph["edges"].pop()
        spec["factors"][0]["levels"] = [[ids[0]]]
    elif change == "duplicate":
        spec["factors"][0]["levels"] = [[ids[0]], [ids[0]]]
    elif change == "conflict":
        graph["nodes"][1]["data"]["factor_bindings"] = {"config.enabled": "Enabled"}
    elif change == "unbound":
        graph["nodes"][0]["data"]["factor_bindings"] = {}
    else:
        graph, spec, ids = knowledge_graph("knowledge_toggle")
        spec["factors"][0]["levels"] = [[ids[0]], []]
    with pytest.raises(ValueError):
        validate_factor_bindings(spec, graph)


@pytest.mark.asyncio
async def test_availability_and_preview_scope(monkeypatch):
    graph, spec, ids = knowledge_graph()
    owner = uuid.uuid4()
    monkeypatch.setattr("asaree.services.knowledge_factors.okf_bundles.list_bundles", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "asaree.services.knowledge_factors.okf_documents.list_documents",
        AsyncMock(return_value=[SimpleNamespace(id=ids[1])]),
    )
    assert await preview_values(graph, {"agent": ids[1]}, owner) == {"Knowledge": [ids[1]]}
    with pytest.raises(ValueError, match="unavailable"):
        await validate_knowledge_factors(spec, graph, owner)
    with pytest.raises(ValueError, match="unavailable"):
        await preview_values(graph, None, owner)
    with pytest.raises(ValueError, match="belong"):
        await preview_values(graph, {"other": ids[1]}, owner)
    graph["nodes"][0]["data"].update(knowledge_factor_mode="knowledge_toggle", knowledge_selection=[])
    assert await preview_values(graph, None, owner) == {"Knowledge": []}
