"""asaree-sklearn-dc — data-cleaning MCP server (SF-DC).

Thin FastMCP wrapper over :mod:`asaree_sklearn_core`. This server does NOT
import asaree_workspace_core and knows nothing about workspace versioning,
state.json, or accept/reject semantics — it only knows the ambient
``workspace_id`` (request ``_meta``) and one fixed convention for where its
own disposable scratch files live: plain ``train.parquet``/``test.parquet``/
``meta.json`` under ``{ASAREE_DATASET_WORKSPACE_DIR}/{workspace_id}/.scratch/dc/``.
asaree-workspace prepares that directory (``open_workspace(..., stage="dc")``)
and is the only thing that later reads it back out and promotes it into the
permanent versioned tree (``accept_stage``) — this server is a pure consumer
of two conventional file paths, nothing more.

Primary flow (inspect → act, decisions stay with the agent):
``inspect_columns`` (read-only report) → ``apply_coercions`` (coerce integrity
errors and implausible outliers to NaN, return post-coercion missingness) →
``drop_and_impute`` (drop the agent-chosen sparse columns, impute the rest).
The staging — coerce first, then decide drops on the returned missingness — is
what lets the drop threshold be judged on real numbers.

The mutating tools chain via the scratch directory: each overwrites
train.parquet/test.parquet there, and the next reads whatever's currently on
disk. Nothing here is visible outside that directory until the notebook calls
asaree-workspace's accept_stage, which validates and promotes it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from mcp.server import FastMCP
from mcp.server.fastmcp import Context

from asaree_sklearn_core import dc

INSTRUCTIONS = """\
Clean a dataset's columns: coerce impossible values to missing, then drop or \
impute what's left.

Inspect first, act second -- the decisions stay with you. Call \
inspect_columns for a read-only report, apply_coercions to turn integrity \
errors and implausible outliers into NaN, and only then drop_and_impute, so \
the drop threshold is judged against real post-coercion missingness rather \
than a guess."""

mcp = FastMCP("asaree-sklearn-dc", instructions=INSTRUCTIONS)

STAGE = "dc"

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
            "open_workspace(..., stage='dc') first."
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
    """Merge new provenance keys into this attempt's learned.json — mirrors
    commit_stage's old merge-across-tool-calls behavior, just against a plain
    scratch file instead of the permanent manifest."""
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
def inspect_columns(
    workspace_id: str = "",
    ctx: Context | None = None,
) -> str:
    """One read-only per-column report for the DC agent — the single inspection call.

    Returns, per column: inferred type, domain signals (numeric-as-string token
    count, negative-value count), IQR outlier bounds + flagged count, the
    categorical value set, and missingness (sorted by missingness descending).
    Commits nothing — it MAKES no decisions; it gives the agent what it needs to
    decide.

    DC flow: inspect_columns -> apply_coercions (coerce integrity errors and
    implausible outliers to NaN, get updated missingness) -> drop_and_impute
    (drop the agent-chosen sparse columns, impute the rest).

    Args:
        workspace_id: Optional explicit workspace id; else resolved from _meta.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, _, _, _, _ = _read_scratch(wid)  # noqa: N806
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(dc.inspect_columns(X_train))


