"""End-to-end tests for the scikit-learn-mcp server tools.

Run directly (matches the other mcp-servers/ suites' style; pytest not required):

    PYTHONPATH=src python tests/test_server.py

Everything is driven against real CSV/Parquet files in a tempdir -- the whole
point of this server is that it reads a dataset from a path, so there is no
workspace to seed and nothing to import from ASAREE.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from scikit_learn_mcp import server

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


def _write_classification(tmp: Path) -> str:
    rng = np.random.RandomState(0)
    n = 400
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = (x1 + 0.5 * x2 + rng.normal(scale=0.3, size=n) > 0).astype(int)
    path = tmp / "clf.csv"
    pd.DataFrame(
        {
            "x1": x1,
            "x2": x2,
            "site": rng.choice(["north", "south", "east"], size=n),
            "outcome": y,
        }
    ).to_csv(path, index=False)
    return str(path)


def _write_regression(tmp: Path) -> str:
    rng = np.random.RandomState(1)
    n = 300
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = 3.0 * x1 - 2.0 * x2 + 1.5 + rng.normal(scale=0.1, size=n)
    path = tmp / "reg.parquet"
    pd.DataFrame({"x1": x1, "x2": x2, "price": y}).to_parquet(path, index=False)
    return str(path)


def _write_multiclass(tmp: Path) -> str:
    rng = np.random.RandomState(2)
    n = 300
    x1 = rng.normal(size=n)
    frame = pd.DataFrame({"x1": x1, "x2": rng.normal(size=n)})
    frame["grade"] = pd.cut(x1, bins=[-np.inf, -0.5, 0.5, np.inf], labels=[0, 1, 2]).astype(int)
    path = tmp / "multi.csv"
    frame.to_csv(path, index=False)
    return str(path)


def _write_grouped(tmp: Path) -> str:
    """Six near-identical rows per subject: a random split leaks, a group split doesn't."""
    rng = np.random.RandomState(3)
    rows = []
    for subject in range(80):
        base = rng.normal()
        label = int(base > 0)
        for _ in range(6):
            rows.append(
                {
                    "patient_id": f"P{subject:03d}",
                    "x1": base + rng.normal(scale=0.01),
                    "x2": rng.normal(),
                    "visit_date": f"2024-{1 + subject % 12:02d}-01",
                    "outcome": label,
                }
            )
    path = tmp / "grouped.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def test_describe_dataset(clf_path: str) -> None:
    print("\ndescribe_dataset")
    out = json.loads(server.describe_dataset(data_path=clf_path))
    check("row count", out.get("n_rows") == 400, str(out.get("n_rows")))
    check("column count", out.get("n_columns") == 4)
    check("names listed", [c["name"] for c in out.get("columns", [])] == ["x1", "x2", "site", "outcome"])
    kinds = {c["name"]: c["kind"] for c in out["columns"]}
    check("kinds inferred", kinds["x1"] == "numeric" and kinds["site"] == "categorical", str(kinds))

    targets = [t["column"] for t in out["suggestions"]["candidate_targets"]]
    check("binary target suggested first", targets and targets[0] == "outcome", str(targets))
    check("random split recommended", out["suggestions"]["recommended_split"]["strategy"] == "random")

    out = json.loads(server.describe_dataset(data_path=clf_path, target_column="outcome"))
    target = out.get("target", {})
    check("task type inferred", target.get("inferred_task_type") == "binary", str(target))
    check("class balance reported", set(target.get("class_distribution", {})) == {"0", "1"})
    check("ping", server.ping() == "pong")


def test_describe_dataset_spots_grouping(grouped_path: str) -> None:
    print("\ndescribe_dataset -- grouping and time hints")
    out = json.loads(server.describe_dataset(data_path=grouped_path))
    s = out["suggestions"]
    groups = [g["column"] for g in s["candidate_group_columns"]]
    check("patient_id flagged as a grouping key", "patient_id" in groups, str(groups))
    times = [t["column"] for t in s["candidate_time_columns"]]
    check("visit_date flagged as temporal", "visit_date" in times, str(times))
    check("group split recommended", s["recommended_split"]["strategy"] == "group", str(s["recommended_split"]))


