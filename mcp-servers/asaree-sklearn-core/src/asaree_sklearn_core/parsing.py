"""Lenient coercion of LLM-supplied JSON payloads to lists.

Agents routinely confuse a tool's list argument with the wrapped report object
they also emit — e.g. passing ``{"engineering_recipe": [...]}`` to a
``recipe_json`` argument that expects the bare ``[...]``. Hard-rejecting that
(``recipe_json must be a JSON list``) burns an agent turn on a purely structural
slip. These helpers unwrap the common shapes instead, matching the leniency
:func:`asaree_sklearn_core.dc.normalize_rule_list` already affords the DC stage.
"""

from __future__ import annotations

import json
from typing import Any


def unwrap_json_list(parsed: Any, *, prefer_keys: tuple[str, ...] = ()) -> list[Any]:
    """Coerce an already-parsed JSON value to a list.

    Accepts:
      - a bare list                       -> returned as-is
      - a dict wrapping the list in a key -> the wrapped list is unwrapped. A name
        in *prefer_keys* present with a list value wins (disambiguates a full
        report object with several array fields); otherwise the dict must hold
        exactly one list-valued value.

    Raises ``ValueError`` on any other shape (a scalar, or an ambiguous dict with
    no preferred key and multiple/zero arrays).
    """
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for k in prefer_keys:
            v = parsed.get(k)
            if isinstance(v, list):
                return v
        list_vals = [v for v in parsed.values() if isinstance(v, list)]
        if len(list_vals) == 1:
            return list_vals[0]
        raise ValueError(
            "expected a JSON array, or an object wrapping exactly one array; "
            f"got an object with keys {sorted(parsed)}"
        )
    raise ValueError(f"expected a JSON array; got {type(parsed).__name__}")


def parse_json_list(
    raw: str, *, arg_name: str, prefer_keys: tuple[str, ...] = ()
) -> tuple[list[Any] | None, str | None]:
    """Parse *raw* JSON and coerce it to a list via :func:`unwrap_json_list`.

    Returns ``(list, None)`` on success or ``(None, error)`` on failure. The error
    string names *arg_name* so an MCP tool can return it verbatim as
    ``{"error": ...}``.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"{arg_name} is not valid JSON: {e}"
    try:
        return unwrap_json_list(parsed, prefer_keys=prefer_keys), None
    except ValueError as e:
        return None, f"{arg_name}: {e}"