@mcp.tool()
def apply_coercions(
    rules_json: str = "",
    workspace_id: str = "",
    run_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Coerce integrity violations AND implausible outliers to NaN.

    Pass 1 of the staged DC actor. The agent decides, from inspect_columns, which
    values are invalid (wrong type, out-of-set category, impossible sign) or
    physiologically implausible outliers, and expresses each as a per-column rule.
    Values outside the bound / not in the allowed set become NaN (imputed later by
    drop_and_impute). Fits on the TRAIN fold, applies the frozen rule to BOTH
    splits, writes the result to this stage's scratch, and RETURNS the updated
    post-coercion missingness — so the drop decision in pass 2 is made on real
    numbers, not estimates.

    Domain-integrity and outlier deletions share one coercion mechanism; the
    optional ``reason`` on each rule records which it was, for the audit trail.

    Args:
        rules_json: JSON list of rules, each
            {"feature", "type"?: "numeric"|"categorical", "min"?, "max"?,
             "nonneg"?: bool, "allowed"?: [..]|"from_train", "reason"?}. A rule with
            only a bound (min/max/nonneg) and no type defaults to numeric (the
            outlier/range case). An unrecognized ``type`` skips just that rule
            (reported in ``warnings``); every other rule still applies. When empty,
            rules are auto-inferred (numeric coercion; categoricals restricted to
            the train value set) — a safety net that also clears numeric-as-string
            columns without the agent enumerating them.
        workspace_id: Optional explicit workspace id; else resolved from _meta.
        run_id: Optional ASAREE run id, recorded alongside this attempt's provenance.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, X_test, y_test, target = _read_scratch(wid)  # noqa: N806
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})

    rule_items: list[dict[str, Any]] | None = None
    warning: str | None = None
    if rules_json.strip():
        try:
            parsed = json.loads(rules_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"rules_json is not valid JSON: {e}"})
        try:
            rule_items = dc.normalize_coercion_rules(dc.normalize_rule_list(parsed))
        except ValueError as e:
            return json.dumps({"error": f"rules_json: {e}"})
        # Per-rule (not per-call) handling of an unrecognized type: that column's
        # rule is left verbatim and no-ops on apply, and is named in warnings;
        # every other rule in the same payload still applies.
        unknown_types = {
            r.get("feature"): r["type"]
            for r in rule_items
            if "type" in r and dc.canonical_column_type(r["type"]) is None
        }
        if unknown_types:
            warning = (
                f"unrecognized column type(s) {unknown_types} left untouched "
                "(no coercion applied to these columns); use 'numeric' "
                "(aliases: continuous, integer, int, float) or 'categorical' "
                "(aliases: nominal, ordinal). Note 'binary' is ambiguous — "
                "declare the underlying type explicitly to have it coerced."
            )

    fixer = dc.fit_domain_fixer(X_train, wid, rule_items=rule_items)
    cleaned_train = fixer.apply(X_train)
    cleaned_test = fixer.apply(X_test)
    violations = dc.compute_domain_violations(X_train, cleaned_train, fixer.rules)

    _write_scratch(wid, target, cleaned_train, y_train, cleaned_test, y_test)
    _merge_learned(wid, {"domain_rules": fixer.rules, "domain_violations": violations})
    _record_run_id(wid, run_id)

    response: dict[str, Any] = {
        "n_rules": len(fixer.rules),
        "domain_violations": violations,
        "missingness_after_coercion": dc.inspect_columns(cleaned_train)["columns"],
        "n_features": len(cleaned_train.columns),
        "note": "Coerced values written to this stage's scratch. Review "
        "missingness_after_coercion, then call drop_and_impute to drop sparse "
        "columns and impute the rest.",
    }
    if warning is not None:
        response["warnings"] = [warning]
    return json.dumps(response)


