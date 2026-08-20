"""Feature-engineering fits (pure; the ``fte`` tool bucket).

The replayable-recipe engine (freeze train-derived statistics, then materialize)
and the preprocessing-pipeline fit (impute/scale/encode via a ColumnTransformer).
Both **fit on the TRAIN fold only** and return replayable artifacts; the caller
replays them on the test split. JSON parsing and the workspace commit live in the
server wrapper.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder

from .artifacts import (
    COMPARE_OPS,
    RECIPE_OPS,
    FeatureRecipeArtifact,
    PreprocessorArtifact,
    new_preprocessor_id,
    new_recipe_id,
)

# Minimum ``inputs`` arity per op. ratio needs a numerator + denominator; every
# other op needs at least one input (the default below), so a 0-input entry is
# rejected up front instead of producing a degenerate all-NaN/zero column.
_MIN_INPUTS = {"ratio": 2}

# Comparison-op word spellings surfaced in validation messages (symbols are also
# accepted at materialize time; see artifacts.COMPARE_OPS).
_COMPARE_WORDS = sorted(k for k in COMPARE_OPS if k.isalpha())


def _is_number(x: Any) -> bool:
    """True for a real int/float (bool is excluded — it is not a valid weight)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_recipe_entries(
    raw_entries: list[Any], columns: list[str]
) -> list[str]:
    """Return human-readable problems with an engineered-feature recipe (empty == OK).

    Checks the recipe contract up front so the agent gets actionable feedback —
    missing ``name``/``op``, an op outside :data:`RECIPE_OPS`, a non-list
    ``inputs``, an input column that isn't in the matrix, or too few inputs for a
    positional op — instead of a cryptic ``KeyError``/``IndexError`` surfacing from
    deep inside materialization.
    """
    problems: list[str] = []
    colset = set(columns)
    for i, entry in enumerate(raw_entries):
        where = f"entry[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: must be an object, got {type(entry).__name__}")
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            where = f"entry '{name}'"
        else:
            problems.append(f"{where}: missing required non-empty string 'name'")

        op = entry.get("op")
        if op is None:
            problems.append(
                f"{where}: missing required 'op' (valid ops: {sorted(RECIPE_OPS)})"
            )
            continue
        if op not in RECIPE_OPS:
            problems.append(
                f"{where}: unknown op {op!r} (valid ops: {sorted(RECIPE_OPS)})"
            )
            continue

        # params must be an object for every op (not just group_agg). A JSON
        # null or a scalar/list here would otherwise reach freeze_recipe_entry's
        # dict(...) and raise an uncaught TypeError. null is tolerated (treated
        # as "no params"); any other non-object is a malformed entry.
        params = entry.get("params")
        if params is not None and not isinstance(params, dict):
            problems.append(
                f"{where}: 'params' must be an object mapping names to values "
                f"(or omitted), got {type(params).__name__}"
            )
            continue

        inputs = entry.get("inputs", [])
        if not isinstance(inputs, list):
            problems.append(
                f"{where}: 'inputs' must be a list of column names, got "
                f"{type(inputs).__name__}"
            )
            continue

        if op == "group_agg":
            # group_agg resolves its grouping column from params.group_col or the
            # first input, so it has a distinct arity/column contract.
            params = entry.get("params")
            params = params if isinstance(params, dict) else {}
            group_col = params.get("group_col") or (inputs[0] if inputs else None)
            if group_col is None:
                problems.append(
                    f"{where}: group_agg needs params.group_col or at least one input"
                )
            elif group_col not in colset:
                problems.append(
                    f"{where}: group_agg group_col {group_col!r} not in the matrix"
                )
            continue

        missing = [c for c in inputs if c not in colset]
        if missing:
            problems.append(f"{where}: input column(s) not in the matrix: {missing}")
        need = _MIN_INPUTS.get(op, 1)
        if len(inputs) < need:
            problems.append(
                f"{where}: op {op!r} needs at least {need} input column(s), "
                f"got {len(inputs)}"
            )

        # op-specific parameter contracts, so a missing/typoed param is an
        # actionable rejection rather than a silent no-op or a materialize crash.
        p = params if isinstance(params, dict) else {}
        if op in ("threshold", "count_threshold"):
            if p.get("op") not in COMPARE_OPS:
                problems.append(f"{where}: {op} needs params.op (one of {_COMPARE_WORDS})")
            if not _is_number(p.get("value")):
                problems.append(f"{where}: {op} needs a numeric params.value")
        elif op == "count_equal" and "value" not in p:
            problems.append(
                f"{where}: count_equal needs params.value (the literal to match)"
            )
        elif op == "weighted_sum":
            w = p.get("weights")
            if not (
                isinstance(w, list)
                and len(w) == len(inputs)
                and all(_is_number(x) for x in w)
            ):
                problems.append(
                    f"{where}: weighted_sum needs params.weights: a list of "
                    f"{len(inputs)} numbers aligned 1:1 with inputs"
                )
    return problems


