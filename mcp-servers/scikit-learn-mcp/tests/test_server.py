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
    n = 300
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = (x1 + 0.5 * x2 + rng.normal(scale=0.3, size=n) > 0).astype(int)
    path = tmp / "clf.csv"
    pd.DataFrame({"x1": x1, "x2": x2, "outcome": y}).to_csv(path, index=False)
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


def test_xgboost_binary(clf_path: str) -> None:
    print("\nrun_xgboost_script -- binary")
    code = """
model = XGBClassifier(n_estimators=40, max_depth=3, random_state=random_seed)
model.fit(X_train, y_train)
def predict_proba(X):
    return model.predict_proba(X)[:, 1]
result = {"n_estimators": 40}
"""
    out = json.loads(server.run_xgboost_script(code=code, data_path=clf_path, target_column="outcome"))
    check("no error", "error" not in out, out.get("error", ""))
    m = out.get("test_metrics", {})
    check("roc_auc is strong on separable data", (m.get("roc_auc") or 0) > 0.85, str(m.get("roc_auc")))
    check("confusion matrix totals the test split", sum(m.get("confusion_matrix", {}).values()) == out.get("n_test"))
    check("model_decisions echoed back", out.get("model_decisions") == {"n_estimators": 40})
    check("family recorded", out.get("model_family") == "xgboost")
    check("features exclude the target", out.get("feature_names") == ["x1", "x2"])
    check("provenance present", "scikit-learn" in out.get("package_versions", {}))


def test_xgboost_multiclass(multi_path: str) -> None:
    print("\nrun_xgboost_script -- multiclass")
    code = """
model = XGBClassifier(n_estimators=30, max_depth=3, random_state=random_seed)
model.fit(X_train, y_train)
def predict_proba(X):
    return model.predict_proba(X)
"""
    out = json.loads(
        server.run_xgboost_script(code=code, data_path=multi_path, target_column="grade", task_type="multiclass")
    )
    check("no error", "error" not in out, out.get("error", ""))
    m = out.get("test_metrics", {})
    check("accuracy beats chance", (m.get("accuracy") or 0) > 0.5, str(m.get("accuracy")))
    check("three classes reported", len(m.get("classes", [])) == 3)


def test_xgboost_regression(reg_path: str) -> None:
    print("\nrun_xgboost_script -- regression")
    code = """
model = XGBRegressor(n_estimators=60, max_depth=3, random_state=random_seed)
model.fit(X_train, y_train)
def predict(X):
    return model.predict(X)
"""
    out = json.loads(
        server.run_xgboost_script(code=code, data_path=reg_path, target_column="price", task_type="regression")
    )
    check("no error", "error" not in out, out.get("error", ""))
    check("r2 is high on a linear signal", (out.get("test_metrics", {}).get("r2") or 0) > 0.9)


def test_linear_regression(reg_path: str) -> None:
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


def test_test_labels_are_not_reachable(clf_path: str) -> None:
    print("\nisolation -- the script cannot see the test split")
    code = """
result = {"saw": sorted(n for n in ("X_test", "y_test") if n in dir())}
def predict_proba(X):
    return np.zeros(len(X))
"""
    out = json.loads(server.run_xgboost_script(code=code, data_path=clf_path, target_column="outcome"))
    check("no error", "error" not in out, out.get("error", ""))
    check("X_test/y_test unbound in the script namespace", out.get("model_decisions", {}).get("saw") == [])
    check("train split is bound", (out.get("n_train") or 0) > 0)


def test_error_paths(clf_path: str, tmp: Path) -> None:
    print("\nerror handling")
    ok = "def predict_proba(X):\n    return np.zeros(len(X))\n"

    out = json.loads(server.run_xgboost_script(code=ok, data_path=str(tmp / "nope.csv"), target_column="outcome"))
    check("missing file reports an error", "no such file" in out.get("error", ""), out.get("error", ""))

    out = json.loads(server.run_xgboost_script(code=ok, data_path=clf_path, target_column="not_a_column"))
    check("unknown target lists real columns", "x1" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.run_xgboost_script(code="raise ValueError('boom')", data_path=clf_path, target_column="outcome")
    )
    check("script exception is captured", "boom" in out.get("error", ""), out.get("error", ""))
    check("traceback returned", "traceback" in out)

    out = json.loads(server.run_xgboost_script(code="x = 1", data_path=clf_path, target_column="outcome"))
    check("missing predict_proba is reported", "predict_proba" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.run_xgboost_script(code=ok, data_path=clf_path, target_column="outcome", task_type="nonsense")
    )
    check("bad task_type rejected", "task_type" in out.get("error", ""), out.get("error", ""))

    out = json.loads(server.run_xgboost_script(code=ok, data_path=clf_path, target_column="outcome", test_size=1.5))
    check("bad test_size rejected", "test_size" in out.get("error", ""), out.get("error", ""))

    out = json.loads(
        server.run_xgboost_script(code=ok, data_path=clf_path, target_column="outcome", payload_json="{oops")
    )
    check("malformed payload_json rejected", "payload_json" in out.get("error", ""), out.get("error", ""))


def test_payload_is_bound(clf_path: str) -> None:
    print("\npayload_json binding")
    code = """
result = {"depth": hp["max_depth"]}
def predict_proba(X):
    return np.zeros(len(X))
"""
    out = json.loads(
        server.run_xgboost_script(
            code=code, data_path=clf_path, target_column="outcome", payload_json='{"max_depth": 7}'
        )
    )
    check("hp reached the script", out.get("model_decisions", {}).get("depth") == 7, out.get("error", ""))
    check("payload hashed", len(out.get("payload_sha256", "")) == 64)


def test_describe_dataset(clf_path: str) -> None:
    print("\ndescribe_dataset")
    out = json.loads(server.describe_dataset(data_path=clf_path))
    check("row count", out.get("n_rows") == 300, str(out.get("n_rows")))
    check("column count", out.get("n_columns") == 3)
    check("names listed", [c["name"] for c in out.get("columns", [])] == ["x1", "x2", "outcome"])
    check("ping", server.ping() == "pong")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        clf_path = _write_classification(tmp)
        reg_path = _write_regression(tmp)
        multi_path = _write_multiclass(tmp)

        test_xgboost_binary(clf_path)
        test_xgboost_multiclass(multi_path)
        test_xgboost_regression(reg_path)
        test_linear_regression(reg_path)
        test_test_labels_are_not_reachable(clf_path)
        test_error_paths(clf_path, tmp)
        test_payload_is_bound(clf_path)
        test_describe_dataset(clf_path)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