def test_describe_split(clf_path: str) -> None:
    print("\ndescribe_split")
    out = json.loads(server.describe_split(data_path=clf_path, target_column="outcome"))
    split = out.get("split", {})
    check("sizes add up", split.get("n_train", 0) + split.get("n_test", 0) == 400, str(split))
    check("stratified by default", split.get("stratified") is True)
    check("both sides have both classes", len(split.get("test_class_distribution", {})) == 2)
    check("no warnings on clean data", split.get("warnings") == [], str(split.get("warnings")))
    check("split hashed", len(out.get("split_sha256", "")) == 64)

    same = json.loads(server.describe_split(data_path=clf_path, target_column="outcome"))
    check("hash is stable across calls", same["split_sha256"] == out["split_sha256"])
    other = json.loads(server.describe_split(data_path=clf_path, target_column="outcome", random_seed=7))
    check("hash changes with the seed", other["split_sha256"] != out["split_sha256"])


def test_split_strategies(grouped_path: str) -> None:
    print("\nsplit strategies")
    random_split = json.loads(server.describe_split(data_path=grouped_path, target_column="outcome"))["split"]
    check(
        "random split warns that patient_id straddles it",
        any("BOTH splits" in w for w in random_split.get("warnings", [])),
        str(random_split.get("warnings")),
    )

    grouped = json.loads(
        server.describe_split(
            data_path=grouped_path,
            target_column="outcome",
            split_json='{"strategy": "group", "group_column": "patient_id"}',
        )
    )["split"]
    check("group split keeps subjects on one side", grouped.get("groups_in_both_splits") == 0, str(grouped))
    check("group column excluded from features", "patient_id" in grouped.get("excluded_from_features", []))

    timed = json.loads(
        server.describe_split(
            data_path=grouped_path,
            target_column="outcome",
            split_json='{"strategy": "time", "time_column": "visit_date"}',
        )
    )["split"]
    check("time split reports its boundary", "train_period" in timed and "test_period" in timed, str(timed))
    check(
        "test period starts after the train period ends",
        timed["test_period"][0] > timed["train_period"][1],
        str(timed),
    )

    bad = json.loads(
        server.describe_split(data_path=grouped_path, target_column="outcome", split_json='{"strategy": "group"}')
    )
    check("group without a column is rejected", "group_column" in bad.get("error", ""), bad.get("error", ""))


def test_predefined_split(tmp: Path, clf_path: str) -> None:
    print("\nsplit strategies -- predefined")
    frame = pd.read_csv(clf_path)
    train_path = tmp / "pre_train.csv"
    test_path = tmp / "pre_test.csv"
    frame.iloc[:300].to_csv(train_path, index=False)
    frame.iloc[300:].to_csv(test_path, index=False)
    out = json.loads(
        server.describe_split(
            data_path=str(train_path),
            target_column="outcome",
            split_json=json.dumps({"strategy": "predefined", "test_path": str(test_path)}),
        )
    )
    split = out.get("split", {})
    check("train side is the first file", split.get("n_train") == 300, str(split))
    check("test side is the second file", split.get("n_test") == 100, str(split))

    marked = frame.assign(split=["train"] * 300 + ["test"] * 100)
    marked_path = tmp / "marked.csv"
    marked.to_csv(marked_path, index=False)
    out = json.loads(
        server.describe_split(
            data_path=str(marked_path),
            target_column="outcome",
            split_json='{"strategy": "predefined", "split_column": "split"}',
        )
    )
    check("split column honored", out["split"]["n_test"] == 100, str(out["split"]))
    check("split column excluded from features", "split" not in out["feature_columns"])


