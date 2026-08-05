"""Unit tests for asaree-workspace-core (split out of ares-sklearn-core, issue #1456).

Run directly (matches the ares-sklearn suites' style; pytest is not required):

    PYTHONPATH=src python tests/test_workspace_core.py

Covers the context-driven resolution acceptance criterion: a tool resolves its
matrix from the on-disk workspace HEAD using only the ambient workspace_id —
no dataset_id, no in-memory session.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import asaree_workspace_core as core
from asaree_workspace_core import staging
from asaree_workspace_core.workspace import Workspace

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


def _seed_workspace(root: str, wid: str) -> Workspace:
    """Write synthetic v0 parquets and open a workspace seeded from them."""
    X_train, y_train, X_test, y_test = make_split()
    train_df = X_train.copy()
    train_df["target"] = y_train.to_numpy()
    test_df = X_test.copy()
    test_df["target"] = y_test.to_numpy()
    seed_dir = Path(root) / "_seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    train_path = seed_dir / "train.parquet"
    test_path = seed_dir / "test.parquet"
    train_df.to_parquet(train_path, index=False)
    test_df.to_parquet(test_path, index=False)
    return Workspace.open(
        wid,
        target_column="target",
        seed_train_path=str(train_path),
        seed_test_path=str(test_path),
        root=root,
    )


def test_context_resolution() -> None:
    print("=== Context-driven resolution (no dataset_id) ===")
    with tempfile.TemporaryDirectory() as root:
        wid = "exp1/cellA"
        _seed_workspace(root, wid)

        # workspace_id resolves from an ambient _meta mapping (the #1455 channel).
        meta = {core.META_KEY_WORKSPACE_ID: wid}
        check("resolve_workspace_id from meta", core.resolve_workspace_id("", meta) == wid)
        check("explicit arg wins over meta",
              core.resolve_workspace_id("exp/other", meta) == "exp/other")
        raised = False
        try:
            core.resolve_workspace_id("", None, required=True)
        except core.WorkspaceError:
            raised = True
        check("resolve_workspace_id fails loud when absent", raised)

        # A "tool built on core" resolves its matrix from HEAD keyed ONLY by
        # workspace_id — no dataset_id anywhere.
        Xtr, ytr, Xte, yte = core.resolve_matrix_from_head(wid, root=root)
        check("HEAD resolves seed matrix", len(Xtr) == 100 and "target" not in Xtr.columns)

        # Before any DC commit, the stage working copy IS the stage input (seed).
        w0 = core.resolve_stage_working(wid, "dc", root=root)
        check("stage working falls back to input when uncommitted", len(w0[0]) == 100)

        # End-to-end leakage-safe flow through the core: read stage input, fit on
        # train only, apply to both, commit, accept, and see HEAD advance.
        sXtr, sytr, sXte, syte = core.resolve_stage_input(wid, "dc", root=root)
        Xtr2 = sXtr.copy()
        Xtr2.loc[0, "signal"] = np.nan
        fill = float(pd.to_numeric(Xtr2["signal"], errors="coerce").mean())
        imputed_train = Xtr2.assign(signal=Xtr2["signal"].fillna(fill))
        imputed_test = sXte.assign(signal=sXte["signal"].fillna(fill))
        staging.commit_stage(
            wid, "dc", X_train=imputed_train, y_train=sytr,
            X_test=imputed_test, y_test=syte, learned={"imputation": {"signal": fill}}, root=root,
        )
        ws = Workspace(wid, root=root)
        check("stage committed, HEAD not yet advanced", ws.load_state()["head"] == "v0_raw")
        # Intra-stage chaining: a second DC tool reads the committed (unaccepted)
        # v1_dc working copy, not the seed — no session needed.
        wXtr, _, _, _ = core.resolve_stage_working(wid, "dc", root=root)
        check("stage working reads committed unaccepted version",
              int(wXtr["signal"].isna().sum()) == 0 and ws.load_state()["head"] == "v0_raw")
        ws.accept_stage("dc")
        check("accept advances HEAD to v1_dc", ws.load_state()["head"] == "v1_dc")
        head_Xtr, _, _, _ = core.resolve_matrix_from_head(wid, root=root)
        check("HEAD now the imputed matrix (no NaN)", int(head_Xtr["signal"].isna().sum()) == 0)


def test_provenance() -> None:
    print("=== provenance hash ===")
    from asaree_workspace_core import provenance

    X_train, y_train, _, _ = make_split()
    h1 = provenance.data_sha256(X_train, X_train, y_train, y_train, "target")
    h2 = provenance.data_sha256(X_train, X_train, y_train, y_train, "target")
    check("data_sha256 deterministic", h1 == h2 and len(h1) == 64)


def main() -> int:
    test_context_resolution()
    test_provenance()
    print(f"\nResults: {_PASS}/{_PASS + _FAIL} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
