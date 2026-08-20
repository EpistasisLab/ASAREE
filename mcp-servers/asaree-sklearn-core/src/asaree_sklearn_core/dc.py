"""Data-cleaning fits (pure; the ``dc`` tool bucket).

Domain/type fixing and imputation. Each **fits on the TRAIN fold only** and
returns a replayable artifact (see :mod:`asaree_sklearn_core.artifacts`) whose
``.apply`` the caller replays on BOTH splits — that frozen-then-applied split is
the leakage-safety invariant (issue #1456). JSON parsing and the workspace
commit stay in the server wrapper; these functions take parsed inputs and return
artifacts + report rows.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .artifacts import (
    DomainFixerArtifact,
    ImputerArtifact,
    new_domain_fixer_id,
    new_imputer_id,
)


def looks_numeric(s: pd.Series) -> bool:
    """True if the column is numeric, or mostly parses as numeric."""
    if pd.api.types.is_numeric_dtype(s):
        return True
    non_null = s.dropna()
    if non_null.empty:
        return False
    parsed = pd.to_numeric(non_null, errors="coerce")
    return bool(parsed.notna().mean() >= 0.5)


def normalize_rule_list(
    parsed: Any, *, key_field: str = "feature", scalar_field: str | None = None
) -> list[dict[str, Any]]:
    """Normalize an LLM-supplied rule payload to a list of dicts.

    LLMs emit these payloads in several shapes; accept all of them instead of
    crashing on ``str.get`` (the historic 'str' object has no attribute 'get'):
      - list of dicts                  -> used as-is (non-dict entries skipped)
      - dict keyed by feature          -> {"AGE": {...}} becomes [{key_field: "AGE", ...}]
      - dict keyed to a scalar         -> {"AGE": "median"} becomes
                                          [{key_field: "AGE", scalar_field: "median"}]
                                          (only when scalar_field is given)
    Raises ValueError on any other shape so the caller returns a clear error.
    """
    if isinstance(parsed, dict):
        out: list[dict[str, Any]] = []
        for k, v in parsed.items():
            if isinstance(v, dict):
                out.append({key_field: k, **v})
            elif scalar_field is not None:
                out.append({key_field: k, scalar_field: v})
            else:
                out.append({key_field: k})
        return out
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    raise ValueError(
        f"expected a JSON list of objects or an object keyed by {key_field}, "
        f"got {type(parsed).__name__}"
    )


# The domain-fixer has exactly two behaviors — coerce-to-numeric or
# restrict-to-value-set — so a rule's ``type`` must resolve to one of these two
# canonical kinds. LLMs phrase the same intent several ways; accept the common,
# unambiguous synonyms and reject everything else with a clear error rather than
# silently leaving the column untouched (which previously let a numeric-stored-
# as-string column reach impute/parquet and crash). Deliberately excluded:
# ``binary`` (cardinality, not type — the 2 levels may be strings), ``object``
# (the very dtype that masks numeric-as-string), ``bool``/``string`` (ambiguous).
_NUMERIC_TYPE_ALIASES = frozenset({"numeric", "continuous", "integer", "int", "float"})
_CATEGORICAL_TYPE_ALIASES = frozenset({"categorical", "nominal", "ordinal"})


def canonical_column_type(raw_type: Any) -> str | None:
    """Map an LLM-supplied column ``type`` to ``"numeric"``/``"categorical"``.

    Returns the canonical kind, or ``None`` when the value is not a recognized
    synonym (so the caller can surface an actionable error instead of no-op'ing).
    """
    t = str(raw_type).strip().lower()
    if t in _NUMERIC_TYPE_ALIASES:
        return "numeric"
    if t in _CATEGORICAL_TYPE_ALIASES:
        return "categorical"
    return None


# Impute strategies are three DISTINCT statistics (mean/median/mode fill
# differently), so — unlike column types — nothing is collapsed *between* them.
# What is accepted is the handful of alternate spellings for the SAME strategy
# (sklearn's "most_frequent" for mode, "average" for mean), so the agent's intent
# survives instead of silently falling through to the mode default. The canonical
# form is the human term mode/mean/median; the FTE preprocessor maps "mode" onto
# sklearn's "most_frequent".
_MEAN_STRATEGY_ALIASES = frozenset({"mean", "average", "avg"})
_MEDIAN_STRATEGY_ALIASES = frozenset({"median"})
_MODE_STRATEGY_ALIASES = frozenset(
    {"mode", "most_frequent", "most frequent", "most-frequent"}
)


def canonical_impute_strategy(raw_strategy: Any) -> str | None:
    """Map an LLM-supplied impute strategy to ``"mean"``/``"median"``/``"mode"``.

    Returns the canonical strategy, or ``None`` when the value is not a recognized
    spelling — so the caller can reject it instead of the historic silent fall-
    through to ``mode`` (which quietly filled the wrong statistic).
    """
    s = str(raw_strategy).strip().lower()
    if s in _MEAN_STRATEGY_ALIASES:
        return "mean"
    if s in _MEDIAN_STRATEGY_ALIASES:
        return "median"
    if s in _MODE_STRATEGY_ALIASES:
        return "mode"
    return None


def inspect_columns(
    X_train: pd.DataFrame,
    *,
    iqr_multiplier: float = 1.5,
    max_levels: int = 50,
) -> dict[str, Any]:
    """Read-only per-column diagnostic — the DC agent's single inspection report.

    Surfaces, per column, everything the agent needs to DECIDE (the agent decides;
    this only reports): inferred type, domain signals (numeric-as-string tokens,
    negative values), IQR outlier bounds + the flagged count, the categorical
    value set, and missingness. Computed on the TRAIN fold only; commits nothing.

    The agent reads the IQR bounds and domain signals to choose coercion bounds
    for :func:`apply_coercions`, and the missingness to choose which columns to
    drop in :func:`drop_and_impute` — the latter AFTER coercion, since coercing
    values to NaN changes each column's missingness. Columns are sorted by
    descending missingness so the drop-threshold decision reads top-down.
    """
    n_rows = int(len(X_train))
    columns: list[dict[str, Any]] = []
    for col in X_train.columns:
        s = X_train[col]
        n_missing = int(s.isnull().sum())
        info: dict[str, Any] = {
            "feature": str(col),
            "n_missing": n_missing,
            "pct_missing": round(n_missing / n_rows, 6) if n_rows else 0.0,
        }
        non_null = s.dropna()
        if looks_numeric(s):
            info["inferred_type"] = "numeric"
            parsed = pd.to_numeric(non_null, errors="coerce")
            # Non-numeric tokens in an otherwise-numeric column are a domain issue
            # (censored labs "<5", unit suffixes, free text). The count tells the
            # agent this column must be coerced before it can be imputed.
            info["n_non_numeric_tokens"] = int(parsed.isna().sum())
            good = parsed.dropna()
            if not good.empty:
                q1, q3 = float(good.quantile(0.25)), float(good.quantile(0.75))
                iqr = q3 - q1
                lower, upper = q1 - iqr_multiplier * iqr, q3 + iqr_multiplier * iqr
                mask = (good < lower) | (good > upper)
                info.update(
                    min=round(float(good.min()), 4),
                    max=round(float(good.max()), 4),
                    mean=round(float(good.mean()), 4),
                    std=round(float(good.std()), 4) if len(good) > 1 else 0.0,
                    skew=round(float(good.skew()), 4) if len(good) > 2 else 0.0,
                    n_negative=int((good < 0).sum()),
                    iqr={
                        "q1": round(q1, 4),
                        "q3": round(q3, 4),
                        "lower": round(float(lower), 4),
                        "upper": round(float(upper), 4),
                        "n_outliers": int(mask.sum()),
                        "pct_outliers": round(float(mask.mean()) * 100, 2),
                    },
                )
        else:
            info["inferred_type"] = "categorical"
            vc = non_null.value_counts()
            levels = [str(v) for v in vc.index.tolist()]
            info["cardinality"] = int(non_null.nunique())
            info["top_values"] = {str(k): int(v) for k, v in vc.head(5).items()}
            info["value_set"] = levels[:max_levels]
            if len(levels) > max_levels:
                info["value_set_truncated"] = True
        columns.append(info)
    columns.sort(key=lambda c: c["pct_missing"], reverse=True)
    return {"n_rows": n_rows, "n_features": len(columns), "columns": columns}


def normalize_coercion_rules(rule_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill in an implicit ``type`` for bound-only coercion rules.

    A coercion rule that carries a numeric bound (``min``/``max``/``nonneg``) but
    no explicit ``type`` is the outlier/range-deletion case — coerce values
    outside the bound to NaN. :class:`DomainFixerArtifact` only applies min/max
    when a rule's type is ``numeric``, so default it here; an ``allowed``-only
    rule defaults to ``categorical``. Rules with an explicit type pass through
    unchanged (synonym canonicalization still happens in :func:`fit_domain_fixer`).
    A ``reason`` key (outlier vs integrity provenance) is preserved for the
    manifest. Domain-integrity and outlier deletions share this one coercion
    mechanism; only the recorded ``reason`` distinguishes them.
    """
    out: list[dict[str, Any]] = []
    for r in rule_items:
        r = dict(r)
        if "type" not in r:
            if any(k in r for k in ("min", "max", "nonneg")):
                r["type"] = "numeric"
            elif "allowed" in r:
                r["type"] = "categorical"
        out.append(r)
    return out


def numeric_as_string_columns(X: pd.DataFrame) -> list[str]:
    """Object-dtype columns that parse as numeric — must be typed before imputing.

    Imputing a numeric fill (mean/median) into an object column leaves a mixed
    str/float column that crashes on parquet write (the ``lab_bun`` ArrowTypeError
    seen in the sweep). :func:`drop_and_impute` uses this to refuse imputation
    until the agent clears them via :func:`apply_coercions` — an actionable guard,
    not a silent fix.
    """
    return [
        str(col)
        for col in X.columns
        if not pd.api.types.is_numeric_dtype(X[col]) and looks_numeric(X[col])
    ]


def plan_column_drop(
    columns: list[str],
    requested: list[str],
    *,
    protected: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str], list[str]]:
    """Resolve a drop request against the current columns.

    Returns ``(to_drop, skipped_absent, skipped_protected)``. A requested column
    that is protected (a group/id column the caller marks) or not present is
    skipped rather than dropped — the guardrail so a runaway "drop everything
    sparse" can never delete a key column. (The target is already structurally
    absent from the DC feature matrix, so it can't be requested.)
    """
    present = set(columns)
    to_drop: list[str] = []
    absent: list[str] = []
    protected_hit: list[str] = []
    for f in requested:
        if f not in present:
            absent.append(f)
        elif f in protected:
            protected_hit.append(f)
        else:
            to_drop.append(f)
    return to_drop, absent, protected_hit