def test_fit_logistic_regression(clf_path: str) -> None:
    print("\nfit_logistic_regression -- binary")
    out = json.loads(server.fit_logistic_regression(data_path=clf_path, target_column="outcome"))
    check("no error", "error" not in out, out.get("error", ""))
    m = out.get("test_metrics", {})
    check("roc_auc is strong on separable data", (m.get("roc_auc") or 0) > 0.9, str(m.get("roc_auc")))
    check("PR-AUC reported with its baseline", m.get("average_precision") and m.get("average_precision_baseline"))
    check("confusion matrix totals the test split", sum(m["confusion_matrix"].values()) == out["split"]["n_test"])
    curves = out.get("test_curves", {})
    check("operating points returned by default", "youden_j" in curves.get("best_thresholds", {}), str(curves))
    check("bulky curve arrays withheld by default", "roc" not in curves and "threshold_sweep" not in curves)

    full = json.loads(server.fit_logistic_regression(data_path=clf_path, target_column="outcome", include_curves=True))
    check("curves returned on request", "roc" in full["test_curves"] and "calibration" in full["test_curves"])
    check("threshold sweep returned on request", len(full["test_curves"].get("threshold_sweep", [])) == 19)
    check("curves are the only difference", full["test_metrics"] == m, "metrics changed with include_curves")

    coefs = out.get("coefficients", {})
    terms = {t["feature"]: t for t in coefs.get("terms", [])}
    check("coefficients reported with odds ratios", all("odds_ratio" in t for t in terms.values()), str(terms))
    check("x1 is the strongest signal", max(terms, key=lambda k: abs(terms[k]["coef"])).endswith("x1"), str(terms))
    check("baseline included", (out.get("baseline", {}).get("roc_auc") or 0.5) == 0.5, str(out.get("baseline")))
    check("beats the no-skill baseline", (m.get("roc_auc") or 0) > (out["baseline"].get("roc_auc") or 0))
    check("solver converged", out.get("convergence", {}).get("converged") is True, str(out.get("convergence")))

    pre = out.get("preprocessing", {})
    check("numeric columns scaled", pre.get("numeric_columns") == ["x1", "x2"], str(pre))
    check("site one-hot encoded", pre.get("categorical_columns_one_hot") == ["site"], str(pre))
    check("encoded width counts the one-hot levels", pre.get("n_encoded_features") == 5, str(pre))
    check("provenance present", "scikit-learn" in out.get("package_versions", {}))


def test_fit_options(clf_path: str) -> None:
    print("\nfit_logistic_regression -- options")
    out = json.loads(
        server.fit_logistic_regression(
            data_path=clf_path, target_column="outcome", penalty="l1", C=0.5, class_weight="balanced"
        )
    )
    check("l1 auto-selects a compatible solver", out.get("model", {}).get("solver") == "saga", str(out.get("model")))
    check("class_weight recorded", out["model"].get("class_weight") == "balanced")

    out = json.loads(
        server.fit_logistic_regression(
            data_path=clf_path, target_column="outcome", penalty="l1", solver="lbfgs"
        )
    )
    check("incompatible solver is rejected", "cannot fit penalty" in out.get("error", ""), out.get("error", ""))

    out = json.loads(server.fit_logistic_regression(data_path=clf_path, target_column="outcome", threshold="youden"))
    thr = out.get("threshold", {})
    check("tuned threshold reported", thr.get("rule") == "youden", str(thr))
    check(
        "tuned on training out-of-fold predictions",
        thr.get("selected_on") == "training out-of-fold predictions",
        str(thr),
    )
    check("threshold applied to the metrics", out["test_metrics"]["threshold"] == thr["value"])

    out = json.loads(server.fit_logistic_regression(data_path=clf_path, target_column="outcome", threshold="1.5"))
    check("out-of-range threshold rejected", "threshold" in out.get("error", ""), out.get("error", ""))


def test_fit_multiclass(multi_path: str) -> None:
    print("\nfit_logistic_regression -- multiclass")
    out = json.loads(server.fit_logistic_regression(data_path=multi_path, target_column="grade"))
    check("no error", "error" not in out, out.get("error", ""))
    check("task type auto-detected", out.get("task_type") == "multiclass", str(out.get("task_type")))
    m = out.get("test_metrics", {})
    check("ovr auc beats chance", (m.get("roc_auc_ovr") or 0) > 0.7, str(m.get("roc_auc_ovr")))
    check("three classes reported", len(m.get("classes", [])) == 3)
    check("per-class metrics returned", len(m.get("per_class", [])) == 3)
    check("per-class coefficients returned", len(out.get("coefficients", {}).get("per_class", [])) == 3)


