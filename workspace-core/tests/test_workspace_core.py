"""Unit tests for asaree-workspace-core (split out of ares-sklearn-core, issue #1456).

Run directly (matches the ares-sklearn suites' style; pytest is not required):

    PYTHONPATH=src python tests/test_workspace_core.py

Covers the context-driven resolution acceptance criterion: a tool resolves its
matrix from the on-disk workspace HEAD using only the ambient workspace_id —
no dataset_id, no in-memory session.
"""

from __future__ import annotations

import json
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
    # Also assert, so `pytest` (which imports these as test_ functions and
    # ignores main()'s exit code) actually fails on a regression instead of
    # printing FAIL and reporting green.
    assert cond, f"{name}  {detail}"


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


def test_owner_id_resolution() -> None:
    print("=== owner_id ambient resolution ===")
    meta = {core.META_KEY_OWNER_ID: "user-123"}
    check("owner_id_from_meta reads the key", core.owner_id_from_meta(meta) == "user-123")
    check("owner_id_from_meta absent -> empty string", core.owner_id_from_meta({}) == "")
    check("owner_id_from_meta None -> empty string", core.owner_id_from_meta(None) == "")
    # Optional by default (unlike workspace_id) — most tool calls don't need it.
    check("resolve_owner_id_from_ctx(None) does not raise", core.resolve_owner_id_from_ctx(None) == "")
    raised = False
    try:
        core.resolve_owner_id_from_ctx(None, required=True)
    except core.WorkspaceError:
        raised = True
    check("resolve_owner_id_from_ctx(required=True) raises when absent", raised)


def test_dataset_name_resolution() -> None:
    print("=== dataset name ambient resolution ===")
    one = {core.META_KEY_DATASET_NAMES: ["spinal"]}
    many = {core.META_KEY_DATASET_NAMES: ["cohort-a", "cohort-b"]}
    check("names read off the key", core.dataset_names_from_meta(many) == ["cohort-a", "cohort-b"])
    check("absent -> empty list", core.dataset_names_from_meta({}) == [])
    check("malformed -> empty list", core.dataset_names_from_meta({core.META_KEY_DATASET_NAMES: "spinal"}) == [])
    check("non-string entries dropped", core.dataset_names_from_meta({core.META_KEY_DATASET_NAMES: [1, "a", ""]}) == ["a"])

    check("explicit wins", core.resolve_dataset_name("chosen", many) == "chosen")
    check("one wired -> ambient fallback", core.resolve_dataset_name("", one) == "spinal")
    # The whole point of the len==1 guard: with a real choice to make, refuse
    # rather than silently read whichever happens to be first.
    check("several wired -> no guess", core.resolve_dataset_name("", many) == "")
    check("none wired -> empty", core.resolve_dataset_name("", None) == "")


def _write_seed(root: str, name: str, *, seed: int = 0) -> tuple[str, str]:
    """Write a synthetic pre-split parquet pair under ``{root}/_seed_{name}``."""
    X_train, y_train, X_test, y_test = make_split(seed)
    train_df = X_train.copy()
    train_df["target"] = y_train.to_numpy()
    test_df = X_test.copy()
    test_df["target"] = y_test.to_numpy()
    seed_dir = Path(root) / f"_seed_{name}"
    seed_dir.mkdir(parents=True, exist_ok=True)
    train_path = seed_dir / "train.parquet"
    test_path = seed_dir / "test.parquet"
    train_df.to_parquet(train_path, index=False)
    test_df.to_parquet(test_path, index=False)
    return str(train_path), str(test_path)


