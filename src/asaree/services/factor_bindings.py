"""Validation of the bridge between declared factors and a protocol graph."""

from __future__ import annotations

from typing import Any


def unbound_factor_names(design_spec: dict[str, Any] | None, graph: dict[str, Any]) -> list[str]:
    declared = [str(f.get("name") or "").strip() for f in (design_spec or {}).get("factors") or []]
    bound = {
        str(name)
        for node in graph.get("nodes") or []
        for name in ((node.get("data") or {}).get("factor_bindings") or {}).values()
    }
    return [name for name in declared if name and name not in bound]


def validate_factor_bindings(design_spec: dict[str, Any] | None, graph: dict[str, Any]) -> None:
    missing = unbound_factor_names(design_spec, graph)
    if missing:
        labels = ", ".join(repr(name) for name in missing)
        raise ValueError(f"Rebind or remove unbound experimental factor(s): {labels}.")


__all__ = ["unbound_factor_names", "validate_factor_bindings"]