def test_fit_rejects_regression(reg_path: str) -> None:
    print("\nfit_logistic_regression -- continuous target")
    out = json.loads(server.fit_logistic_regression(data_path=reg_path, target_column="price"))
    check("continuous target refused", "continuous" in out.get("error", ""), out.get("error", ""))
    check("points at the regression tool", "run_linear_regression_script" in out.get("error", ""))


def test_cross_validate(clf_path: str) -> None:
    print("\ncross_validate_logistic_regression")
    out = json.loads(server.cross_validate_logistic_regression(data_path=clf_path, target_column="outcome"))
    check("no error", "error" not in out, out.get("error", ""))
    auc = out.get("cv_metrics", {}).get("roc_auc", {})
    check("mean auc reported", (auc.get("mean") or 0) > 0.9, str(auc))
    check("std reported", auc.get("std") is not None and auc["std"] >= 0, str(auc))
    check("five folds used", out.get("n_splits_used") == 5 and len(out.get("per_fold", [])) == 5)
    check("folds cover every row", sum(f["n"] for f in out["per_fold"]) == out["n_rows"])
    check("pooled out-of-fold metrics present", "roc_auc" in out.get("pooled_out_of_fold_metrics", {}))
    check("not grouped by default", out.get("grouped_folds") is False)

    out = json.loads(
        server.cross_validate_logistic_regression(data_path=clf_path, target_column="outcome", n_splits=999)
    )
    used = out.get("n_splits_used", 999)
    check("n_splits clamped to what the data supports", used < 999, str(used))


def test_cross_validate_grouped(grouped_path: str) -> None:
    print("\ncross_validate_logistic_regression -- grouped folds")
    plain = json.loads(server.cross_validate_logistic_regression(data_path=grouped_path, target_column="outcome"))
    grouped = json.loads(
        server.cross_validate_logistic_regression(
            data_path=grouped_path,
            target_column="outcome",
            split_json='{"group_column": "patient_id"}',
        )
    )
    check("grouped folds flagged", grouped.get("grouped_folds") is True, str(grouped.get("grouped_folds")))
    one_hot = grouped["preprocessing"]["categorical_columns_one_hot"]
    check("group column not used as a feature", "patient_id" not in one_hot)
    plain_auc = plain["cv_metrics"]["roc_auc"]["mean"]
    grouped_auc = grouped["cv_metrics"]["roc_auc"]["mean"]
    check("both produce an AUC", plain_auc is not None and grouped_auc is not None, f"{plain_auc} {grouped_auc}")


def test_tune(clf_path: str) -> None:
    print("\ntune_logistic_regression")
    out = json.loads(server.tune_logistic_regression(data_path=clf_path, target_column="outcome"))
    check("no error", "error" not in out, out.get("error", ""))
    check("winner reported", "C" in out.get("best_params", {}), str(out.get("best_params")))
    check("default grid searched", out["search"]["n_candidates"] == 8, str(out["search"]["n_candidates"]))
    check("leaderboard covers every candidate", len(out["search"]["leaderboard"]) == 8)
    check(
        "selection happened inside the training split",
        out["search"]["scored_on"] == "cross-validation within the training split only",
    )
    check("holdout metrics returned", (out.get("test_metrics", {}).get("roc_auc") or 0) > 0.9)
    check("leaderboard is sorted", [r["roc_auc"] for r in out["search"]["leaderboard"]] ==
          sorted((r["roc_auc"] for r in out["search"]["leaderboard"]), reverse=True))

    out = json.loads(
        server.tune_logistic_regression(
            data_path=clf_path, target_column="outcome", grid_json='{"C": [0.1, 1.0], "penalty": ["l2"]}'
        )
    )
    check("custom grid honored", out["search"]["n_candidates"] == 2, str(out["search"]))

    out = json.loads(
        server.tune_logistic_regression(data_path=clf_path, target_column="outcome", grid_json='{"nope": [1]}')
    )
    check("unknown grid key rejected", "unknown grid key" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.tune_logistic_regression(data_path=clf_path, target_column="outcome", selection_metric="nonsense")
    )
    check("bad selection_metric rejected", "selection_metric" in out.get("error", ""), out.get("error", ""))


