"""asaree-sklearn-fte — feature-engineering MCP server (SF-FTE).

Thin FastMCP wrapper over :mod:`asaree_sklearn_core`. This server does NOT
import asaree_workspace_core — it only knows the ambient ``workspace_id``
(request ``_meta``) and one fixed convention for where its own disposable
scratch files live: plain ``train.parquet``/``test.parquet``/``meta.json``
under ``{ASAREE_DATASET_WORKSPACE_DIR}/{workspace_id}/.scratch/fte/``.
asaree-workspace prepares that directory (``open_workspace(..., stage="fte")``)
and is the only thing that later reads it back out and promotes it into the
permanent versioned tree (``accept_stage``).

Both tools fit TRAIN-only and write the engineered/encoded pair back to the
scratch dir. The FTE input is the accepted ``v1_dc`` output. The two tools
chain via the scratch working copy: ``build_features`` writes its result,
then ``fit_preprocessor`` reads that and overwrites it with the fully
encoded, model-ready matrix.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from mcp.server import FastMCP
from mcp.server.fastmcp import Context

import asaree_sklearn_core as core
from asaree_sklearn_core import fte

mcp = FastMCP("asaree-sklearn-fte")

STAGE = "fte"

# Mirrors motoro.mcp.adapters.META_KEY_WORKSPACE_ID as a literal string,
# not an import — this server has no dependency on Motoro or ASAREE's
# own packages, only on the MCP request _meta contract itself.
_META_KEY_WORKSPACE_ID = "motoro.workspace_id"


class ScratchNotReadyError(Exception):
    """Raised when this stage's scratch files are missing, empty, or unreadable."""


def _workspace_id_from_ctx(explicit: str, ctx: Context | None) -> str:
    if explicit.strip():
        return explicit.strip()
    if ctx is not None:
        try:
            meta = ctx.request_context.meta
            extra = getattr(meta, "model_extra", None) or {}
            wid = extra.get(_META_KEY_WORKSPACE_ID)
            if isinstance(wid, str) and wid:
                return wid
        except Exception:  # noqa: BLE001 — no ambient meta available outside a request
            pass
    raise ScratchNotReadyError("workspace_id missing: pass it explicitly or via ambient _meta")


def _scratch_dir(workspace_id: str) -> Path:
    """This stage's scratch directory — the entire contract shared with
    asaree-workspace: one env var plus a fixed relative path, no shared code."""
    root = os.environ.get("ASAREE_DATASET_WORKSPACE_DIR", "./data/workspaces")
    return Path(root).resolve() / workspace_id / ".scratch" / STAGE


def _read_scratch(
    workspace_id: str,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series, str]:
    scratch = _scratch_dir(workspace_id)
    train_path = scratch / "train.parquet"
    meta_path = scratch / "meta.json"
    if not train_path.is_file() or not meta_path.is_file():
        raise ScratchNotReadyError(
            f"no scratch input for {workspace_id!r}/{STAGE} — call "
            "open_workspace(..., stage='fte') first."
        )
    target = json.loads(meta_path.read_text())["target_column"]
    train_df = pd.read_parquet(train_path)
    test_df = pd.read_parquet(scratch / "test.parquet")
    X_train = train_df.drop(columns=[target]).reset_index(drop=True)  # noqa: N806
    y_train = train_df[target].reset_index(drop=True)
    X_test = test_df.drop(columns=[target]).reset_index(drop=True)  # noqa: N806
    y_test = test_df[target].reset_index(drop=True)
    return X_train, y_train, X_test, y_test, target


def _write_scratch(
    workspace_id: str,
    target: str,
    X_train: pd.DataFrame,  # noqa: N803
    y_train: pd.Series,
    X_test: pd.DataFrame,  # noqa: N803
    y_test: pd.Series,
) -> None:
    scratch = _scratch_dir(workspace_id)
    train_df = X_train.copy()
    train_df[target] = y_train.to_numpy()
    test_df = X_test.copy()
    test_df[target] = y_test.to_numpy()
    train_df.to_parquet(scratch / "train.parquet", index=False)
    test_df.to_parquet(scratch / "test.parquet", index=False)


