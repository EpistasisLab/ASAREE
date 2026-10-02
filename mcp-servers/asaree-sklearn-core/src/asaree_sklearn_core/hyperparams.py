"""Coerce a hyperparameter payload into one a modeling script can run.

The payload contract ``run_model_script`` binds as ``hp``::

    {"search_space": [{"param": str, "low": num, "high": num}, ...],
     "fixed_params": {param: num, ...},
     "n_trials": int}

*param_spec* is the caller's closed vocabulary of tunable parameters and
their hard bounds (``{param: {"low": num, "high": num, ...}}``) -- it is data
the caller passes, not something this module knows, so the same sanitizer
serves any model family. A port of the spinal notebook's ``sanitize_payload``:
every suggestion outside the spec is DROPPED (the script's own default then
stands) and noted, so a malformed suggestion still scores instead of crashing
the script.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, TypeGuard

DEFAULT_N_TRIALS = 30
MAX_N_TRIALS = 100


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def _sanitize_n_trials(value: Any, notes: list[str], default: int) -> int:
    if not _is_number(value):
        notes.append(f"n_trials was not a number ({value!r}); defaulted to {default}")
        return default
    rounded = int(round(float(value)))
    if rounded < 1:
        notes.append(f"n_trials {value} < 1; raised to 1")
        return 1
    if rounded > MAX_N_TRIALS:
        notes.append(f"n_trials {value} > {MAX_N_TRIALS}; capped at {MAX_N_TRIALS}")
        return MAX_N_TRIALS
    return rounded


def sanitize_hyperparameter_payload(
    payload: Any,
    param_spec: Mapping[str, Mapping[str, Any]],
    *,
    default_n_trials: int = DEFAULT_N_TRIALS,
) -> tuple[dict[str, Any], list[str]]:
    """``(clean_payload, notes)``; notes is empty when the payload was already clean."""
    spec = param_spec
    notes: list[str] = []
    if not isinstance(payload, Mapping):
        notes.append(
            f"payload was not a JSON object ({type(payload).__name__}); scored with all-default hyperparameters"
        )
        return {"search_space": [], "fixed_params": {}, "n_trials": default_n_trials}, notes

    def in_bounds(name: str, value: float) -> bool:
        return float(spec[name]["low"]) <= value <= float(spec[name]["high"])

    clean_search: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_search = payload.get("search_space")
    if isinstance(raw_search, list):
        for index, entry in enumerate(raw_search):
            where = f"search_space[{index}]"
            if not isinstance(entry, Mapping):
                notes.append(f"dropped {where}: not an object")
                continue
            name = entry.get("param")
            if name is None:
                notes.append(
                    f'dropped {where}: missing the "param" key (keys: {sorted(entry)}) -> that param uses its default'
                )
                continue
            if name not in spec:
                notes.append(
                    f"dropped {where}: {name!r} is not a tunable parameter -> not passed to the model "
                    "(uses its default)"
                )
                continue
            if name in seen:
                notes.append(f"dropped {where}: {name!r} already tuned earlier (duplicate)")
                continue
            low, high = entry.get("low"), entry.get("high")
            if not _is_number(low) or not _is_number(high):
                notes.append(f"dropped {where} ({name!r}): non-numeric low/high -> uses its default")
                continue
            if low >= high:
                notes.append(
                    f"dropped {where} ({name!r}): needs low < high, got low={low}, high={high} -> uses its default"
                )
                continue
            if not in_bounds(name, low) or not in_bounds(name, high):
                notes.append(
                    f"dropped {where} ({name!r}): range [{low}, {high}] outside its hard bound "
                    f"[{spec[name]['low']}, {spec[name]['high']}] -> uses its default"
                )
                continue
            seen.add(name)
            clean_search.append({"param": name, "low": low, "high": high})
    elif raw_search is not None:
        notes.append(f"ignored search_space: not a list ({type(raw_search).__name__})")

    clean_fixed: dict[str, Any] = {}
    raw_fixed = payload.get("fixed_params")
    if isinstance(raw_fixed, Mapping):
        for name, value in raw_fixed.items():
            if name not in spec:
                notes.append(
                    f"dropped fixed_params[{name!r}]: not a settable parameter (harness-fixed or unknown) "
                    "-> the script's own default stands"
                )
                continue
            if name in seen:
                notes.append(
                    f"dropped fixed_params[{name!r}]: already tuned in search_space (cannot be both) "
                    "-> kept the tuned range"
                )
                continue
            if not _is_number(value):
                notes.append(f"dropped fixed_params[{name!r}]: non-numeric value {value!r} -> uses its default")
                continue
            if not in_bounds(name, value):
                notes.append(
                    f"dropped fixed_params[{name!r}]={value}: outside its hard bound "
                    f"[{spec[name]['low']}, {spec[name]['high']}] -> uses its default"
                )
                continue
            clean_fixed[name] = value
    elif raw_fixed is not None:
        notes.append(f"ignored fixed_params: not an object ({type(raw_fixed).__name__})")

    n_trials = _sanitize_n_trials(payload.get("n_trials"), notes, default_n_trials)
    if not clean_search and n_trials > 1:
        notes.append(f"no tunable parameters survived; n_trials reduced {n_trials} -> 1")
        n_trials = 1
    return {"search_space": clean_search, "fixed_params": clean_fixed, "n_trials": n_trials}, notes


def parse_param_spec(param_spec_json: str) -> dict[str, dict[str, Any]]:
    """Validate a ``param_spec_json`` argument; raises ``ValueError`` naming the problem."""
    spec = _loads_object(param_spec_json, "param_spec_json")
    for name, bounds in spec.items():
        if not isinstance(bounds, Mapping) or not _is_number(bounds.get("low")) or not _is_number(bounds.get("high")):
            raise ValueError(f"param_spec_json[{name!r}] needs numeric 'low' and 'high'")
        if bounds["low"] > bounds["high"]:
            raise ValueError(f"param_spec_json[{name!r}] has low > high")
    return {str(name): dict(bounds) for name, bounds in spec.items()}


def _loads_object(text: str, what: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be a JSON object, got {type(value).__name__}")
    return value