def freeze_recipe_entry(X: pd.DataFrame, entry: dict[str, Any]) -> dict[str, Any]:
    """Freeze any train-derived statistic an entry depends on into its params."""
    op = entry.get("op")
    # Tolerate a null/absent/non-object "params": treat it as empty rather than
    # letting dict(None) raise TypeError (validate_recipe_entries already rejects
    # a non-null non-object params up front, so this is the defensive floor).
    raw_params = entry.get("params")
    params = dict(raw_params) if isinstance(raw_params, dict) else {}
    inputs = entry.get("inputs", [])
    if op == "bin" and "edges" not in params:
        n_bins = int(params.get("n_bins", 4))
        col = pd.to_numeric(X[inputs[0]], errors="coerce").dropna()
        qs = np.linspace(0, 1, n_bins + 1)[1:-1]
        params["edges"] = sorted({round(float(col.quantile(q)), 6) for q in qs})
    if op == "group_agg" and "group_map" not in params:
        group_col = params.get("group_col", inputs[0])
        value_col = params.get("value_col", inputs[1] if len(inputs) > 1 else inputs[0])
        stat = params.get("stat", "mean")
        grouped = X.groupby(group_col)[value_col].agg(stat)
        params["group_col"] = group_col
        params["group_map"] = {str(k): float(v) for k, v in grouped.items()}
        params["fallback"] = float(X[value_col].agg(stat))
    if op == "frequency_encode" and "freq_map" not in params:
        vc = X[inputs[0]].astype(str).value_counts(normalize=True)
        params["freq_map"] = {str(k): float(v) for k, v in vc.items()}
        params.setdefault("fallback", 0.0)
    entry = dict(entry)
    entry["params"] = params
    return entry


def build_feature_recipe(
    X_train_clean: pd.DataFrame,
    raw_entries: list[dict[str, Any]],
    source_dataset_id: str,
) -> FeatureRecipeArtifact:
    """Freeze a recipe's train-derived statistics and return the replayable artifact.

    Fits on the (already cleaned) TRAIN fold: every ``bin``/``group_agg`` statistic
    is frozen from train here, so ``FeatureRecipeArtifact.apply`` replays identically
    on the test split. Raises (KeyError/IndexError/ValueError) on a malformed recipe
    for the wrapper to report.
    """
    entries = [freeze_recipe_entry(X_train_clean, e) for e in raw_entries]
    feature_names_out = list(X_train_clean.columns) + [e["name"] for e in entries]
    return FeatureRecipeArtifact(
        feature_recipe_id=new_recipe_id(),
        entries=entries,
        feature_names_in=list(X_train_clean.columns),
        feature_names_out=feature_names_out,
        source_dataset_id=source_dataset_id,
    )