def _merge_learned(workspace_id: str, new_keys: dict[str, Any]) -> None:
    path = _scratch_dir(workspace_id) / "learned.json"
    merged: dict[str, Any] = {}
    if path.is_file():
        try:
            merged = json.loads(path.read_text())
        except json.JSONDecodeError:
            merged = {}
    merged.update(new_keys)
    path.write_text(json.dumps(merged, default=str))


def _record_run_id(workspace_id: str, run_id: str) -> None:
    if run_id:
        (_scratch_dir(workspace_id) / "run_meta.json").write_text(json.dumps({"run_id": run_id}))


@mcp.tool()
def build_features(
    recipe_json: str,
    workspace_id: str = "",
    run_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Materialize an engineered-feature recipe on the FTE working matrix.

    Declares each engineered column as a typed recipe entry, freezes any train-derived
    statistic (bin edges, group aggregates) on the train fold, and materializes the
    columns on BOTH splits (the frozen statistic keeps them aligned).

    recipe_json MUST be a JSON array of entries (an object wrapping exactly one array
    under 'engineering_recipe'/'recipe'/'entries' is also accepted). Each entry:
    {"name": <new column>, "op": <one of the ops below>, "inputs": [<existing columns>],
    "params": {...}, "rationale"?}. Both "name" and "op" are REQUIRED, every "inputs"
    column must already exist in the matrix, and "op" must be one of these exact values
    (the set is closed — an unknown op is rejected, not silently run):
      meta-feature/composite (across a column group): count_nonzero,
        count_equal(params.value), count_threshold(params.op, params.value),
        count_missing, weighted_sum(params.weights aligned 1:1 with inputs),
        ratio(2 inputs: num, den), sum, mean, min, max, range, std, n_distinct,
        which_max, and, or, xor, nor, multiply
      single-column transform: threshold(params.op, params.value), is_missing,
        log1p, sqrt, square, abs
      statistic-bearing (auto-frozen from train): bin(params.n_bins or params.edges),
        group_agg(params.group_col + params.value_col + params.stat),
        frequency_encode(1 input; train category frequencies)
    params.op for threshold/count_threshold is one of gt/ge/lt/le/eq/ne.
    To leave a column unchanged, simply omit it — do not add a passthrough entry.

    Args:
        recipe_json: JSON array of recipe entries (see above).
        workspace_id: Optional explicit workspace id; else resolved from _meta.
        run_id: Optional ASAREE run id, recorded alongside this attempt's provenance.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, X_test, y_test, target = _read_scratch(wid)  # noqa: N806
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})

    raw_entries, err = core.parse_json_list(
        recipe_json, arg_name="recipe_json",
        prefer_keys=("engineering_recipe", "recipe", "entries"),
    )
    if err is not None:
        return json.dumps({"error": err})

    problems = fte.validate_recipe_entries(raw_entries, list(X_train.columns))
    if problems:
        return json.dumps({"error": "invalid recipe: " + "; ".join(problems)})

    try:
        recipe = fte.build_feature_recipe(X_train, raw_entries, wid)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return json.dumps({"error": f"recipe is malformed: {type(e).__name__}: {e}"})

    try:
        eng_train = recipe.apply(X_train)
        eng_test = recipe.apply(X_test)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return json.dumps({"error": f"recipe failed to materialize: {type(e).__name__}: {e}"})

    engineered = [e["name"] for e in recipe.entries]
    _write_scratch(wid, target, eng_train, y_train, eng_test, y_test)
    _merge_learned(
        wid,
        {
            "recipe": recipe.entries,
            "engineered_features": engineered,
            "feature_names_out": recipe.feature_names_out,
        },
    )
    _record_run_id(wid, run_id)

    return json.dumps(
        {
            "n_features_in": len(recipe.feature_names_in),
            "n_features_out": len(recipe.feature_names_out),
            "engineered_features": engineered,
            "note": "Engineered matrices written to this stage's scratch. Run "
            "fit_preprocessor next so the result is the fully encoded, numeric, "
            "model-ready matrix.",
        }
    )