def fit_domain_fixer(
    X_train: pd.DataFrame,
    source_dataset_id: str,
    *,
    rule_items: list[dict[str, Any]] | None = None,
) -> DomainFixerArtifact:
    """Fit a domain-fixer on the TRAIN fold.

    *rule_items* is a normalized rule list (see :func:`normalize_rule_list`); when
    ``None`` rules are auto-inferred — numeric columns get numeric coercion,
    object/categorical columns are restricted to the value set observed in train
    (out-of-set values become NaN on apply). ``"allowed": "from_train"`` in an
    explicit rule resolves to the train value set.
    """
    rules: dict[str, dict[str, Any]] = {}
    if rule_items is not None:
        for r in rule_items:
            feat = r.get("feature")
            if feat is None or feat not in X_train.columns:
                continue
            rule = {k: v for k, v in r.items() if k != "feature"}
            if "type" in rule:
                # Canonicalize known synonyms (continuous->numeric, nominal->
                # categorical, ...). An unrecognized type is left verbatim; the
                # server layer rejects it up front, and apply() ignores it, so a
                # bad type never silently coerces the wrong way.
                canon = canonical_column_type(rule["type"])
                if canon is not None:
                    rule["type"] = canon
            if rule.get("allowed") == "from_train":
                rule["allowed"] = sorted(
                    str(v) for v in X_train[feat].dropna().unique()
                )
            rules[feat] = rule
    else:
        for col in X_train.columns:
            if looks_numeric(X_train[col]):
                rules[col] = {"type": "numeric"}
            else:
                rules[col] = {
                    "type": "categorical",
                    "allowed": sorted(str(v) for v in X_train[col].dropna().unique()),
                }

    return DomainFixerArtifact(
        domain_fixer_id=new_domain_fixer_id(),
        rules=rules,
        feature_names=list(X_train.columns),
        source_dataset_id=source_dataset_id,
    )


