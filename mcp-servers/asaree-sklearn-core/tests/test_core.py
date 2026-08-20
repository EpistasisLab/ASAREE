"""Unit tests for asaree-sklearn-core (issue #1456).

Run directly (matches the asaree-sklearn suites' style; pytest is not required):

    PYTHONPATH=src python tests/test_core.py

Covers two things the issue's acceptance criteria call out:
  1. Computation reproduces the monolith's numeric results (checked against an
     independent scipy/sklearn computation on synthetic data).
  2. The train-only-fit / leakage-safe invariant: every statistic is frozen on
     the TRAIN fold and applying it to a perturbed test fold changes nothing.

Context-driven workspace-HEAD resolution moved to asaree-workspace-core's own
test suite along with workspace.py/staging.py/context.py — see that repo.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from asaree_sklearn_core import dc, eda, fs, fte, model, stats

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Synthetic fixtures — a deterministic classification split.
# ---------------------------------------------------------------------------


def make_split(seed: int = 0) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    rng = np.random.RandomState(seed)
    n = 200
    signal = rng.normal(0, 1, n)
    y = (signal + rng.normal(0, 0.3, n) > 0).astype(int)
    X = pd.DataFrame(
        {
            "signal": signal,
            "noise": rng.normal(0, 1, n),
            "constant": np.ones(n),
            "cat": rng.choice(["a", "b", "c"], n),
        }
    )
    split = n // 2
    return (
        X.iloc[:split].reset_index(drop=True),
        pd.Series(y[:split], name="target"),
        X.iloc[split:].reset_index(drop=True),
        pd.Series(y[split:], name="target").reset_index(drop=True),
    )


# ---------------------------------------------------------------------------
# 1. Numeric reproduction — core == independent reference computation.
# ---------------------------------------------------------------------------


def test_numeric_reproduction() -> None:
    print("=== Numeric reproduction ===")
    X_train, y_train, _, _ = make_split()

    # correlations vs a direct scipy spearmanr
    from scipy.stats import spearmanr

    out = eda.compute_correlations(X_train, y_train, method="spearman")
    got = {d["feature"]: d["correlation"] for d in out["target_correlations"]}
    ref_signal = round(float(spearmanr(X_train["signal"], y_train.values)[0]), 4)
    check("correlations reproduce scipy spearmanr", got.get("signal") == ref_signal,
          f"{got.get('signal')} != {ref_signal}")

    # class balance vs value_counts
    cb = eda.check_class_balance(y_train)
    vc = y_train.value_counts()
    check("class_balance counts", cb["class_counts"] == {str(k): int(v) for k, v in vc.items()})

    # IQR outliers vs a manual computation on an injected extreme value
    Xo = X_train.copy()
    Xo.loc[0, "signal"] = 1e6
    od = eda.detect_outliers(Xo, method="iqr", threshold=1.5)
    check("iqr flags injected extreme", od["per_feature"].get("signal", {}).get("n_outliers", 0) >= 1)

    # variance filter drops the constant column
    sel, dropped, space = fs.variance_filter(X_train, "ds_test", threshold=0.0)
    check("variance_filter drops constant", "constant" not in sel.selected_features
          and any(d["feature"] == "constant" for d in dropped))

    # supervised f_classif ranks 'signal' above 'noise'
    sf = fs.supervised_filter(X_train, y_train, "ds_test", method="f_classif", k=2)
    check("supervised_filter ranks signal first", sf.selected_features[0] == "signal",
          str(sf.selected_features))

    # CV reproduces a direct sklearn cross_validate mean
    from sklearn.model_selection import StratifiedKFold, cross_validate
    from sklearn.ensemble import RandomForestClassifier

    est, cvres, feats = model.cross_validate_model(
        X_train[["signal", "noise"]], y_train, "random_forest", {}, cv_folds=5, random_seed=42
    )
    ref_cv = cross_validate(
        RandomForestClassifier(random_state=42),
        X_train[["signal", "noise"]].fillna(0).values,
        y_train.values,
        cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42),
        scoring={"balanced_accuracy": "balanced_accuracy"},
    )
    check("cross_validate_model reproduces sklearn",
          cvres["balanced_accuracy_mean"] == round(float(ref_cv["test_balanced_accuracy"].mean()), 4),
          f"{cvres['balanced_accuracy_mean']}")

    # stats: friedman + summarize
    fr = stats.friedman_test({"a": [1, 2, 3, 4], "b": [2, 3, 4, 5], "c": [1, 1, 2, 2]})
    check("friedman returns statistic", fr["test"] == "Friedman" and fr["n_conditions"] == 3)
    se = stats.summarize_experiment(
        [{"m": "x", "balanced_accuracy": 0.7}, {"m": "x", "balanced_accuracy": 0.9},
         {"m": "y", "balanced_accuracy": 0.5}],
        ["m"],
    )
    check("summarize ranks by median", se["best_condition"]["m"] == "x")


# ---------------------------------------------------------------------------
# 2. Train-only-fit / leakage-safe invariant.
# ---------------------------------------------------------------------------


def test_train_only_fit() -> None:
    print("=== Train-only-fit invariant ===")
    X_train, y_train, X_test, _ = make_split()

    # Imputer fill value is frozen on TRAIN — perturbing TEST cannot change it.
    Xtr = X_train.copy()
    Xtr.loc[0, "signal"] = np.nan  # force an imputation
    imp1, _ = dc.fit_imputer(Xtr, "ds")
    fill_a = imp1.fill_values["signal"]
    ref = float(pd.to_numeric(Xtr["signal"], errors="coerce").mean()) \
        if imp1.strategies["signal"] == "mean" \
        else float(pd.to_numeric(Xtr["signal"], errors="coerce").median())
    check("imputer fill == train statistic", abs(fill_a - ref) < 1e-9)

    # Applying to a wildly perturbed test fold uses the frozen value, not test's.
    Xte = X_test.copy()
    Xte.loc[0, "signal"] = np.nan
    Xte.loc[1:, "signal"] = 1e9  # would wreck any test-derived mean/median
    applied = imp1.apply(Xte)
    check("imputer applies frozen fill to test", float(applied.loc[0, "signal"]) == fill_a)

    # Domain fixer's allowed set is the TRAIN value set; a test-only category -> NaN.
    fixer = dc.fit_domain_fixer(X_train, "ds")  # auto-infer: 'cat' allowed = train cats
    Xte2 = X_test.copy()
    Xte2.loc[0, "cat"] = "ZZZ_unseen"
    cleaned = fixer.apply(Xte2)
    check("domain_fixer NaNs unseen test category", pd.isna(cleaned.loc[0, "cat"]))

    # Feature recipe bin edges are frozen on TRAIN and reused on TEST.
    recipe = fte.build_feature_recipe(
        X_train, [{"name": "signal_bin", "op": "bin", "inputs": ["signal"], "params": {"n_bins": 4}}], "ds"
    )
    edges = recipe.entries[0]["params"]["edges"]
    col = pd.to_numeric(X_train["signal"], errors="coerce").dropna()
    ref_edges = sorted({round(float(col.quantile(q)), 6) for q in np.linspace(0, 1, 5)[1:-1]})
    check("recipe bin edges frozen from train", edges == ref_edges)
    eng_test = recipe.apply(X_test)
    check("recipe materializes on test with train edges", "signal_bin" in eng_test.columns)

    # Preprocessor fit is train-only; transform of test yields the same width.
    pre, steps = fte.fit_preprocessor(
        X_train, y_train, list(X_train.columns), "ds", scale_method="standard", encode_method="onehot"
    )
    tr = pre.pipeline.transform(X_train)
    te = pre.pipeline.transform(X_test)
    check("preprocessor train/test same width", tr.shape[1] == te.shape[1] == len(pre.feature_names_out))


def test_parse_json_list() -> None:
    print("=== Lenient JSON-list parsing ===")
    from asaree_sklearn_core import parse_json_list

    # Bare array — the canonical form.
    val, err = parse_json_list('[{"name": "a"}]', arg_name="recipe_json")
    check("bare array passes through", err is None and val == [{"name": "a"}])

    # Wrapped under a preferred key (the report-vs-arg slip that caused the bug).
    val, err = parse_json_list(
        '{"engineering_recipe": [{"name": "a"}]}', arg_name="recipe_json",
        prefer_keys=("engineering_recipe", "recipe"),
    )
    check("preferred key is unwrapped", err is None and val == [{"name": "a"}])

    # Full report object (several arrays) — preferred key disambiguates.
    val, err = parse_json_list(
        '{"engineering_recipe": [1], "encoding_map": [2, 3]}', arg_name="recipe_json",
        prefer_keys=("engineering_recipe",),
    )
    check("preferred key wins over other arrays", err is None and val == [1])

    # Single-array wrapper with no preferred-key hint — still unambiguous.
    val, err = parse_json_list('{"whatever": [1, 2]}', arg_name="x")
    check("sole array value is unwrapped", err is None and val == [1, 2])

    # Ambiguous dict (two arrays, no preferred key) — a clear error, not a guess.
    val, err = parse_json_list('{"a": [1], "b": [2]}', arg_name="x")
    check("ambiguous object errors", val is None and err is not None and "x:" in err)

    # Scalar / invalid JSON both surface caller-ready errors.
    val, err = parse_json_list('"nope"', arg_name="x")
    check("scalar errors", val is None and err is not None)
    val, err = parse_json_list("{not json", arg_name="x")
    check("invalid JSON errors", val is None and err is not None and "not valid JSON" in err)

    # FS regression: a wrapped name-list is unwrapped, not reduced to dict keys.
    val, err = parse_json_list(
        '{"selected_features": ["age", "bmi"]}', arg_name="features_json",
        prefer_keys=("selected_features", "features"),
    )
    check("wrapped name-list unwrapped (not dict keys)", err is None and val == ["age", "bmi"])


def test_domain_type_synonyms() -> None:
    print("=== Domain-fixer type synonyms ===")

    # Canonical mapping: common numeric/categorical synonyms resolve; the
    # deliberately-excluded/ambiguous terms stay unrecognized (None).
    for t in ("numeric", "continuous", "integer", "int", "float", "CONTINUOUS", " Float "):
        check(f"'{t}' -> numeric", dc.canonical_column_type(t) == "numeric")
    for t in ("categorical", "nominal", "ordinal", "Nominal"):
        check(f"'{t}' -> categorical", dc.canonical_column_type(t) == "categorical")
    for t in ("binary", "object", "bool", "boolean", "string", "text", "datetime"):
        check(f"'{t}' unrecognized", dc.canonical_column_type(t) is None)

    # Regression for the lab_bun crash: a numeric measurement stored as strings.
    # A 'continuous' rule (synonym of numeric) must coerce it to a real numeric
    # column so downstream impute/parquet never sees a mixed str+float object.
    n = 40
    X_train = pd.DataFrame({"lab": [str(v) for v in range(n)], "cat": ["a"] * n})
    X_train.loc[0, "lab"] = None  # a NaN alongside the numeric strings
    check("fixture lab is non-numeric before fix", not pd.api.types.is_numeric_dtype(X_train["lab"]))

    fixer = dc.fit_domain_fixer(
        X_train, "ds",
        rule_items=[{"feature": "lab", "type": "continuous", "min": 0, "max": 1000}],
    )
    check("continuous rule stored as canonical numeric", fixer.rules["lab"]["type"] == "numeric")

    cleaned = fixer.apply(X_train)
    check("lab coerced to numeric dtype", pd.api.types.is_numeric_dtype(cleaned["lab"]))
    check("numeric-string values preserved", float(cleaned.loc[5, "lab"]) == 5.0)
    check("original NaN stays NaN", pd.isna(cleaned.loc[0, "lab"]))

    # An unrecognized type is left verbatim by the core (the server rejects it up
    # front); apply() must not silently coerce it either way.
    fx2 = dc.fit_domain_fixer(X_train, "ds", rule_items=[{"feature": "lab", "type": "binary"}])
    check("unrecognized type left verbatim", fx2.rules["lab"]["type"] == "binary")
    check("unrecognized type not coerced by apply", not pd.api.types.is_numeric_dtype(fx2.apply(X_train)["lab"]))


def test_impute_strategy_synonyms() -> None:
    print("=== Impute strategy synonyms ===")

    # Alternate spellings of the SAME statistic resolve; the three canonical
    # statistics stay distinct (never collapsed into one another).
    for s in ("mean", "average", "avg", "AVERAGE", " Mean "):
        check(f"'{s}' -> mean", dc.canonical_impute_strategy(s) == "mean")
    check("'median' -> median", dc.canonical_impute_strategy("median") == "median")
    for s in ("mode", "most_frequent", "most frequent", "most-frequent", "MODE"):
        check(f"'{s}' -> mode", dc.canonical_impute_strategy(s) == "mode")

    # Unrecognized strategies return None so the server can reject them instead
    # of the historic silent fall-through to mode.
    for s in ("medium", "min", "zero", "constant", ""):
        check(f"'{s}' unrecognized", dc.canonical_impute_strategy(s) is None)

    # A recognized override actually fits that statistic. mean of 0..9 is 4.5;
    # its mode (all distinct) would be 0 — so the value proves mean was used.
    X_train = pd.DataFrame({"v": list(range(10)) + [None]})
    imputer, _ = dc.fit_imputer(X_train, "ds", overrides={"v": "mean"})
    check("mean override fills the mean", abs(float(imputer.fill_values["v"]) - 4.5) < 1e-9)
    check("mean override recorded as mean", imputer.strategies["v"] == "mean")


def test_recipe_validation() -> None:
    print("=== build_features recipe validation ===")

    cols = ["AGE", "BMI", "comorbid_dm", "comorbid_htn"]

    # A well-formed recipe has no problems.
    ok = [
        {"name": "cc", "op": "count_nonzero", "inputs": ["comorbid_dm", "comorbid_htn"]},
        {"name": "r", "op": "ratio", "inputs": ["BMI", "AGE"]},
    ]
    check("valid recipe -> no problems", fte.validate_recipe_entries(ok, cols) == [])

    # Each real failure mode from the sweep is caught with a targeted message.
    cases = {
        "missing op": [{"name": "x", "inputs": ["AGE"]}],
        "unknown op": [{"name": "x", "op": "threshold", "inputs": ["AGE"]}],
        "missing name": [{"op": "sum", "inputs": ["AGE", "BMI"]}],
        "unknown input col": [{"name": "x", "op": "ratio", "inputs": ["BMI", "WEIGHT"]}],
        "ratio too few inputs": [{"name": "x", "op": "ratio", "inputs": ["BMI"]}],
        "entry not object": ["not-a-dict"],
    }
    for label, entries in cases.items():
        problems = fte.validate_recipe_entries(entries, cols)
        check(f"{label} -> flagged", len(problems) == 1)

    # unknown op lists the valid vocabulary so the agent can self-correct.
    msg = fte.validate_recipe_entries(cases["unknown op"], cols)[0]
    check("unknown-op message lists valid ops", "count_nonzero" in msg and "ratio" in msg)

    # group_agg resolves its grouping column from params (no positional input
    # required), so a params-only group_agg is valid.
    ga = [{"name": "g", "op": "group_agg", "inputs": [],
           "params": {"group_col": "comorbid_dm", "value_col": "AGE"}}]
    check("group_agg via params -> valid", fte.validate_recipe_entries(ga, cols) == [])


def test_inspect_and_clean() -> None:
    print("=== inspect_columns + staged coerce/drop/impute ===")

    n = 100
    X = pd.DataFrame(
        {
            # numeric-as-string: mostly parses, a censored token + free text
            "lab": [str(v) for v in range(n - 2)] + ["<5", "SEE NOTE"],
            # clean numeric with one implausible outlier and some missing
            "vital": [70.0] * (n - 5) + [900.0, 71.0, 69.0, 72.0, 68.0],
            # sparse column (mostly missing) — a drop candidate
            "sparse": [1.0] + [np.nan] * (n - 1),
            # categorical
            "grp": ["a", "b"] * (n // 2),
        }
    )
    X.loc[0, "vital"] = np.nan  # a native NaN to impute

    report = dc.inspect_columns(X)
    by_feat = {c["feature"]: c for c in report["columns"]}
    check("inspect covers every column", set(by_feat) == set(X.columns))
    check("lab flagged numeric-as-string", by_feat["lab"]["n_non_numeric_tokens"] == 2)
    check("lab inferred numeric", by_feat["lab"]["inferred_type"] == "numeric")
    check("vital reports IQR bounds", "iqr" in by_feat["vital"] and by_feat["vital"]["iqr"]["n_outliers"] >= 1)
    check("sparse ranks first by missingness", report["columns"][0]["feature"] == "sparse")
    check("grp inferred categorical", by_feat["grp"]["inferred_type"] == "categorical")

    # Coercion rules: a bound-only rule (outlier deletion) defaults to numeric;
    # a censored/text token in lab is cleared by a numeric-typing rule.
    rules = dc.normalize_coercion_rules(
        [
            {"feature": "vital", "max": 300, "reason": "physiologically implausible"},
            {"feature": "lab", "type": "numeric"},
        ]
    )
    check("bound-only rule defaults to numeric", rules[0]["type"] == "numeric")
    check("reason preserved for provenance", rules[0]["reason"] == "physiologically implausible")

    fixer = dc.fit_domain_fixer(X, "ds", rule_items=rules)
    cleaned = fixer.apply(X)
    check("outlier 900 coerced to NaN by max bound", pd.isna(cleaned.loc[n - 5, "vital"]))
    check("lab tokens coerced to NaN", pd.isna(cleaned.loc[n - 1, "lab"]) and pd.isna(cleaned.loc[n - 2, "lab"]))
    check("lab now numeric dtype", pd.api.types.is_numeric_dtype(cleaned["lab"]))
    check("clean numeric value survives coercion", float(cleaned.loc[10, "lab"]) == 10.0)

    # numeric_as_string guard: before coercion lab is a mixed object column; after
    # it is clean numeric and no longer flagged.
    check("lab flagged as numeric-as-string pre-coercion", "lab" in dc.numeric_as_string_columns(X))
    check("lab cleared post-coercion", "lab" not in dc.numeric_as_string_columns(cleaned))

    # Drop planning: protect a group column and skip an absent one.
    to_drop, absent, protected = dc.plan_column_drop(
        list(cleaned.columns), ["sparse", "grp", "ghost"], protected=frozenset({"grp"})
    )
    check("sparse selected for drop", to_drop == ["sparse"])
    check("protected group column skipped", protected == ["grp"])
    check("absent column skipped", absent == ["ghost"])

    # After drop + impute the matrix has zero missing values.
    kept = cleaned.drop(columns=to_drop)
    imputer, _ = dc.fit_imputer(kept, "ds")
    imputed = imputer.apply(kept)
    check("no missing values remain after impute", int(imputed.isnull().sum().sum()) == 0)
    check("dropped column is gone", "sparse" not in imputed.columns)


def test_binary_bundle() -> None:
    """metrics_at_* carries precision/recall alongside accuracy/balanced_accuracy/f1,
    and they agree with an independent sklearn computation at the same threshold."""
    from sklearn.metrics import precision_score, recall_score

    y = np.array([0, 0, 0, 1, 1, 1, 1, 0, 1, 0])
    proba = np.array([0.1, 0.2, 0.6, 0.9, 0.8, 0.3, 0.7, 0.4, 0.6, 0.2])
    bundle = model.binary_bundle(y, proba, threshold=0.6)

    prevalence = float(y.mean())
    check(
        "metrics_at_prevalence uses the observed test-set prevalence as its threshold",
        bundle["metrics_at_prevalence"]["threshold"] == round(prevalence, 4),
    )
    check("test_prevalence matches y.mean()", bundle["test_prevalence"] == round(prevalence, 4))

    for key in ("metrics_at_0.5", "metrics_at_chosen_threshold", "metrics_at_prevalence"):
        at = bundle[key]
        t = at["threshold"]
        pred = (proba >= t).astype(int)
        check(f"{key} has precision", "precision" in at)
        check(f"{key} has recall", "recall" in at)
        check(
            f"{key} precision matches independent computation",
            at["precision"] == round(float(precision_score(y, pred, zero_division=0)), 4),
        )
        check(
            f"{key} recall matches independent computation",
            at["recall"] == round(float(recall_score(y, pred, zero_division=0)), 4),
        )


def main() -> int:
    test_numeric_reproduction()
    test_train_only_fit()
    test_parse_json_list()
    test_domain_type_synonyms()
    test_impute_strategy_synonyms()
    test_recipe_validation()
    test_inspect_and_clean()
    test_binary_bundle()
    print(f"\nResults: {_PASS}/{_PASS + _FAIL} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
