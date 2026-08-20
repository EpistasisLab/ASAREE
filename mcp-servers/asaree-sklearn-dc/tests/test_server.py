"""End-to-end tests for the asaree-sklearn-dc server tools (scratch-folder flow).

Run directly (matches the asaree-sklearn suites' style; pytest not required):

    PYTHONPATH=src python tests/test_server.py

Drives the primary DC flow — inspect_columns -> apply_coercions -> drop_and_impute
— against a real on-disk SCRATCH directory, passing ``workspace_id`` explicitly so
no FastMCP request context is needed. This server has no dependency on
asaree_workspace_core at all, so the test seeds the scratch dir itself, the same
plain train.parquet/test.parquet/meta.json asaree-workspace's open_workspace(...,
stage="dc") would produce — nothing here imports asaree_workspace_core.

ASAREE_DATASET_WORKSPACE_DIR is redirected to a tempdir BEFORE importing the
server module (the env var is read at call time, not import time, but seeding
happens before any tool call regardless).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

_TMP = tempfile.TemporaryDirectory()
os.environ["ASAREE_DATASET_WORKSPACE_DIR"] = _TMP.name

from asaree_sklearn_dc import server  # noqa: E402

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


def _seed(wid: str) -> None:
    """Write a synthetic train/test pair directly into this stage's scratch dir —
    standing in for asaree-workspace's open_workspace(..., stage="dc")."""
    n = 120
    rng = np.random.RandomState(0)
    # numeric-as-string with a censored token + free text (domain errors) placed
    # in the FIRST rows so they fall in the train split (index < split); the
    # domain fixer fits and counts violations on train only.
    lab = [str(v) for v in rng.randint(1, 40, n)]
    lab[2], lab[3] = "<5", "SEE NOTE"
    frame = {
        "lab": lab,
        # clean vital with one physiologically implausible outlier + a native NaN
        "vital": list(rng.normal(70, 3, n)),
        # very sparse column -> a drop candidate
        "sparse": [1.0] + [np.nan] * (n - 1),
        # a group/id column to protect from dropping
        "grp": (["a", "b", "c"] * (n // 3 + 1))[:n],
        "target": rng.randint(0, 2, n),
    }
    df = pd.DataFrame(frame)
    df.loc[0, "vital"] = 900.0  # implausible outlier (train)
    df.loc[1, "vital"] = np.nan  # native missing (train)
    split = n // 2
    scratch = server._scratch_dir(wid)
    scratch.mkdir(parents=True, exist_ok=True)
    df.iloc[:split].reset_index(drop=True).to_parquet(scratch / "train.parquet", index=False)
    df.iloc[split:].reset_index(drop=True).to_parquet(scratch / "test.parquet", index=False)
    (scratch / "meta.json").write_text(json.dumps({"target_column": "target"}))


def test_staged_flow() -> None:
    print("=== DC server: inspect -> coerce -> drop_and_impute (scratch flow) ===")
    wid = "expDC/cell0"
    _seed(wid)
    scratch = server._scratch_dir(wid)

    # 1. inspect_columns — read-only, writes nothing, target absent from the report.
    rep = json.loads(server.inspect_columns(workspace_id=wid))
    feats = {c["feature"] for c in rep["columns"]}
    check("inspect reports features, not target", feats == {"lab", "vital", "sparse", "grp"})
    by = {c["feature"]: c for c in rep["columns"]}
    check("inspect flags lab numeric-as-string", by["lab"]["n_non_numeric_tokens"] >= 1)
    check("inspect reports vital IQR outlier", by["vital"]["iqr"]["n_outliers"] >= 1)
    check("scratch unchanged after inspect", not (scratch / "learned.json").exists())

    # 2. drop_and_impute BEFORE coercion is refused — lab is still text.
    guard = json.loads(server.drop_and_impute(drop_json='["sparse"]', workspace_id=wid))
    check("drop_and_impute refuses untyped numeric-as-string", "error" in guard and "lab" in guard["error"])

    # 3. apply_coercions — type lab numeric, cap vital; returns post-coercion missingness.
    rules = json.dumps([
        {"feature": "lab", "type": "numeric", "min": 0, "reason": "integrity"},
        {"feature": "vital", "max": 300, "reason": "implausible outlier"},
    ])
    coerced = json.loads(server.apply_coercions(rules_json=rules, workspace_id=wid, run_id="run-1"))
    check("apply_coercions returns post-coercion missingness", "missingness_after_coercion" in coerced)
    viol = {v["feature"]: v["n_coerced"] for v in coerced["domain_violations"]}
    check("vital outlier coerced", viol.get("vital", 0) >= 1)
    check("lab tokens coerced", viol.get("lab", 0) >= 1)
    check("scratch train.parquet updated in place", (scratch / "train.parquet").is_file())
    check("run_id recorded", json.loads((scratch / "run_meta.json").read_text())["run_id"] == "run-1")

    # 4. drop_and_impute — drop sparse, protect grp, guard against emptying.
    done = json.loads(server.drop_and_impute(
        drop_json='["sparse", "grp", "ghost"]',
        protect_json='["grp"]',
        strategy_json='[{"feature": "vital", "strategy": "median"}]',
        workspace_id=wid,
        run_id="run-2",
    ))
    check("sparse dropped", done["dropped"] == ["sparse"])
    check("grp protected from drop", done["skipped_protected"] == ["grp"])
    check("ghost reported absent", done["skipped_absent"] == ["ghost"])
    check("zero missing after impute", done["n_missing_remaining"] == 0)

    # 5. The scratch output — what accept_stage would read and promote — is clean,
    # and learned.json merged BOTH tool calls' provenance (not just the last one).
    final_train = pd.read_parquet(scratch / "train.parquet")
    check("dropped column absent from scratch output", "sparse" not in final_train.columns)
    check("group column retained", "grp" in final_train.columns)
    check("scratch output has no missing values", int(final_train.isnull().sum().sum()) == 0)
    learned = json.loads((scratch / "learned.json").read_text())
    check("learned.json merged both tool calls", set(learned) == {"domain_rules", "domain_violations", "dropped_columns", "imputation"})
    check("run_id updated to the latest call", json.loads((scratch / "run_meta.json").read_text())["run_id"] == "run-2")


def test_empty_drop_guard() -> None:
    print("=== DC server: refuse to drop every column ===")
    wid = "expDC/cell1"
    _seed(wid)
    server.apply_coercions(rules_json='[{"feature": "lab", "type": "numeric"}]', workspace_id=wid)
    res = json.loads(server.drop_and_impute(
        drop_json='["lab", "vital", "sparse", "grp"]', workspace_id=wid
    ))
    check("refuses to empty the matrix", "error" in res and "every column" in res["error"])


def test_scratch_not_ready() -> None:
    print("=== DC server: clear error when scratch was never seeded ===")
    res = json.loads(server.inspect_columns(workspace_id="expDC/never-opened"))
    check("reports scratch-not-ready, not a crash", "error" in res and "open_workspace" in res["error"])


def main() -> int:
    test_staged_flow()
    test_empty_drop_guard()
    test_scratch_not_ready()
    print(f"\nResults: {_PASS}/{_PASS + _FAIL} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