def test_fit_random_forest(clf_path: str) -> None:
    print("\nfit_random_forest -- binary")
    out = json.loads(server.fit_random_forest(data_path=clf_path, target_column="outcome", n_estimators=100))
    check("no error", "error" not in out, out.get("error", ""))
    m = out.get("test_metrics", {})
    check("roc_auc is strong on separable data", (m.get("roc_auc") or 0) > 0.9, str(m.get("roc_auc")))
    check("estimator named", out.get("model", {}).get("estimator") == "RandomForestClassifier", str(out.get("model")))
    check("beats the no-skill baseline", (m.get("roc_auc") or 0) > (out["baseline"].get("roc_auc") or 0))

    imp = out.get("feature_importances", {}).get("impurity", {})
    terms = imp.get("terms", [])
    check("impurity importances reported", len(terms) > 0, str(imp))
    check("x1 is the most important feature", terms and terms[0]["feature"].endswith("x1"), str(terms[:2]))
    check("per-tree spread reported", all("std" in t for t in terms), str(terms[:2]))
    check("impurity bias caveat travels with them", "biased" in imp.get("note", ""), imp.get("note", ""))
    check("permutation withheld by default", "permutation" not in out["feature_importances"])

    oob = out.get("out_of_bag", {})
    check("out-of-bag estimate reported", (oob.get("roc_auc") or 0) > 0.9, str(oob))
    check("oob covers the training split", oob.get("n_rows_scored", 0) > 0.9 * oob.get("n_rows_total", 1), str(oob))

    pre = out.get("preprocessing", {})
    check("numerics imputed, not scaled", pre.get("numeric_columns_imputed") == ["x1", "x2"], str(pre))
    check("no scaling step, and it says why", "tree splits on thresholds" in pre.get("scaling", ""), str(pre))
    check("site one-hot encoded", pre.get("categorical_columns_one_hot") == ["site"], str(pre))
    check("split audited like the logistic tools", "split_sha256" in out, str(sorted(out)))


def test_fit_random_forest_options(clf_path: str) -> None:
    print("\nfit_random_forest -- options")
    out = json.loads(
        server.fit_random_forest(
            data_path=clf_path, target_column="outcome", n_estimators=60,
            max_features="0.5", min_samples_leaf=5, max_depth=4, class_weight="balanced",
        )
    )
    model = out.get("model", {})
    check("fractional max_features parsed", model.get("max_features") == 0.5, str(model))
    check("max_depth recorded", model.get("max_depth") == 4, str(model))
    check("class_weight recorded", model.get("class_weight") == "balanced", str(model))

    out = json.loads(
        server.fit_random_forest(data_path=clf_path, target_column="outcome", n_estimators=40, bootstrap=False)
    )
    check("unlimited depth is None, not 0", out.get("model", {}).get("max_depth") is None, str(out.get("model")))
    check("no out-of-bag block without bootstrap", "out_of_bag" not in out, str(out.get("out_of_bag")))

    out = json.loads(
        server.fit_random_forest(data_path=clf_path, target_column="outcome", n_estimators=40, max_features="nonsense")
    )
    check("bad max_features rejected", "max_features" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.fit_random_forest(data_path=clf_path, target_column="outcome", n_estimators=40, criterion="nope")
    )
    check("bad criterion rejected", "criterion must be one of" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.fit_random_forest(
            data_path=clf_path, target_column="outcome", n_estimators=40,
            bootstrap=False, class_weight="balanced_subsample",
        )
    )
    check("balanced_subsample needs bootstrap", "bootstrap=True" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.fit_random_forest(
            data_path=clf_path, target_column="outcome", n_estimators=40, permutation_importance=True, top_k_features=3
        )
    )
    perm = out.get("feature_importances", {}).get("permutation", {})
    check("permutation importance on request", len(perm.get("terms", [])) > 0, str(perm))
    check(
        "permutation reports raw columns, not encoded ones",
        {t["feature"] for t in perm["terms"]} <= {"x1", "x2", "site"},
        str(perm["terms"]),
    )

    out = json.loads(
        server.fit_random_forest(data_path=clf_path, target_column="outcome", n_estimators=40, threshold="youden")
    )
    thr = out.get("threshold", {})
    check("tuned on training out-of-fold predictions", thr.get("selected_on") == "training out-of-fold predictions", str(thr))