def compute_domain_violations(
    X_before: pd.DataFrame,
    X_cleaned: pd.DataFrame,
    rules: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per-column count of values the fixer coerced to NaN (train fold)."""
    violations = []
    for col in X_before.columns:
        before = int(X_before[col].isna().sum())
        after = int(X_cleaned[col].isna().sum())
        if after > before:
            violations.append(
                {
                    "feature": col,
                    "rule": rules.get(col, {}).get("type", ""),
                    "n_coerced": after - before,
                }
            )
    return violations


def fit_imputer(
    X_train: pd.DataFrame,
    source_dataset_id: str,
    *,
    overrides: dict[str, str] | None = None,
) -> tuple[ImputerArtifact, list[dict[str, Any]]]:
    """Fit per-column fill values on the TRAIN fold; return (imputer, report).

    *overrides* maps a feature to a forced ``"mode"|"mean"|"median"`` strategy.
    Columns not listed (and with no missing values) are skipped; otherwise the
    strategy is auto-assigned — mode for binary/low-cardinality/non-numeric
    columns; for continuous columns, median when ``|skew| > 1`` else mean.
    """
    overrides = overrides or {}
    fill_values: dict[str, Any] = {}
    strategies: dict[str, str] = {}
    report = []
    for col in X_train.columns:
        s = X_train[col]
        n_missing = int(s.isna().sum())
        if n_missing == 0 and col not in overrides:
            continue
        if col in overrides:
            strategy = overrides[col]
        elif looks_numeric(s) and s.nunique(dropna=True) > 10:
            num = pd.to_numeric(s, errors="coerce")
            strategy = "median" if abs(float(num.skew())) > 1 else "mean"
        else:
            strategy = "mode"

        if strategy == "mean":
            value: Any = float(pd.to_numeric(s, errors="coerce").mean())
        elif strategy == "median":
            value = float(pd.to_numeric(s, errors="coerce").median())
        else:
            modes = s.mode(dropna=True)
            raw = modes.iloc[0] if not modes.empty else 0
            # Cast numpy scalars (e.g. int64 from an int column) to native Python
            # so the fill_value is JSON-serializable in the report.
            value = raw.item() if hasattr(raw, "item") else raw
            strategy = "mode"

        fill_values[col] = value
        strategies[col] = strategy
        report.append(
            {
                "feature": col,
                "strategy": strategy,
                "fill_value": value,
                "n_imputed": n_missing,
            }
        )

    imputer = ImputerArtifact(
        imputer_id=new_imputer_id(),
        fill_values=fill_values,
        strategies=strategies,
        feature_names=list(X_train.columns),
        source_dataset_id=source_dataset_id,
    )
    return imputer, report
