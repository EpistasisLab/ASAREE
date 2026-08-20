"""asaree-sklearn-core — shared, MCP-free domain computation for the asaree-sklearn family.

Pure sklearn/xgboost/statsmodels computation only. The on-disk versioned
workspace and context-driven matrix resolution moved to ``asaree-workspace-core``
(ASAREE owns the workspace and the registered dataset it seeds from) — the
per-domain MCP servers (issue #1457) depend on both packages directly.
"""

from __future__ import annotations

from . import dc, eda, fs, fte, model, provenance, stats
from .artifacts import (
    DatasetArtifact,
    DomainFixerArtifact,
    FeatureRecipeArtifact,
    ImputerArtifact,
    ModelArtifact,
    PreprocessorArtifact,
    RECIPE_OPS,
    SelectorArtifact,
    apply_recipe_entry,
)
from .errors import ComputeError
from .parsing import parse_json_list, unwrap_json_list

__all__ = [
    # submodules (compute buckets)
    "dc",
    "eda",
    "fs",
    "fte",
    "model",
    "provenance",
    "stats",
    # artifacts
    "DatasetArtifact",
    "DomainFixerArtifact",
    "FeatureRecipeArtifact",
    "ImputerArtifact",
    "ModelArtifact",
    "PreprocessorArtifact",
    "RECIPE_OPS",
    "SelectorArtifact",
    "apply_recipe_entry",
    # errors
    "ComputeError",
    # parsing
    "parse_json_list",
    "unwrap_json_list",
]
