"""Validation of the bridge between declared factors and a protocol graph."""

from __future__ import annotations

from typing import Any

_MISSING = object()


def _path_value(root: dict[str, Any], dotted_path: str) -> Any:
    value: Any = root
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


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

    factors = {
        str(factor.get("name") or "").strip(): factor
        for factor in (design_spec or {}).get("factors") or []
        if str(factor.get("name") or "").strip()
    }
    for node in graph.get("nodes") or []:
        data = node.get("data") or {}
        label = str(data.get("label") or node.get("type") or node.get("id") or "node")
        for field_path, raw_factor_name in (data.get("factor_bindings") or {}).items():
            factor_name = str(raw_factor_name).strip()
            factor = factors.get(factor_name)
            if factor is None:
                raise ValueError(
                    f"{label!r} field {field_path!r} is bound to undeclared factor {factor_name!r}. "
                    "Remove the stale binding or restore the factor."
                )
            published_value = _path_value(data, str(field_path))
            if published_value is _MISSING:
                raise ValueError(
                    f"{label!r} field {field_path!r}, bound to factor {factor_name!r}, no longer exists on the "
                    "published canvas. Remove the stale binding or restore the field."
                )
            levels = factor.get("levels") or []
            if not any(published_value == level for level in levels):
                raise ValueError(
                    f"{label!r} field {field_path!r} is bound to factor {factor_name!r}, but its published value "
                    "does not match any declared level. Update the factor levels and regenerate the design, or "
                    "remove the binding before publishing or running."
                )


__all__ = ["unbound_factor_names", "validate_factor_bindings"]
