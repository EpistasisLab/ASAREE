"""Generic result-provenance helpers (pure; no workspace/filesystem knowledge).

Lifted out of asaree-workspace-core's provenance module for asaree-sklearn-model:
these describe the SCORING RUN's own environment/output, not the on-disk dataset
version, so they belong with the domain computation, not the workspace format.
"""

from __future__ import annotations

from typing import Any


def env_provenance() -> dict[str, str | None]:
    """Versions of the packages that fit and score the model (stats brief Section 12).

    Captured in the scorer process, which is the authoritative environment for
    reproducibility — the notebook/client environment is irrelevant to the fit.
    """
    import importlib.metadata as _md

    versions: dict[str, str | None] = {}
    for dist in ("scikit-learn", "xgboost", "optuna", "numpy", "scipy", "pandas"):
        try:
            versions[dist] = _md.version(dist)
        except Exception:  # noqa: BLE001 — missing dist is reported as null, never fatal
            versions[dist] = None
    return versions


def np_default(o: Any) -> Any:
    """json.dumps default that coerces numpy scalars/arrays to native Python."""
    if hasattr(o, "item"):
        try:
            return o.item()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)
