from __future__ import annotations

from asaree.services.protocol_graph_schema import normalize_protocol_graph


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