@mcp.tool()
def fit_preprocessor(
    impute_strategy: str = "median",
    scale_method: str = "standard",
    encode_method: str = "none",
    encode_columns: str = "",
    encoding_map_json: str = "",
    workspace_id: str = "",
    run_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Fit an impute/scale/encode pipeline on the FTE working matrix.

    Fits on the training fold only, applies to BOTH splits, and writes the fully
    encoded, numeric, model-ready matrix to this stage's scratch — the terminal
    FTE output the FS stage and the runner read once accepted.

    Args:
        impute_strategy: 'median' (default), 'mean', or 'mode'/'most_frequent'
            (aliases average/avg->mean accepted; any other value is rejected).
        scale_method: 'standard' (default), 'minmax', 'robust', or 'none'.
        encode_method: 'onehot', 'ordinal', 'target', or 'none' (default).
        encode_columns: Comma-separated columns to encode; empty auto-detects
            string/object columns.
        encoding_map_json: Optional JSON list of PER-COLUMN encoding decisions
            (the FTE encoding_map); supersedes encode_method/encode_columns. Each
            entry: {"feature", "encoding": leave-numeric|binarize|ordinal|onehot|bin,
            "order_or_bins"?}.
        workspace_id: Optional explicit workspace id; else resolved from _meta.
        run_id: Optional ASAREE run id, recorded alongside this attempt's provenance.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, X_test, y_test, target = _read_scratch(wid)  # noqa: N806
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})

    encoding_map = None
    if encoding_map_json.strip():
        encoding_map, err = core.parse_json_list(
            encoding_map_json, arg_name="encoding_map_json",
            prefer_keys=("encoding_map",),
        )
        if err is not None:
            return json.dumps({"error": err})

    cols = [c.strip() for c in encode_columns.split(",") if c.strip()] or None

    canon_impute = core.dc.canonical_impute_strategy(impute_strategy)
    if canon_impute is None:
        return json.dumps(
            {
                "error": (
                    f"unrecognized impute_strategy {impute_strategy!r}; use 'mean' "
                    "(aliases: average, avg), 'median', or 'mode'/'most_frequent'. "
                    "These are distinct statistics, not interchangeable."
                )
            }
        )
    impute_strategy = "most_frequent" if canon_impute == "mode" else canon_impute

    try:
        pre, steps_info = fte.fit_preprocessor(
            X_train, y_train, list(X_train.columns), wid,
            impute_strategy=impute_strategy, scale_method=scale_method,
            encode_method=encode_method, encode_columns=cols, encoding_map=encoding_map,
        )
        enc_train = pd.DataFrame(pre.pipeline.transform(X_train), columns=pre.feature_names_out)
        enc_test = pd.DataFrame(pre.pipeline.transform(X_test), columns=pre.feature_names_out)
    except ValueError as e:
        return json.dumps({"error": f"fit_preprocessor failed: {e}"})

    _write_scratch(wid, target, enc_train, y_train, enc_test, y_test)
    _merge_learned(
        wid,
        {
            "encoding": steps_info,
            "feature_names_out": pre.feature_names_out,
            "n_features_out": len(pre.feature_names_out),
        },
    )
    _record_run_id(wid, run_id)

    return json.dumps(
        {
            "n_features_out": len(pre.feature_names_out),
            "steps_applied": steps_info,
            "note": "Encoded matrix written to this stage's scratch (FTE complete). "
            "The notebook will call accept_stage to validate and promote this output.",
        }
    )


@mcp.tool()
def ping() -> str:
    """Health check — returns 'pong' to verify the server is running."""
    return "pong"
