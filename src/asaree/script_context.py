"""Stable access to datasets authorized for an ASAREE wired script.

The script runner publishes a short-lived JSON manifest and points this module
at it with ``ASAREE_RUN_CONTEXT``.  User scripts therefore do not need to know
the workspace ``state.json`` format or receive filesystem paths through an
agent/model argument.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CONTEXT_ENV = "ASAREE_RUN_CONTEXT"


class ScriptContextError(ValueError):
    """The wired-script runtime context is absent, invalid, or ambiguous."""


@dataclass(frozen=True)
class TrainingInput:
    """One authorized training input attached to the running script."""

    name: str
    path: Path
    target_column: str
    mode: str
    slot: str | None = None
    workspace_version: str | None = None


def _manifest() -> dict[str, Any]:
    context_path = os.environ.get(_CONTEXT_ENV, "")
    if not context_path:
        raise ScriptContextError("This process has no ASAREE wired-script runtime context.")
    try:
        payload = json.loads(Path(context_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScriptContextError(f"Could not read the ASAREE runtime context: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ScriptContextError("Unsupported ASAREE wired-script runtime context.")
    return payload


def training_inputs() -> tuple[TrainingInput, ...]:
    """Return every authorized training input attached to this script."""
    raw_inputs = _manifest().get("training_inputs", [])
    if not isinstance(raw_inputs, list):
        raise ScriptContextError("ASAREE runtime training_inputs must be a list.")
    resolved: list[TrainingInput] = []
    for item in raw_inputs:
        if not isinstance(item, dict):
            raise ScriptContextError("ASAREE runtime training input must be an object.")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ScriptContextError("ASAREE runtime training input has no path.")
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise ScriptContextError(f"Authorized training input does not exist: {path}")
        resolved.append(
            TrainingInput(
                name=str(item.get("name") or ""),
                path=path,
                target_column=str(item.get("target_column") or ""),
                mode=str(item.get("mode") or ""),
                slot=str(item["slot"]) if item.get("slot") is not None else None,
                workspace_version=(
                    str(item["workspace_version"]) if item.get("workspace_version") is not None else None
                ),
            )
        )
    return tuple(resolved)


def training_input(*, name: str | None = None, slot: str | None = None) -> TrainingInput:
    """Resolve one attached training input, requiring a selector if ambiguous."""
    inputs = training_inputs()
    matches = [item for item in inputs if (name is None or item.name == name) and (slot is None or item.slot == slot)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        requested = f" name={name!r}" if name is not None else f" slot={slot!r}" if slot is not None else ""
        raise ScriptContextError(f"No authorized training input matches{requested}.")
    choices = ", ".join(item.slot or item.name or "<unnamed>" for item in matches)
    raise ScriptContextError(f"Several training inputs are attached ({choices}); select by name or slot.")