def test_format_1_state_reads_as_one_slot() -> None:
    """The backward-compatibility guarantee: a state.json written before slots
    existed keeps working, keeps its HEAD, and is NOT rewritten by reading it."""
    print("=== format 1 state.json (pre-slots, on disk) ===")
    with tempfile.TemporaryDirectory() as root:
        wid = "exp1/cellA"
        train_path, test_path = _write_seed(root, "legacy")
        # Hand-write the exact flat shape the pre-slot code produced.
        wdir = Path(root) / "exp1" / "cellA"
        wdir.mkdir(parents=True)
        legacy = {
            "target_column": "target",
            "head": "v0_raw",
            "versions": [
                {"id": "v0_raw", "stage": None, "train": train_path, "test": test_path,
                 "sha256_train": "", "sha256_test": "", "accepted": True}
            ],
        }
        state_path = wdir / "state.json"
        state_path.write_text(json.dumps(legacy, indent=1) + "\n")
        before = state_path.read_text()

        ws = Workspace(wid, root=root)
        check("legacy file resolves to the single legacy slot", ws.slot == core.LEGACY_SLOT)
        check("legacy HEAD unchanged", ws.load_state()["head"] == "v0_raw")
        check("legacy target_column unchanged", ws.target_column == "target")
        check("legacy version dirs stay at the workspace root",
              ws.version_dir("v1_dc") == wdir / "v1_dc")
        check("legacy manifests stay at the workspace root", ws.manifests_dir == wdir / "manifests")
        Xtr, _, _, _ = core.resolve_matrix_from_head(wid, root=root)
        check("legacy HEAD matrix still resolves", len(Xtr) == 100)
        check("reading did not rewrite the file", state_path.read_text() == before)

        # A named slot resolves to it too: format 1 recorded no name, so the
        # sole slot answers for whatever name the caller asks about.
        named = Workspace(wid, root=root, slot="dataset:whatever-it-was")
        check("a named slot resolves against a nameless legacy file",
              named.slot == core.LEGACY_SLOT)

        # And a write keeps the file in format 1 — the format moves when a
        # second slot arrives, not when something touches the workspace.
        sXtr, sytr, sXte, syte = core.resolve_stage_input(wid, "dc", root=root)
        staging.commit_stage(wid, "dc", X_train=sXtr, y_train=sytr, X_test=sXte,
                             y_test=syte, learned={}, root=root)
        ws.accept_stage("dc")
        written = json.loads(state_path.read_text())
        check("a write keeps format 1 on disk", "slots" not in written)
        check("accept still advances HEAD", written["head"] == "v1_dc")
        check("v1_dc parquet written at the root layout",
              (wdir / "v1_dc" / "train.parquet").is_file())


def test_two_datasets_get_independent_slots() -> None:
    """Two datasets in one cell — the case a single implicit lineage rejected."""
    print("=== two dataset slots in one workspace ===")
    with tempfile.TemporaryDirectory() as root:
        wid = "exp2/cellA"
        a_train, a_test = _write_seed(root, "a", seed=1)
        b_train, b_test = _write_seed(root, "b", seed=2)
        a = Workspace.open(wid, target_column="target", seed_train_path=a_train,
                           seed_test_path=a_test, root=root, slot=core.dataset_slot("cohort-a"))
        b = Workspace.open(wid, target_column="target", seed_train_path=b_train,
                           seed_test_path=b_test, root=root, slot=core.dataset_slot("cohort-b"))
        check("two slots recorded", sorted(a.slots()) == ["dataset:cohort-a", "dataset:cohort-b"])
        check("slot dirs are distinct",
              a.version_dir("v1_dc") != b.version_dir("v1_dc"))

        # Stage cohort-a only; cohort-b must be untouched.
        sXtr, sytr, sXte, syte = core.resolve_stage_input(
            wid, "dc", root=root, slot="dataset:cohort-a"
        )
        staging.commit_stage(wid, "dc", X_train=sXtr.assign(marker=1.0), y_train=sytr,
                             X_test=sXte.assign(marker=1.0), y_test=syte, learned={},
                             root=root, slot="dataset:cohort-a")
        a.accept_stage("dc")
        check("cohort-a HEAD advanced", a.load_state()["head"] == "v1_dc")
        check("cohort-b HEAD untouched", b.load_state()["head"] == "v0_raw")
        aX, _, _, _ = core.resolve_matrix_from_head(wid, root=root, slot="dataset:cohort-a")
        bX, _, _, _ = core.resolve_matrix_from_head(wid, root=root, slot="dataset:cohort-b")
        check("cohort-a HEAD is its own staged matrix", "marker" in aX.columns)
        check("cohort-b HEAD is still its own seed", "marker" not in bX.columns)

        # Re-opening from the same seed returns the same slot rather than a
        # second copy — a resumed cell re-seeds every wired dataset.
        again = Workspace.open(wid, target_column="target", seed_train_path=a_train,
                               seed_test_path=a_test, root=root,
                               slot=core.dataset_slot("cohort-a"))
        check("re-open is idempotent", len(again.slots()) == 2)
        check("re-open keeps the staged HEAD", again.load_state()["head"] == "v1_dc")

        # A slot holds one dataset: reusing a key for a different seed is an error.
        raised = False
        try:
            Workspace.open(wid, target_column="target", seed_train_path=b_train,
                           seed_test_path=b_test, root=root,
                           slot=core.dataset_slot("cohort-a"))
        except core.WorkspaceError:
            raised = True
        check("reusing a slot key for another dataset is refused", raised)

        # An agent scratch slot is its own lineage even when it starts from a
        # dataset another slot also holds — that is the point of a scratch slot.
        scratch = Workspace.open(wid, target_column="target", seed_train_path=a_train,
                                 seed_test_path=a_test, root=root,
                                 slot=core.agent_slot("dndnode_3"))
        check("an agent slot is its own lineage", scratch.slot == "agent:dndnode_3")
        check("the scratch slot starts at the seed", scratch.load_state()["head"] == "v0_raw")
        check("the dataset slot it copied from is unaffected",
              a.load_state()["head"] == "v1_dc")
        check("three slots now", len(scratch.slots()) == 3)