def test_random_forest_multiclass_and_regression(multi_path: str, reg_path: str) -> None:
    print("\nfit_random_forest -- multiclass and a continuous target")
    out = json.loads(server.fit_random_forest(data_path=multi_path, target_column="grade", n_estimators=60))
    check("task type auto-detected", out.get("task_type") == "multiclass", str(out.get("task_type")))
    check("ovr auc beats chance", (out.get("test_metrics", {}).get("roc_auc_ovr") or 0) > 0.7, str(out.get("test_metrics")))
    check("multiclass out-of-bag reported", (out.get("out_of_bag", {}).get("roc_auc_ovr") or 0) > 0.7, str(out.get("out_of_bag")))

    out = json.loads(server.fit_random_forest(data_path=reg_path, target_column="price", n_estimators=40))
    check("continuous target refused", "continuous" in out.get("error", ""), out.get("error", ""))


def test_cross_validate_random_forest(clf_path: str, grouped_path: str) -> None:
    print("\ncross_validate_random_forest")
    out = json.loads(
        server.cross_validate_random_forest(data_path=clf_path, target_column="outcome", n_estimators=60)
    )
    check("no error", "error" not in out, out.get("error", ""))
    auc = out.get("cv_metrics", {}).get("roc_auc", {})
    check("mean auc reported", (auc.get("mean") or 0) > 0.9, str(auc))
    check("five folds used", out.get("n_splits_used") == 5 and len(out.get("per_fold", [])) == 5)
    check("folds cover every row", sum(f["n"] for f in out["per_fold"]) == out["n_rows"])
    check("pooled out-of-fold metrics present", "roc_auc" in out.get("pooled_out_of_fold_metrics", {}))

    grouped = json.loads(
        server.cross_validate_random_forest(
            data_path=grouped_path, target_column="outcome", n_estimators=60,
            split_json='{"group_column": "patient_id"}',
        )
    )
    check("grouped folds flagged", grouped.get("grouped_folds") is True, str(grouped.get("grouped_folds")))
    check(
        "group column not used as a feature",
        "patient_id" not in grouped["preprocessing"]["categorical_columns_one_hot"],
        str(grouped["preprocessing"]),
    )


def test_tune_random_forest(clf_path: str) -> None:
    print("\ntune_random_forest")
    out = json.loads(server.tune_random_forest(data_path=clf_path, target_column="outcome", n_estimators=40))
    check("no error", "error" not in out, out.get("error", ""))
    check("default grid searched", out["search"]["n_candidates"] == 8, str(out["search"]["n_candidates"]))
    check("winner reported", "max_features" in out.get("best_params", {}), str(out.get("best_params")))
    check(
        "selection happened inside the training split",
        out["search"]["scored_on"] == "cross-validation within the training split only",
    )
    check("holdout metrics returned", (out.get("test_metrics", {}).get("roc_auc") or 0) > 0.9)
    check("winner's importances returned", len(out["feature_importances"]["impurity"]["terms"]) > 0)

    out = json.loads(
        server.tune_random_forest(
            data_path=clf_path, target_column="outcome", n_estimators=40,
            grid_json='{"min_samples_leaf": [1, 10]}',
        )
    )
    check("custom grid honored", out["search"]["n_candidates"] == 2, str(out["search"]))

    out = json.loads(
        server.tune_random_forest(data_path=clf_path, target_column="outcome", grid_json='{"C": [1.0]}')
    )
    check("a logistic grid key is rejected here", "unknown grid key" in out.get("error", ""), out.get("error", ""))