def encoding_map_transformers(
    X_train: pd.DataFrame,
    emap: list[Any],
    numeric_cols: list[str],
    scaler_class: Any,
    impute_strategy: str,
) -> tuple[list[tuple[str, Any, list[str]]], list[str]]:
    """Build per-column ColumnTransformer entries from an FTE encoding map.

    Each entry is ``{"feature", "encoding", "order_or_bins"?}`` where encoding is one
    of leave-numeric | binarize | ordinal | onehot | bin. Ordinal/binarize honor an
    explicit category order (train values reordered by it; train values absent from
    the order are appended, so fit never crashes). All encoders set handle_unknown so
    unseen test categories transform to a sentinel instead of raising. Columns not in
    the map that are numeric fall through impute + scale.
    """
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import (
        KBinsDiscretizer,
        OneHotEncoder,
        OrdinalEncoder,
    )

    def _num_pipeline() -> Any:
        steps: list[tuple[str, Any]] = [
            (
                "impute",
                SimpleImputer(
                    strategy=impute_strategy
                    if impute_strategy in ("mean", "median", "most_frequent")
                    else "median"
                ),
            )
        ]
        if scaler_class is not None:
            steps.append(("scale", scaler_class()))
        return Pipeline(steps)

    def _ordinal_categories(col: str, order: list[str] | None) -> Any:
        observed = list(pd.unique(X_train[col].dropna()))
        if not order:
            return "auto"
        rank = {str(v): i for i, v in enumerate(order)}
        return [sorted(observed, key=lambda v: rank.get(str(v), len(order)))]

    def _as_order(ob: Any) -> list[str] | None:
        if isinstance(ob, list):
            return [str(v) for v in ob]
        if isinstance(ob, str) and ob.strip():
            return [v.strip() for v in ob.split(",") if v.strip()]
        return None

    transformers: list[tuple[str, Any, list[str]]] = []
    leave_numeric: list[str] = []
    onehot_cols: list[str] = []
    mapped: set[str] = set()
    steps_info: list[str] = []

    for e in emap:
        if not isinstance(e, dict):
            continue
        col = e.get("feature")
        enc = str(e.get("encoding", "")).strip()
        if not col or col not in X_train.columns or col in mapped:
            continue
        mapped.add(col)
        ob = e.get("order_or_bins")
        if enc in ("ordinal", "binarize"):
            transformers.append(
                (
                    f"{enc}_{col}",
                    Pipeline(
                        [
                            ("impute", SimpleImputer(strategy="most_frequent")),
                            (
                                "enc",
                                OrdinalEncoder(
                                    categories=_ordinal_categories(col, _as_order(ob)),
                                    handle_unknown="use_encoded_value",
                                    unknown_value=-1,
                                ),
                            ),
                        ]
                    ),
                    [col],
                )
            )
            steps_info.append(f"{col}:{enc}")
        elif enc == "bin":
            order = _as_order(ob)
            n_bins = (
                int(ob)
                if isinstance(ob, (int, float))
                else (len(order) if order else 4)
            )
            transformers.append(
                (
                    f"bin_{col}",
                    Pipeline(
                        [
                            ("impute", SimpleImputer(strategy="median")),
                            (
                                "kbin",
                                KBinsDiscretizer(
                                    n_bins=max(2, n_bins),
                                    encode="ordinal",
                                    strategy="quantile",
                                ),
                            ),
                        ]
                    ),
                    [col],
                )
            )
            steps_info.append(f"{col}:bin({max(2, n_bins)})")
        elif enc == "onehot":
            onehot_cols.append(col)
        else:  # leave-numeric or unrecognized -> numeric passthrough
            leave_numeric.append(col)

    if onehot_cols:
        transformers.append(
            (
                "onehot",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        (
                            "enc",
                            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                        ),
                    ]
                ),
                onehot_cols,
            )
        )
        steps_info.append(f"onehot[{len(onehot_cols)}]")

    numeric_block: list[str] = []
    for col in leave_numeric + [c for c in numeric_cols if c not in mapped]:
        if col not in numeric_block:
            numeric_block.append(col)
    if numeric_block:
        transformers.insert(0, ("numeric", _num_pipeline(), numeric_block))
        steps_info.insert(0, f"numeric[{len(numeric_block)}]")

    return transformers, steps_info


