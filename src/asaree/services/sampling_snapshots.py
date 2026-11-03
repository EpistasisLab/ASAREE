"""Pin sampling values and their provenance for every published model configuration."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import uuid
from datetime import UTC, datetime
from importlib.metadata import distribution
from typing import Any

from motoro.schemas.agent import ModelConfig
from motoro.services.llm_service import _sampling_kwargs
from motoro.services.model_capabilities import ModelCapabilities
from motoro.services.sampling import resolve_sampling

from asaree.services.model_config import build_model_config, resolve_discovery_context

_SNAPSHOT_KEY = "sampling_snapshot"
_SNAPSHOT_VERSION = 2


def sampling_policy_is_current(graph: dict[str, Any]) -> bool:
    if not any(str(node.get("type", "")).startswith(("model_", "llm_")) for node in graph.get("nodes") or []):
        return True
    snapshots = graph.get(_SNAPSHOT_KEY)
    if not isinstance(snapshots, dict) or not snapshots:
        return False
    return all(isinstance(pin, dict) and pin.get("schema_version") == _SNAPSHOT_VERSION for pin in snapshots.values())


def _config_key(config: dict[str, Any]) -> str:
    canonical = json.dumps({key: value for key, value in config.items() if value is not None}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _pin_config(config: ModelConfig) -> dict[str, Any]:
    package = distribution("motoro")
    origin = json.loads(package.read_text("direct_url.json") or "{}")
    effective = resolve_sampling(
        config.resolved_capabilities or config.model_metadata or ModelCapabilities(),
        temperature=config.temperature,
        effort=config.effort,
        model=config.model,
    )
    pinned = config.model_dump(mode="json")
    pinned["temperature"] = effective.temperature
    pinned["effort"] = effective.effort
    fallbacks = [_pin_config(fallback) for fallback in config.fallback_models]
    pinned["fallback_models"] = [fallback["config"] for fallback in fallbacks]
    return {
        "schema_version": _SNAPSHOT_VERSION,
        "motoro_version": package.version,
        "motoro_revision": origin.get("vcs_info", {}).get("commit_id"),
        "resolver": "provider-driven-v2",
        "frozen_at": datetime.now(UTC).isoformat(),
        "input": config.model_dump(mode="json"),
        "provider_request_sampling": _sampling_request(config),
        "provider_controls_unadvertised_defaults": effective.temperature is None or effective.effort is None,
        "config": pinned,
        "sampling": effective.model_dump(mode="json"),
        "fallbacks": fallbacks,
    }


def _sampling_request(config: ModelConfig) -> dict[str, Any]:
    kwargs = _sampling_kwargs(config, f"{config.provider.value}/{config.model}")
    native: dict[str, Any] = kwargs.get(
        "extra_body", {key: kwargs[key] for key in ("temperature", "reasoning_effort") if key in kwargs}
    )
    return native


def _check_override_origin(config: dict[str, Any]) -> None:
    if config.get("resolved_capabilities") is not None and config.get("resolved_capabilities_origin") != "override":
        raise ValueError(
            "Saved model capability rules have no recorded source. In the Model inspector, "
            "choose Automatic metadata or Keep saved override before publishing."
        )
    for fallback in config.get("fallback_models") or []:
        _check_override_origin(fallback)


async def _record_discovery_context(snapshot: dict[str, Any], config: ModelConfig, *, owner_id: uuid.UUID) -> None:
    snapshot["discovery_context"] = await resolve_discovery_context(config, owner_id=owner_id)
    for fallback_snapshot, fallback_config in zip(snapshot["fallbacks"], config.fallback_models, strict=True):
        await _record_discovery_context(fallback_snapshot, fallback_config, owner_id=owner_id)


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
                _check_override_origin(config)
                # Draft discovery copies are refreshed; only publications pin metadata.
                resolved = await build_model_config(config, owner_id=owner_id)
                snapshot = _pin_config(resolved)
                await _record_discovery_context(snapshot, resolved, owner_id=owner_id)
                snapshot["explicit_input"] = ModelConfig(**config).model_dump(mode="json", exclude_unset=True)
                snapshots[key] = snapshot
    if snapshots:
        frozen[_SNAPSHOT_KEY] = snapshots
    return frozen


def published_model_config(graph: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Legacy/draft graphs resolve live; newly published graphs must use their pin."""
    snapshots = graph.get(_SNAPSHOT_KEY)
    if snapshots is None:
        if graph.get("sampling_publication"):
            raise ValueError("This legacy publication requires its original Motoro runtime or a new publication.")
        return config
    snapshot = snapshots.get(_config_key(config))
    if snapshot is None:
        raise ValueError("This model configuration has no published sampling snapshot. Republish the experiment.")
    if snapshot.get("schema_version") != _SNAPSHOT_VERSION:
        raise ValueError(
            "This publication uses legacy sampling semantics. Replay it with its original Motoro runtime, "
            "or republish the experiment to use provider-driven sampling."
        )
    pinned = _validated_pinned_config(snapshot)
    pinned["sampling_snapshot_version"] = _SNAPSHOT_VERSION
    return pinned


def _validated_pinned_config(snapshot: dict[str, Any]) -> dict[str, Any]:
    pinned: dict[str, Any] = copy.deepcopy(snapshot["config"])
    # Guard omitted values as well as explicit ones against future resolver changes.
    current = _sampling_request(ModelConfig(**pinned))
    if current != snapshot["provider_request_sampling"]:
        raise ValueError(
            "Published sampling no longer matches its frozen request. Use the original runtime or republish."
        )
    fallback_configs = pinned.get("fallback_models") or []
    fallback_snapshots = snapshot.get("fallbacks") or []
    if len(fallback_configs) != len(fallback_snapshots):
        raise ValueError("Published fallback sampling no longer matches its snapshot. Republish the experiment.")
    for fallback_config, fallback_snapshot in zip(fallback_configs, fallback_snapshots, strict=True):
        if fallback_config != _validated_pinned_config(fallback_snapshot):
            raise ValueError("Published fallback sampling no longer matches its snapshot. Republish the experiment.")
    return pinned