def test_leakage_audit_catches_duplicates(tmp: Path) -> None:
    print("\nleakage audit -- duplicated rows")
    rng = np.random.RandomState(4)
    base = pd.DataFrame({"x1": rng.normal(size=100), "x2": rng.normal(size=100)})
    base["outcome"] = (base["x1"] > 0).astype(int)
    path = tmp / "dupes.csv"
    pd.concat([base, base], ignore_index=True).to_csv(path, index=False)

    out = json.loads(server.describe_split(data_path=str(path), target_column="outcome"))
    split = out["split"]
    dupes = split.get("duplicate_feature_rows_in_both_splits", 0)
    check("duplicates across the split are counted", dupes > 0, str(split))
    warned = any("identical to a training row" in w for w in split["warnings"])
    check("and warned about", warned, str(split["warnings"]))


def test_linear_regression_script(reg_path: str) -> None:
    print("\nrun_linear_regression_script")
    code = """
model = LinearRegression()
model.fit(X_train, y_train)
def predict(X):
    return model.predict(X)
result = {"coef": model.coef_.tolist(), "intercept": float(model.intercept_)}
"""
    out = json.loads(server.run_linear_regression_script(code=code, data_path=reg_path, target_column="price"))
    check("no error", "error" not in out, out.get("error", ""))
    m = out.get("test_metrics", {})
    check("r2 recovers the generating model", (m.get("r2") or 0) > 0.99, str(m.get("r2")))
    check("rmse is near the noise scale", (m.get("rmse") or 9) < 0.2, str(m.get("rmse")))
    coef = out.get("model_decisions", {}).get("coef", [])
    recovered = len(coef) == 2 and abs(coef[0] - 3.0) < 0.05 and abs(coef[1] + 2.0) < 0.05
    check("coefficients recovered", recovered, str(coef))
    check("family recorded", out.get("model_family") == "linear_regression")


def test_logistic_script(clf_path: str) -> None:
    print("\nrun_logistic_regression_script")
    code = """
numeric = X_train.select_dtypes("number")
model = LogisticRegression(max_iter=500).fit(numeric, y_train)
def predict_proba(X):
    return model.predict_proba(X.select_dtypes("number"))[:, 1]
chosen_threshold = 0.4
result = {"n_features": numeric.shape[1]}
"""
    out = json.loads(server.run_logistic_regression_script(code=code, data_path=clf_path, target_column="outcome"))
    check("no error", "error" not in out, out.get("error", ""))
    check("roc_auc is strong", (out["test_metrics"].get("roc_auc") or 0) > 0.9, str(out["test_metrics"]))
    check("chosen_threshold honored", out["test_metrics"]["threshold"] == 0.4)
    check("operating points returned", "youden_j" in out.get("test_curves", {}).get("best_thresholds", {}))
    check("decisions echoed back", out.get("model_decisions") == {"n_features": 2})
    check("family recorded", out.get("model_family") == "logistic_regression")


def test_test_labels_are_not_reachable(clf_path: str) -> None:
    print("\nisolation -- the script cannot see the test split")
    code = """
result = {"saw": sorted(n for n in ("X_test", "y_test") if n in dir())}
def predict_proba(X):
    return np.zeros(len(X))
"""
    out = json.loads(server.run_logistic_regression_script(code=code, data_path=clf_path, target_column="outcome"))
    check("no error", "error" not in out, out.get("error", ""))
    check("X_test/y_test unbound in the script namespace", out.get("model_decisions", {}).get("saw") == [])
    check("train split is bound", (out.get("n_train") or 0) > 0)