def test_ambiguous_slot_is_an_error_naming_the_candidates() -> None:
    """Never a guess: with several slots and no slot argument, say which exist."""
    print("=== ambiguous slot resolution ===")
    with tempfile.TemporaryDirectory() as root:
        wid = "exp3/cellA"
        a_train, a_test = _write_seed(root, "a", seed=1)
        b_train, b_test = _write_seed(root, "b", seed=2)
        Workspace.open(wid, target_column="target", seed_train_path=a_train,
                       seed_test_path=a_test, root=root, slot=core.dataset_slot("cohort-a"))
        Workspace.open(wid, target_column="target", seed_train_path=b_train,
                       seed_test_path=b_test, root=root, slot=core.dataset_slot("cohort-b"))
        message = ""
        try:
            Workspace(wid, root=root).load_state()
        except core.WorkspaceError as exc:
            message = str(exc)
        check("ambiguity raises", bool(message), message)
        check("the error names both candidates",
              "cohort-a" in message and "cohort-b" in message, message)

        unknown = ""
        try:
            Workspace(wid, root=root, slot=core.dataset_slot("cohort-z")).load_state()
        except core.WorkspaceError as exc:
            unknown = str(exc)
        check("an unknown slot names the candidates too",
              "cohort-a" in unknown and "cohort-b" in unknown, unknown)

        # A bare dataset name (what a model is likely to type) resolves by the
        # recorded name rather than requiring the namespace prefix.
        bare = Workspace(wid, root=root, slot="cohort-b")
        check("a bare dataset name resolves", bare.slot == "dataset:cohort-b")

        # The ambient fallback only fires with exactly one dataset wired.
        many = {core.META_KEY_DATASET_NAMES: ["cohort-a", "cohort-b"]}
        check("slot: one wired -> ambient slot",
              core.resolve_slot("", {core.META_KEY_DATASET_NAMES: ["cohort-a"]})
              == "dataset:cohort-a")
        check("slot: several wired -> no guess", core.resolve_slot("", many) is None)
        check("slot: explicit wins", core.resolve_slot("dataset:x", many) == "dataset:x")


def main() -> int:
    test_context_resolution()
    test_provenance()
    test_owner_id_resolution()
    test_dataset_name_resolution()
    test_format_1_state_reads_as_one_slot()
    test_two_datasets_get_independent_slots()
    test_ambiguous_slot_is_an_error_naming_the_candidates()
    print(f"\nResults: {_PASS}/{_PASS + _FAIL} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
