from __future__ import annotations

from copy import deepcopy

from asaree.services.protocol_graph_schema import functional_protocol_graph_hash, normalize_protocol_graph


def test_normalize_protocol_graph_rewrites_only_model_schema_fields() -> None:
    graph = {
        "nodes": [
            {
                "id": "llm_openai-user-id",
                "type": "llm_openai",
                "data": {"label": "AI node", "config": {"prompt": "keep llm_openai and ai verbatim"}},
            }
        ],
        "edges": [
            {
                "id": "ai-edge-id",
                "source": "llm_openai-user-id",
                "target": "agent",
                "sourceHandle": "llm",
                "targetHandle": "ai",
            }
        ],
    }

    normalized = normalize_protocol_graph(graph)

    assert normalized["nodes"][0] == {
        "id": "llm_openai-user-id",
        "type": "model_openai",
        "data": {"label": "AI node", "config": {"prompt": "keep llm_openai and ai verbatim"}},
    }
    assert normalized["edges"][0] == {
        "id": "ai-edge-id",
        "source": "llm_openai-user-id",
        "target": "agent",
        "sourceHandle": "model",
        "targetHandle": "model",
    }
    assert graph["nodes"][0]["type"] == "llm_openai"
    assert graph["edges"][0]["targetHandle"] == "ai"


def test_normalize_protocol_graph_preserves_canonical_graph() -> None:
    graph = {
        "nodes": [{"id": "model", "type": "model_local", "data": {}}],
        "edges": [{"source": "model", "target": "agent", "targetHandle": "model"}],
    }

    assert normalize_protocol_graph(graph) == graph


def test_functional_hash_ignores_canvas_only_state_and_collection_order() -> None:
    first = {
        "nodes": [
            {
                "id": "agent",
                "type": "agent",
                "position": {"x": 10, "y": 20},
                "selected": True,
                "measured": {"width": 288, "height": 180},
                "data": {"label": "Agent", "config": {"system_prompt": "Answer carefully"}},
            },
            {"id": "model", "type": "model_openai", "position": {"x": 30, "y": 40}, "data": {}},
        ],
        "edges": [
            {
                "id": "edge-one",
                "source": "model",
                "target": "agent",
                "sourceHandle": "model",
                "targetHandle": "model",
                "selected": True,
            }
        ],
        "viewport": {"x": 100, "y": 200, "zoom": 1.5},
    }
    second = {
        "nodes": [
            {"id": "model", "type": "model_openai", "position": {"x": 900, "y": 800}, "data": {}},
            {
                "id": "agent",
                "type": "agent",
                "position": {"x": -10, "y": -20},
                "dragging": True,
                "data": {"config": {"system_prompt": "Answer carefully"}, "label": "Agent"},
            },
        ],
        "edges": [
            {
                "id": "replacement-ui-id",
                "source": "model",
                "target": "agent",
                "sourceHandle": "model",
                "targetHandle": "model",
            }
        ],
    }

    assert functional_protocol_graph_hash(first) == functional_protocol_graph_hash(second)


def test_functional_hash_changes_for_node_configuration_and_edge_reconnection() -> None:
    graph = {
        "nodes": [
            {"id": "agent-a", "type": "agent", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
            {"id": "agent-b", "type": "agent", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
        ],
        "edges": [{"id": "edge", "source": "agent-a", "target": "agent-b"}],
    }
    configured = deepcopy(graph)
    configured["nodes"][0]["data"]["config"]["system_prompt"] = "Changed"
    reconnected = deepcopy(graph)
    reconnected["edges"][0]["source"] = "agent-b"

    assert functional_protocol_graph_hash(configured) != functional_protocol_graph_hash(graph)
    assert functional_protocol_graph_hash(reconnected) != functional_protocol_graph_hash(graph)