def fit_preprocessor(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    feature_names_in: list[str],
    source_dataset_id: str,
    *,
    impute_strategy: str = "median",
    scale_method: str = "standard",
    encode_method: str = "none",
    encode_columns: list[str] | None = None,
    encoding_map: list[Any] | None = None,
) -> tuple[PreprocessorArtifact, list[str]]:
    """Fit an impute/scale/encode pipeline on the TRAIN fold; return (artifact, steps).

    *encoding_map* (the FTE per-column map) supersedes ``encode_method`` /
    ``encode_columns`` when supplied. ``target`` encoding is the only path that
    consumes ``y_train`` — and only the TRAIN labels — so the fit never touches the
    held-out split. The returned :class:`PreprocessorArtifact` carries the fitted
    pipeline and the output feature names.
    """
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import (
        MinMaxScaler,
        OneHotEncoder,
        OrdinalEncoder,
        RobustScaler,
        StandardScaler,
        TargetEncoder,
    )

    X_train = X_train.copy()
    y_train = y_train.copy()
    numeric_cols = X_train.select_dtypes(include="number").columns.tolist()
    cat_cols = X_train.select_dtypes(exclude="number").columns.tolist()

    resolved_cat_cols = encode_columns if encode_columns else cat_cols

    _scalers = {
        "standard": StandardScaler,
        "minmax": MinMaxScaler,
        "robust": RobustScaler,
    }
    ScalerClass = _scalers.get(scale_method)

    transformers: list[tuple[str, Any, list[str]]] = []
    used_map = encoding_map is not None

    if used_map:
        # Per-column encoding map (FTE encoding_map) supersedes the global encoders.
        transformers, map_steps = encoding_map_transformers(
            X_train, encoding_map, numeric_cols, ScalerClass, impute_strategy
        )
        steps_info = [impute_strategy, scale_method, *map_steps]
    else:
        if numeric_cols:
            num_steps: list[tuple[str, Any]] = [
                (
                    "impute",
                    SimpleImputer(
                        strategy=impute_strategy
                        if impute_strategy in ("mean", "median", "most_frequent")
                        else "median"
                    ),
                ),
            ]
            if ScalerClass is not None:
                num_steps.append(("scale", ScalerClass()))
            transformers.append(("numeric", Pipeline(num_steps), numeric_cols))

        if resolved_cat_cols and encode_method != "none":
            if encode_method == "onehot":
                enc: Any = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
            elif encode_method == "ordinal":
                enc = OrdinalEncoder(
                    handle_unknown="use_encoded_value", unknown_value=-1
                )
            elif encode_method == "target":
                enc = TargetEncoder(smooth="auto")
            else:
                enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False)

            cat_steps: list[tuple[str, Any]] = [
                ("impute", SimpleImputer(strategy="most_frequent")),
                ("encode", enc),
            ]
            transformers.append(("categorical", Pipeline(cat_steps), resolved_cat_cols))

        steps_info = [impute_strategy, scale_method]
        if encode_method != "none" and resolved_cat_cols:
            steps_info.append(f"{encode_method}_encoding")

    if not transformers:
        pipe: Any = Pipeline([("passthrough", "passthrough")])
        X_transformed = pipe.fit_transform(X_train)
        feature_names_out = list(X_train.columns)
    else:
        ct = ColumnTransformer(transformers, remainder="drop")
        pipe = Pipeline([("preprocessor", ct)])

        if not used_map and encode_method == "target":
            if y_train.dtype == object or str(y_train.dtype) == "category":
                y_enc = LabelEncoder().fit_transform(y_train)
            else:
                y_enc = y_train.values
            X_transformed = pipe.fit_transform(X_train, y_enc)
        else:
            X_transformed = pipe.fit_transform(X_train)

        try:
            feature_names_out = list(ct.get_feature_names_out())
        except Exception:  # noqa: BLE001
            feature_names_out = [f"f{i}" for i in range(X_transformed.shape[1])]

    artifact = PreprocessorArtifact(
        preprocessor_id=new_preprocessor_id(),
        pipeline=pipe,
        source_dataset_id=source_dataset_id,
        feature_names_in=feature_names_in,
        feature_names_out=feature_names_out,
        recipe=[
            {
                "impute": impute_strategy,
                "scale": scale_method,
                "encode": "per_column_map" if used_map else encode_method,
            }
        ],
    )
    return artifact, steps_info