@mcp.tool()
def drop_and_impute(
    drop_json: str = "",
    strategy_json: str = "",
    protect_json: str = "",
    workspace_id: str = "",
    run_id: str = "",
    ctx: Context | None = None,
) -> str:
    """Drop the agent-chosen sparse columns, impute every remaining NaN.

    Pass 2 (terminal) of the staged DC actor — call AFTER apply_coercions so the
    drop decision uses post-coercion missingness. The agent sets its OWN
    missingness threshold and names the columns to drop; this tool does not choose.
    After dropping, it imputes so the result has zero missing values, then writes
    it to this stage's scratch (DC's output — the notebook accepts it from there).

    Guardrails: a protected column (group/id) or an absent column is skipped, never
    dropped; it refuses to drop every column (would empty the matrix); and it
    refuses to impute a not-yet-typed numeric-as-string column, returning an
    actionable error to coerce it via apply_coercions first (rather than crashing
    on parquet write).

    Args:
        drop_json: JSON list of feature names to drop (or {"feature": ...} objects).
            Empty drops nothing (impute-only).
        strategy_json: Optional JSON list of {"feature", "strategy": "mode"|"mean"|
            "median"}; aliases accepted (average/avg->mean, most_frequent->mode),
            unrecognized values rejected. Unlisted columns are auto-assigned.
        protect_json: Optional JSON list of columns that must never be dropped
            (e.g. a group/id column). The target is already excluded structurally.
        workspace_id: Optional explicit workspace id; else resolved from _meta.
        run_id: Optional ASAREE run id, recorded alongside this attempt's provenance.
    """
    try:
        wid = _workspace_id_from_ctx(workspace_id, ctx)
        X_train, y_train, X_test, y_test, target = _read_scratch(wid)  # noqa: N806
    except ScratchNotReadyError as e:
        return json.dumps({"error": str(e)})

    def _feature_list(raw: str, label: str) -> tuple[list[str] | None, str | None]:
        if not raw.strip():
            return [], None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"{label} is not valid JSON: {e}"
        if isinstance(parsed, dict):
            parsed = list(parsed.keys())
        if not isinstance(parsed, list):
            return None, f"{label} must be a JSON list of feature names"
        out = [str(x.get("feature")) if isinstance(x, dict) else str(x) for x in parsed]
        return [f for f in out if f and f != "None"], None

    requested, err = _feature_list(drop_json, "drop_json")
    if err is not None:
        return json.dumps({"error": err})
    protect_list, err = _feature_list(protect_json, "protect_json")
    if err is not None:
        return json.dumps({"error": err})

    to_drop, absent, protected_hit = dc.plan_column_drop(
        list(X_train.columns), requested or [], protected=frozenset(protect_list or [])
    )
    if to_drop and len(to_drop) >= len(X_train.columns):
        return json.dumps(
            {"error": "refusing to drop every column (would empty the matrix); "
                      "keep at least one feature"}
        )

    kept_train = X_train.drop(columns=to_drop)
    kept_test = X_test.drop(columns=to_drop)

    # Guard: a numeric-as-string column can't be imputed (a numeric fill would
    # leave a mixed str/float column that crashes on parquet write). Send the
    # agent back to apply_coercions rather than crash.
    untyped = dc.numeric_as_string_columns(kept_train)
    if untyped:
        return json.dumps(
            {"error": f"columns {untyped} are numeric stored as text and must be "
                      "coerced before imputation; call apply_coercions with a "
                      "{'type': 'numeric'} rule for each, then retry drop_and_impute"}
        )

    overrides: dict[str, str] = {}
    if strategy_json.strip():
        try:
            parsed = json.loads(strategy_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"strategy_json is not valid JSON: {e}"})
        try:
            strat_items = dc.normalize_rule_list(parsed, scalar_field="strategy")
        except ValueError as e:
            return json.dumps({"error": f"strategy_json: {e}"})
        unknown_strats = {
            r.get("feature"): r["strategy"]
            for r in strat_items
            if r.get("strategy") and dc.canonical_impute_strategy(r["strategy"]) is None
        }
        if unknown_strats:
            return json.dumps(
                {"error": f"unrecognized impute strategy(ies) {unknown_strats}; use "
                          "'mean' (aliases: average, avg), 'median', or 'mode' "
                          "(aliases: most_frequent). These are distinct statistics, "
                          "not interchangeable."}
            )
        for r in strat_items:
            if r.get("feature") in kept_train.columns and r.get("strategy"):
                overrides[r["feature"]] = dc.canonical_impute_strategy(r["strategy"])

    imputer, report = dc.fit_imputer(kept_train, wid, overrides=overrides)
    imputed_train = imputer.apply(kept_train)
    imputed_test = imputer.apply(kept_test)

    _write_scratch(wid, target, imputed_train, y_train, imputed_test, y_test)
    _merge_learned(
        wid,
        {
            "dropped_columns": to_drop,
            "imputation": {
                col: {"strategy": imputer.strategies[col], "fill_value": imputer.fill_values[col]}
                for col in imputer.fill_values
            },
        },
    )
    _record_run_id(wid, run_id)

    remaining = int(imputed_train.isnull().sum().sum())
    return json.dumps(
        {
            "dropped": to_drop,
            "skipped_absent": absent,
            "skipped_protected": protected_hit,
            "imputation": report,
            "n_missing_remaining": remaining,
            "n_features": len(imputed_train.columns),
            "note": "Dropped + imputed matrices written to this stage's scratch "
            "(DC complete). The notebook will call accept_stage to validate and "
            "promote this output.",
        }
    )


@mcp.tool()
def ping() -> str:
    """Health check — returns 'pong' to verify the server is running."""
    return "pong"
