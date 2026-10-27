"""Pin sampling values and their provenance for every published model configuration."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import uuid
from typing import Any

from motoro.schemas.agent import ModelConfig
from motoro.services.sampling import resolve_sampling

from asaree.services.model_config import build_model_config

_SNAPSHOT_KEY = "sampling_snapshot"


def _config_key(config: dict[str, Any]) -> str:
    canonical = json.dumps({key: value for key, value in config.items() if value is not None}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _pin_config(config: ModelConfig) -> dict[str, Any]:
    assert config.resolved_capabilities is not None
    effective = resolve_sampling(
        config.resolved_capabilities, temperature=config.temperature, effort=config.effort, model=config.model
    )
    pinned = config.model_dump(mode="json")
    pinned["temperature"] = effective.temperature
    pinned["effort"] = effective.effort
    fallbacks = [_pin_config(fallback) for fallback in config.fallback_models]
    pinned["fallback_models"] = [fallback["config"] for fallback in fallbacks]
    return {"config": pinned, "sampling": effective.model_dump(mode="json"), "fallbacks": fallbacks}


async def freeze_sampling(
    graph: dict[str, Any], *, design_spec: dict[str, Any] | None, owner_id: uuid.UUID
) -> dict[str, Any]:
    """Freeze defaults after factor substitution, without editing the draft.

    Only factors bound to each Model are combined, avoiding a Cartesian product
    over unrelated experimental factors. The snapshot lives outside node data so
    it does not make an otherwise unchanged draft appear unpublished.
    """
    from asaree.services.protocol_execution import apply_factor_bindings

    frozen = copy.deepcopy(graph)
    frozen.pop(_SNAPSHOT_KEY, None)
    snapshots: dict[str, Any] = {}
    factors = {factor["name"]: factor.get("levels") or [] for factor in (design_spec or {}).get("factors") or []}
    for node in frozen.get("nodes") or []:
        if not str(node.get("type", "")).startswith(("model_", "llm_")):
            continue
        data = node.get("data") or {}
        names = sorted(set((data.get("factor_bindings") or {}).values()) & factors.keys())
        selections = (
            dict(zip(names, levels, strict=True)) for levels in itertools.product(*(factors[name] for name in names))
        )
        for selection in itertools.chain([{}], selections):
            patched = apply_factor_bindings({"nodes": [node], "edges": []}, selection)
            config = patched["nodes"][0].get("data", {}).get("config") or {}
            # An empty Model config still executes with ModelConfig's defaults.
            # Freeze it through the same construction path as an explicit model.
            key = _config_key(config)
            if key not in snapshots:
                snapshots[key] = _pin_config(await build_model_config(config, owner_id=owner_id))
    if snapshots:
        frozen[_SNAPSHOT_KEY] = snapshots
    return frozen


def published_model_config(graph: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Legacy/draft graphs resolve live; newly published graphs must use their pin."""
    snapshots = graph.get(_SNAPSHOT_KEY)
    if snapshots is None:
        return config
    snapshot = snapshots.get(_config_key(config))
    if snapshot is None:
        raise ValueError("This model configuration has no published sampling snapshot. Republish the experiment.")
    return copy.deepcopy(snapshot["config"])
