"""Published sampling stays fixed across discovery changes and factor substitutions."""

import copy
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from motoro.services.llm_service import _sampling_kwargs

from asaree.services import model_config, sampling_snapshots
from asaree.services.protocol_execution import _resolve_model_config, apply_factor_bindings
from asaree.services.protocol_graph_schema import functional_protocol_graph_hash
from asaree.services.protocol_revisions import is_draft_published


@pytest.fixture(autouse=True)
def discovery_context(monkeypatch):
    monkeypatch.setattr(
        sampling_snapshots,
        "resolve_discovery_context",
        AsyncMock(
            return_value={
                "provider": "openrouter",
                "model": "custom",
                "api_base": None,
                "credential_revision": "test-revision",
                "discovered_at": 123,
            }
        ),
    )


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
        from motoro.services.provider_types import ModelInfo

        return ModelConfig(
            **data,
            model_metadata=ModelInfo(
                id=data["model"],
                capabilities=capabilities,
                capability_sources={name: "provider" for name in capabilities},
            ),
        )

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

    monkeypatch.setattr(model_config, "resolve_model_metadata", unavailable)

    @asynccontextmanager
    async def session():
        yield None

    monkeypatch.setattr(model_config, "get_session", session)

    for effort in [None, "high", "low"]:
        patched = apply_factor_bindings(frozen, {"Effort": effort})
        config = await model_config.build_model_config(_resolve_model_config(patched, "agent"), owner_id=owner)
        assert _sampling_kwargs(config, "openrouter/custom") == {
            "extra_body": {
                "temperature": 0.0,
                "reasoning": {"effort": effort if effort is not None else "none"},
            },
            "drop_params": False,
        }
    assert graph == original


async def test_unknown_published_treatment_cannot_fall_back_to_live_discovery(graph):
    graph["sampling_snapshot"] = {}
    with pytest.raises(ValueError, match="Republish"):
        _resolve_model_config(graph, "agent")


def test_legacy_graphs_remain_readable(graph):
    assert _resolve_model_config(graph, "agent") == graph["nodes"][0]["data"]["config"]


def test_legacy_published_graph_cannot_silently_resolve_live(graph):
    graph["sampling_publication"] = True
    with pytest.raises(ValueError, match="original Motoro runtime"):
        _resolve_model_config(graph, "agent")


def test_unchanged_draft_can_publish_a_new_sampling_policy(graph):
    publication = SimpleNamespace(graph=copy.deepcopy(graph))
    protocol = SimpleNamespace(graph=graph)
    assert not is_draft_published(protocol, publication)
    publication.graph["sampling_snapshot"] = {"key": {"schema_version": 2}}
    assert is_draft_published(protocol, publication)


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
        "resolved_capabilities_origin": "override",
        "resolved_capabilities": {
            "supports_temperature": True,
            "supports_effort": False,
            "default_temperature": 0.0,
        },
    }
    second = {
        "provider": "openai",
        "model": "second",
        "resolved_capabilities_origin": "override",
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
    assert _sampling_kwargs(config, "second") == {"extra_body": {"temperature": 0.7}, "drop_params": False}
    assert _sampling_kwargs(config.fallback_models[0], "first") == {
        "extra_body": {"temperature": 0.0},
        "drop_params": False,
    }
    provenance = next(pin for pin in frozen["sampling_snapshot"].values() if pin["config"]["model"] == "second")
    assert provenance["sampling"]["temperature_source"] == "explicit"
    assert provenance["sampling"]["effort_source"] == "omitted"
    assert provenance["fallbacks"][0]["sampling"]["temperature_source"] == "capability_override"


def test_legacy_publication_requires_original_runtime_without_mutating_history(graph):
    original_config = graph["nodes"][0]["data"]["config"]
    graph["sampling_snapshot"] = {
        sampling_snapshots._config_key(original_config): {
            "config": {**original_config, "effort": "low"},
            "sampling": {"effort_source": "application_fallback"},
        }
    }
    original = copy.deepcopy(graph)
    with pytest.raises(ValueError, match="original Motoro runtime"):
        _resolve_model_config(graph, "agent")
    assert graph == original


async def test_ambiguous_draft_override_must_be_resolved_before_publication(graph):
    graph["nodes"][0]["data"]["config"]["resolved_capabilities"] = {"supports_temperature": True}
    with pytest.raises(ValueError, match="no recorded source"):
        await sampling_snapshots.freeze_sampling(graph, design_spec=None, owner_id=uuid.uuid4())
