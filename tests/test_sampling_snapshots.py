"""Published sampling stays fixed across discovery changes and factor substitutions."""

import copy
import uuid

import pytest
from motoro.services.llm_service import _sampling_kwargs

from asaree.services import model_config, sampling_snapshots
from asaree.services.protocol_execution import _resolve_model_config, apply_factor_bindings
from asaree.services.protocol_graph_schema import functional_protocol_graph_hash


@pytest.fixture
def graph():
    return {
        "nodes": [
            {
                "id": "model",
                "type": "model_openrouter",
                "data": {
                    "config": {"provider": "openrouter", "model": "custom", "temperature": None, "effort": None},
                    "factor_bindings": {"config.effort": "Effort"},
                },
            },
            {"id": "agent", "type": "agent", "data": {}},
        ],
        "edges": [{"source": "model", "target": "agent", "targetHandle": "model"}],
    }


async def test_publication_pins_factor_values_defaults_and_provenance(monkeypatch, graph):
    owner = uuid.uuid4()
    original = copy.deepcopy(graph)
    # Discovery at publication supplies metadata absent from the mutable draft.
    capabilities = {
        "supports_temperature": True,
        "supports_effort": True,
        "default_temperature": 0.0,
        "effort_levels": ["high", "low", "none"],
        "default_effort": "none",
    }

    async def build(data, *, owner_id):
        assert owner_id == owner
        from motoro.schemas.agent import ModelConfig

        return ModelConfig(**data, resolved_capabilities=capabilities)

    monkeypatch.setattr(sampling_snapshots, "build_model_config", build)
    frozen = await sampling_snapshots.freeze_sampling(
        graph,
        design_spec={"factors": [{"name": "Effort", "levels": [None, "high", "low"]}]},
        owner_id=owner,
    )
    assert "sampling_snapshot" not in graph
    assert functional_protocol_graph_hash(graph) == functional_protocol_graph_hash(frozen)
    pins = frozen["sampling_snapshot"].values()
    assert any(pin["sampling"]["effort_source"] == "advertised_default" for pin in pins)
    assert all(pin["sampling"]["temperature_source"] == "advertised_default" for pin in pins)

    # Even if discovery becomes unavailable, production reads no live capabilities.
    async def unavailable(**kwargs):
        raise AssertionError("Published sampling must never rediscover defaults")

    monkeypatch.setattr(model_config, "resolve_model_capabilities", unavailable)
    from motoro.schemas.agent import ModelConfig

    for effort in [None, "high", "low"]:
        patched = apply_factor_bindings(frozen, {"Effort": effort})
        config = ModelConfig(**_resolve_model_config(patched, "agent"))
        await model_config._resolve_capabilities(config, db=None, owner_id=owner)
        assert _sampling_kwargs(config, "openrouter/custom") == {
            "temperature": 0.0,
            "reasoning_effort": effort if effort is not None else "none",
            "allowed_openai_params": ["reasoning_effort"],
        }
    assert graph == original


async def test_unknown_published_treatment_cannot_fall_back_to_live_discovery(graph):
    graph["sampling_snapshot"] = {}
    with pytest.raises(ValueError, match="Republish"):
        _resolve_model_config(graph, "agent")


def test_legacy_graphs_remain_readable(graph):
    assert _resolve_model_config(graph, "agent") == graph["nodes"][0]["data"]["config"]


@pytest.mark.parametrize("node_type", ["persona", "step"])
async def test_publication_without_model_nodes_preserves_graph_shape(node_type):
    graph = {"nodes": [{"id": "node", "type": node_type, "data": {}}], "edges": []}
    frozen = await sampling_snapshots.freeze_sampling(graph, design_spec=None, owner_id=uuid.uuid4())
    assert frozen == graph
    assert frozen is not graph


async def test_empty_model_config_is_frozen_using_the_same_defaults_as_execution(monkeypatch, graph):
    from motoro.schemas.agent import ModelConfig
    from motoro.services.model_capabilities import get_capabilities

    graph["nodes"][0]["data"] = {"config": {}}
    calls = []

    async def build(data, **kwargs):
        calls.append(data)
        config = ModelConfig(**data)
        config.resolved_capabilities = get_capabilities(config.model)
        return config

    monkeypatch.setattr(sampling_snapshots, "build_model_config", build)
    frozen = await sampling_snapshots.freeze_sampling(graph, design_spec=None, owner_id=uuid.uuid4())
    config = ModelConfig(**_resolve_model_config(frozen, "agent"))
    assert config.model == ModelConfig().model
    assert config.resolved_capabilities is not None
    assert _sampling_kwargs(config, config.model) == _sampling_kwargs(ModelConfig(), config.model)
    assert calls == [{}]
    assert graph["nodes"][0]["data"]["config"] == {}


async def test_whole_model_factor_freezes_each_models_defaults_and_fallbacks(monkeypatch, graph):
    from motoro.schemas.agent import ModelConfig

    first = {
        "provider": "openai",
        "model": "first",
        "resolved_capabilities": {
            "supports_temperature": True,
            "supports_effort": False,
            "default_temperature": 0.0,
        },
    }
    second = {
        "provider": "openai",
        "model": "second",
        "temperature": 0.7,
        "resolved_capabilities": {
            "supports_temperature": True,
            "supports_effort": True,
            "effort_levels": ["high", "low"],
        },
        "fallback_models": [first],
    }
    graph["nodes"][0]["data"] = {"config": first, "factor_bindings": {"config": "Models"}}

    async def build(data, **kwargs):
        return ModelConfig(**data)

    monkeypatch.setattr(sampling_snapshots, "build_model_config", build)
    frozen = await sampling_snapshots.freeze_sampling(
        graph,
        design_spec={"factors": [{"name": "Models", "levels": [first, second]}]},
        owner_id=uuid.uuid4(),
    )
    assert len(frozen["sampling_snapshot"]) == 2
    selected = apply_factor_bindings(frozen, {"Models": second})
    config = ModelConfig(**_resolve_model_config(selected, "agent"))
    assert _sampling_kwargs(config, "second") == {"temperature": 0.7, "reasoning_effort": "low"}
    assert _sampling_kwargs(config.fallback_models[0], "first") == {"temperature": 0.0}
    provenance = next(pin for pin in frozen["sampling_snapshot"].values() if pin["config"]["model"] == "second")
    assert provenance["sampling"]["temperature_source"] == "explicit"
    assert provenance["sampling"]["effort_source"] == "application_fallback"
    assert provenance["fallbacks"][0]["sampling"]["temperature_source"] == "advertised_default"