def test_error_paths(clf_path: str, tmp: Path) -> None:
    print("\nerror handling")
    ok = "def predict_proba(X):\n    return np.zeros(len(X))\n"

    out = json.loads(server.fit_logistic_regression(data_path=str(tmp / "nope.csv"), target_column="outcome"))
    check("missing file reports an error", "no such file" in out.get("error", ""), out.get("error", ""))

    out = json.loads(server.fit_logistic_regression(data_path=clf_path, target_column="not_a_column"))
    check("unknown target lists real columns", "x1" in out.get("error", ""), out.get("error", ""))

    out = json.loads(server.fit_logistic_regression(data_path=clf_path, target_column="outcome", test_size=1.5))
    check("bad test_size rejected", "test_size" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.fit_logistic_regression(data_path=clf_path, target_column="outcome", split_json='{"oops": 1}')
    )
    check("unknown split_json key rejected", "unknown split_json" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.fit_logistic_regression(data_path=clf_path, target_column="outcome", split_json="{nope")
    )
    check("malformed split_json rejected", "not valid JSON" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.run_logistic_regression_script(
            code="raise ValueError('boom')", data_path=clf_path, target_column="outcome"
        )
    )
    check("script exception is captured", "boom" in out.get("error", ""), out.get("error", ""))
    check("traceback returned", "traceback" in out)

    out = json.loads(server.run_logistic_regression_script(code="x = 1", data_path=clf_path, target_column="outcome"))
    check("missing predict_proba is reported", "predict_proba" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.run_logistic_regression_script(
            code=ok, data_path=clf_path, target_column="outcome", task_type="nonsense"
        )
    )
    check("bad task_type rejected", "task_type" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.run_logistic_regression_script(
            code=ok, data_path=clf_path, target_column="outcome", payload_json="{oops"
        )
    )
    check("malformed payload_json rejected", "payload_json" in out.get("error", ""), out.get("error", ""))


def test_payload_is_bound(clf_path: str) -> None:
    print("\npayload_json binding")
    code = """
result = {"cap": hp["max_iter"]}
def predict_proba(X):
    return np.zeros(len(X))
"""
    out = json.loads(
        server.run_logistic_regression_script(
            code=code, data_path=clf_path, target_column="outcome", payload_json='{"max_iter": 700}'
        )
    )
    check("hp reached the script", out.get("model_decisions", {}).get("cap") == 700, out.get("error", ""))
    check("payload hashed", len(out.get("payload_sha256", "")) == 64)


def test_tool_schemas_survive_the_guard() -> None:
    print("\nFastMCP schema generation")
    import asyncio
    import inspect

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    expected = {
        "describe_dataset",
        "describe_split",
        "fit_logistic_regression",
        "cross_validate_logistic_regression",
        "tune_logistic_regression",
        "fit_random_forest",
        "cross_validate_random_forest",
        "tune_random_forest",
        "run_logistic_regression_script",
        "run_linear_regression_script",
        "ping",
    }
    check("every tool registered", names == expected, str(sorted(names ^ expected)))
    check("xgboost tool is gone", "run_xgboost_script" not in names)
    params = set(inspect.signature(server.fit_logistic_regression).parameters)
    check("the guard preserves real signatures", "target_column" in params and "kwargs" not in params, str(params))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        clf_path = _write_classification(tmp)
        reg_path = _write_regression(tmp)
        multi_path = _write_multiclass(tmp)
        grouped_path = _write_grouped(tmp)

        test_describe_dataset(clf_path)
        test_describe_dataset_spots_grouping(grouped_path)
        test_describe_split(clf_path)
        test_split_strategies(grouped_path)
        test_predefined_split(tmp, clf_path)
        test_fit_logistic_regression(clf_path)
        test_fit_options(clf_path)
        test_fit_multiclass(multi_path)
        test_fit_rejects_regression(reg_path)
        test_cross_validate(clf_path)
        test_cross_validate_grouped(grouped_path)
        test_tune(clf_path)
        test_fit_random_forest(clf_path)
        test_fit_random_forest_options(clf_path)
        test_random_forest_multiclass_and_regression(multi_path, reg_path)
        test_cross_validate_random_forest(clf_path, grouped_path)
        test_tune_random_forest(clf_path)
        test_leakage_audit_catches_duplicates(tmp)
        test_linear_regression_script(reg_path)
        test_logistic_script(clf_path)
        test_test_labels_are_not_reachable(clf_path)
        test_error_paths(clf_path, tmp)
        test_payload_is_bound(clf_path)
        test_tool_schemas_survive_the_guard()

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
