"""On-disk versioned workspace for the ASAREE pipeline (issues #1452 / #1453).

A *workspace* is a per-cell directory holding versioned ``{train,test}.parquet``
pairs, a ``state.json`` pointer, and per-stage JSON manifests. It replaces the
fitted-transformer artifacts that were persisted to Postgres by UUID key and
threaded by hand across agents — a behavioral contract that silently failed
when an agent skipped ``save_artifact``.

Why disk (not the in-memory Session, not Postgres): agent tool calls run in the
arq **worker** process while the notebook's direct ``run_model_script`` call runs
in the **backend** process — two different MCP sessions. The bind-mounted
filesystem (``/app/data`` on both) is the shared, inspectable handoff channel.

Leakage safety is unchanged and structural: the caller fits every statistic on
the **train** partition only and applies it to both; this module just persists
the resulting pair. The test partition is written but never surfaced to the agent.

Layout (format 1 — one implicit dataset per cell)::

    {root}/{experiment_id}/{cell_label}/
        state.json
        v1_dc/   {train,test}.parquet
        v2_fte/  {train,test}.parquet
        v3_fs/   {train,test}.parquet
        manifests/  {dc,fte,fs}.json

Layout (format 2 — named slots)::

    {root}/{experiment_id}/{cell_label}/
        state.json                      {"format_version": 2, "slots": {...}}
        dataset=spinal/v1_dc/  {train,test}.parquet
        dataset=spinal/manifests/  {dc,fte,fs}.json
        dataset=controls/v1_dc/  ...
        agent=dndnode_3/...

``v0_raw`` is not copied — it references the registered upload's parquet paths,
which are already the frozen train/test split.

Slots
-----
A *slot* is an independently staged lineage inside one cell's workspace: its own
``target_column``, its own ``versions`` list and its own HEAD. Two namespaces
use it today — ``dataset:<name>`` for a seeded registration, and
``agent:<node_id>`` for one agent's private scratch — but the mechanism is
general, which is the point: multi-dataset pipelines, parallel workers writing
without contention, and the per-stage scratch dirs are all the same question of
"whose lineage is this".

**Format 1 is not migrated on disk.** A reader that finds a state file with no
``format_version`` upgrades it *in memory* to a single :data:`LEGACY_SLOT`, and
a writer keeps writing format 1 for as long as that stays the only slot. Real
published workspaces exist on disk; an in-memory upgrade leaves a rollback
possible where an on-disk one would not, and the format only changes when a
second slot genuinely arrives.

**Ambiguity is an error, never a guess.** Every slot-taking method defaults to
the sole slot when there is exactly one -- which is what makes a single-dataset
caller unaware that slots exist -- and raises naming the candidates when there
is more than one. Silently picking the first would be a guess dressed as a
default, the same reason ``resolve_dataset_name`` refuses to choose.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

# ASAREE's own workspace-server process spawns as a child of the ASAREE
# backend (stdio), so it shares the backend's filesystem automatically — no
# bind mount needed (unlike ARES's separate backend/worker containers, the
# reason this was ever env-configurable at all). Defaults to a path relative
# to wherever that process's cwd is, matching dataset_storage_dir's own
# default convention (asaree.config.AsareeSettings).
WORKSPACE_ROOT = os.environ.get("ASAREE_DATASET_WORKSPACE_DIR", "./data/workspaces")

# Ordered pipeline stages and their canonical output version. Each stage reads
# the previous stage's accepted output (or the v0 seed for the first stage).
STAGES: list[str] = ["dc", "fte", "fs"]
STAGE_VERSION: dict[str, str] = {"dc": "v1_dc", "fte": "v2_fte", "fs": "v3_fs"}
SEED_VERSION = "v0_raw"

# A workspace_id is "{experiment_id}/{cell_label}". Each component must be a
# plain, traversal-safe token (no separators, no parent refs, not absolute).
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=,-]*$")

# The slot a format-1 state file is read as, and the slot every single-dataset
# workspace keeps using. It deliberately lives at the workspace root rather than
# in a "dataset=default/" subdirectory: version dirs and manifests for this slot
# must stay exactly where format 1 put them, including after the workspace gains
# a second slot and the state file moves to format 2.
LEGACY_SLOT = "dataset:default"

DATASET_SLOT_PREFIX = "dataset:"
AGENT_SLOT_PREFIX = "agent:"

CURRENT_FORMAT_VERSION = 2


class WorkspaceError(Exception):
    """Raised for malformed workspace ids or inconsistent on-disk state."""


def dataset_slot(name: str) -> str:
    """The slot key for a seeded dataset registration."""
    return f"{DATASET_SLOT_PREFIX}{name}"


def agent_slot(node_id: str) -> str:
    """The slot key for one agent's private scratch lineage."""
    return f"{AGENT_SLOT_PREFIX}{node_id}"


def _slot_token(slot: str) -> str:
    """A slot key as a single traversal-safe path component.

    ``:`` is not in ``_SAFE_COMPONENT``'s alphabet (and is awkward in paths on
    some filesystems), so the namespace separator becomes ``=``:
    ``dataset:spinal`` -> ``dataset=spinal``. The prefix is kept so the two
    namespaces can never collide on a shared name.
    """
    return _safe(slot.replace(":", "="), what="slot")


def _safe(component: str, *, what: str) -> str:
    component = (component or "").strip()
    if not component or component in {".", ".."} or not _SAFE_COMPONENT.match(component):
        raise WorkspaceError(f"unsafe {what}: {component!r}")
    return component


def make_workspace_id(experiment_id: str, cell_label: str) -> str:
    """Compose a workspace id from an experiment id and a (safe) cell label."""
    return f"{_safe(experiment_id, what='experiment_id')}/{_safe(cell_label, what='cell_label')}"


def _resolve_dir(workspace_id: str, root: str | None = None) -> Path:
    """Map a workspace id to its directory, rejecting path traversal."""
    base = Path(root or WORKSPACE_ROOT).resolve()
    parts = (workspace_id or "").split("/")
    if len(parts) != 2:
        raise WorkspaceError(
            f"workspace_id must be 'experiment_id/cell_label', got {workspace_id!r}"
        )
    experiment_id = _safe(parts[0], what="experiment_id")
    cell_label = _safe(parts[1], what="cell_label")
    resolved = (base / experiment_id / cell_label).resolve()
    if base not in resolved.parents:
        raise WorkspaceError(f"resolved path escapes workspace root: {resolved}")
    return resolved


def _file_sha256(path: Path) -> str:
    """Content hash of a written parquet file (tamper-evidence / provenance).

    Deterministic for identical data under our single pinned pandas/pyarrow, which
    is all data_sha256 needs — it is a within-deployment audit anchor, not a
    cross-version invariant.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _seed_train(slot_state: dict[str, Any]) -> str:
    """The v0 seed's train path for one slot — its dataset identity."""
    seed = next(
        (v for v in slot_state.get("versions", []) if v.get("id") == SEED_VERSION), None
    )
    return str((seed or {}).get("train") or "")


@dataclass
class VersionRef:
    """One materialized dataset version's on-disk location + provenance."""

    id: str
    stage: str | None
    train: str
    test: str
    sha256_train: str
    sha256_test: str
    accepted: bool
    run_id: str = ""


class Workspace:
    """One slot of a per-cell versioned dataset workspace on disk.

    An instance is bound to a single slot: ``load_state`` returns *that slot's*
    ``{target_column, head, versions}`` and every read/write path is scoped to
    it, which is why the staging methods below never mention slots. Pass
    ``slot=None`` (the common case) to bind to the sole slot.
    """

    def __init__(self, workspace_id: str, root: str | None = None, *, slot: str | None = None) -> None:
        self.workspace_id = workspace_id
        self.dir = _resolve_dir(workspace_id, root)
        self.requested_slot = slot

    # --- paths ---

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def slot(self) -> str:
        """The slot key this instance resolves to against the state on disk."""
        return self._resolve_slot(self._document())

    @property
    def slot_dir(self) -> Path:
        slot = self.slot
        return self.dir if slot == LEGACY_SLOT else self.dir / _slot_token(slot)

    @property
    def manifests_dir(self) -> Path:
        return self.slot_dir / "manifests"

    def version_dir(self, version_id: str) -> Path:
        return self.slot_dir / _safe(version_id, what="version_id")

    def exists(self) -> bool:
        return self.state_path.is_file()

    # --- state ---

    def _document(self) -> dict[str, Any]:
        """The whole state file, normalized to the slot shape *in memory only*.

        A format-1 file (no ``format_version``, no ``slots``) is the flat
        ``{target_column, head, versions}`` document every workspace written
        before slots existed has. It reads as a one-slot document and is never
        rewritten on that account — see the module docstring.
        """
        if not self.exists():
            raise WorkspaceError(f"workspace not initialized: {self.workspace_id}")
        raw = json.loads(self.state_path.read_text())
        if isinstance(raw.get("slots"), dict):
            return raw
        return {"format_version": 1, "slots": {LEGACY_SLOT: raw}}

    def _save_document(self, doc: dict[str, Any]) -> None:
        slots = doc.get("slots") or {}
        # Write format 1 back as format 1 for as long as the legacy slot is the
        # only one. The format changes when a second slot actually arrives, not
        # because something read the file.
        payload: dict[str, Any]
        if list(slots) == [LEGACY_SLOT]:
            payload = dict(slots[LEGACY_SLOT])
            # ``name`` is a format-2 field, and the legacy slot's would be the
            # placeholder "default" rather than a dataset anyone named -- so it
            # is dropped, keeping a format-1 file byte-shaped exactly as before
            # slots existed. Resolution does not need it: a sole slot answers to
            # any name (see _resolve_slot).
            payload.pop("name", None)
        else:
            payload = {"format_version": CURRENT_FORMAT_VERSION, "slots": slots}
        self.dir.mkdir(parents=True, exist_ok=True)
        # Atomic write: a crash mid-write must never truncate the pointer of record.
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(self.state_path)

    def _resolve_slot(self, doc: dict[str, Any]) -> str:
        """Which slot of ``doc`` this instance addresses.

        Resolution order: the exact key, then a slot recording that ``name``,
        then — only when there is exactly one slot — that slot. The last rule is
        what lets a caller name a dataset that a format-1 file never recorded,
        and it is safe precisely because a single slot leaves nothing to choose
        between. Anything else raises naming the candidates.
        """
        slots: dict[str, Any] = doc.get("slots") or {}
        names = list(slots)
        if not names:
            raise WorkspaceError(f"workspace {self.workspace_id} has no slots")
        requested = self.requested_slot
        if requested is None:
            if len(names) == 1:
                return names[0]
            raise WorkspaceError(
                f"workspace {self.workspace_id} holds several slots "
                f"({', '.join(sorted(names))}) — name the one to use"
            )
        if requested in slots:
            return requested
        bare = requested.split(":", 1)[-1]
        by_name = [key for key, state in slots.items() if str(state.get("name") or "") == bare]
        if len(by_name) == 1:
            return by_name[0]
        if len(names) == 1:
            return names[0]
        raise WorkspaceError(
            f"workspace {self.workspace_id} has no slot {requested!r} — "
            f"available: {', '.join(sorted(names))}"
        )

    def slots(self) -> dict[str, dict[str, Any]]:
        """Every slot in this workspace, keyed by slot key."""
        return dict(self._document().get("slots") or {})

    def load_state(self) -> dict[str, Any]:
        """This slot's ``{target_column, head, versions}``."""
        doc = self._document()
        return cast("dict[str, Any]", doc["slots"][self._resolve_slot(doc)])

    def _save_state(self, state: dict[str, Any]) -> None:
        doc = self._document()
        doc["slots"][self._resolve_slot(doc)] = state
        self._save_document(doc)

    @property
    def target_column(self) -> str:
        return str(self.load_state()["target_column"])

    # --- lifecycle ---

    @classmethod
    def open(
        cls,
        workspace_id: str,
        *,
        target_column: str,
        seed_train_path: str,
        seed_test_path: str,
        root: str | None = None,
        slot: str | None = None,
    ) -> Workspace:
        """Open (create if absent) a workspace slot seeded from a pre-split upload.

        Idempotent per slot: re-opening keeps every accepted version, so a
        resumed cell continues where it stopped. The v0 seed references the
        upload parquet paths directly — the split is already frozen there.

        Slots are identified by key. The one exception is a format-1 workspace,
        whose single slot recorded no name at all: naming a dataset there adopts
        that slot when the seed matches, which is what keeps an existing
        single-dataset workspace on format 1 instead of growing a redundant
        second slot beside it. Reusing a key for a *different* seed is refused —
        a slot holds one dataset.
        """
        ws = cls(workspace_id, root=root, slot=slot)
        doc: dict[str, Any] = (
            ws._document() if ws.exists() else {"format_version": CURRENT_FORMAT_VERSION, "slots": {}}
        )
        slots: dict[str, Any] = doc["slots"]

        key = slot or LEGACY_SLOT
        if key not in slots and list(slots) == [LEGACY_SLOT] and _seed_train(slots[LEGACY_SLOT]) == seed_train_path:
            ws.requested_slot = LEGACY_SLOT
            return ws
        if key in slots:
            if _seed_train(slots[key]) != seed_train_path:
                raise WorkspaceError(
                    f"slot {key!r} of workspace {workspace_id} is already seeded from a "
                    "different dataset — a slot holds one dataset, so use a distinct slot"
                )
            ws.requested_slot = key
            return ws
        slots[key] = {
            "name": key.split(":", 1)[-1],
            "target_column": target_column,
            "head": SEED_VERSION,
            "versions": [
                {
                    "id": SEED_VERSION,
                    "stage": None,
                    "train": seed_train_path,
                    "test": seed_test_path,
                    "sha256_train": "",
                    "sha256_test": "",
                    "accepted": True,
                }
            ],
        }
        ws.requested_slot = key
        ws._save_document(doc)
        ws.manifests_dir.mkdir(parents=True, exist_ok=True)
        return ws

    # --- version lookup ---

    def _versions(self) -> list[dict[str, Any]]:
        return list(self.load_state().get("versions", []))

    def _find_version(self, version_id: str) -> dict[str, Any] | None:
        return next((v for v in self._versions() if v["id"] == version_id), None)

    def accepted_output(self, stage: str) -> dict[str, Any] | None:
        """The accepted version a given stage produced, if any."""
        want = STAGE_VERSION[stage]
        v = self._find_version(want)
        return v if (v and v.get("accepted")) else None

    def has_accepted(self, stage: str) -> bool:
        """Resume predicate: is this stage's output already accepted on disk?"""
        return self.accepted_output(stage) is not None

    def _input_version_for(self, stage: str) -> dict[str, Any]:
        """The accepted version a stage reads as input (prior stage or v0 seed)."""
        idx = STAGES.index(stage)
        if idx == 0:
            seed = self._find_version(SEED_VERSION)
            if seed is None:
                raise WorkspaceError("v0 seed missing from state")
            return seed
        prev_stage = STAGES[idx - 1]
        prev = self.accepted_output(prev_stage)
        if prev is None:
            raise WorkspaceError(
                f"stage {stage!r} needs the accepted output of {prev_stage!r}, "
                "which is not present — the prior stage did not commit/accept."
            )
        return prev

    # --- reads ---

    def _read_pair(self, version: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
        train = pd.read_parquet(version["train"])
        test = pd.read_parquet(version["test"])
        return train, test

    def _split_target(
        self, df: pd.DataFrame, target_column: str
    ) -> tuple[pd.DataFrame, pd.Series]:
        if target_column not in df.columns:
            raise WorkspaceError(f"target column {target_column!r} missing from version")
        X = df.drop(columns=[target_column]).reset_index(drop=True)
        y = cast("pd.Series", df[target_column].reset_index(drop=True))
        return X, y

    def read_stage_input(
        self, stage: str
    ) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
        """Load the (X_train, y_train, X_test, y_test) a stage should start from."""
        version = self._input_version_for(stage)
        target = self.target_column
        train_df, test_df = self._read_pair(version)
        X_train, y_train = self._split_target(train_df, target)
        X_test, y_test = self._split_target(test_df, target)
        return X_train, y_train, X_test, y_test

    def read_stage_working(
        self, stage: str
    ) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
        """Load the working matrix for a stage — its own committed version if one
        exists (even unaccepted), otherwise the stage input.

        This is what lets a stage's tools chain *within* the stage without an
        in-memory session: DC's ``drop_and_impute`` reads the ``apply_coercions``
        output (the committed-but-unaccepted ``v1_dc``), and FTE's ``fit_preprocessor``
        reads the ``build_features`` output. The first tool of a stage finds no
        committed version yet and falls back to the stage input (prior accepted
        stage, or the ``v0_raw`` seed).
        """
        own = self._find_version(STAGE_VERSION[stage])
        if own is None:
            return self.read_stage_input(stage)
        target = self.target_column
        train_df, test_df = self._read_pair(own)
        X_train, y_train = self._split_target(train_df, target)
        X_test, y_test = self._split_target(test_df, target)
        return X_train, y_train, X_test, y_test

    def read_head(
        self,
    ) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
        """Load (X_train, y_train, X_test, y_test) for the current HEAD version.

        Used by run_model_script — HEAD is the last accepted stage output, i.e.
        the fully engineered/preprocessed/selected model-ready matrix.
        """
        state = self.load_state()
        head = state["head"]
        version = self._find_version(head)
        if version is None:
            raise WorkspaceError(f"HEAD version {head!r} not found in state")
        target = state["target_column"]
        train_df, test_df = self._read_pair(version)
        X_train, y_train = self._split_target(train_df, target)
        X_test, y_test = self._split_target(test_df, target)
        return X_train, y_train, X_test, y_test

    # --- writes ---

    def write_stage(
        self,
        stage: str,
        *,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame,
        y_test: pd.Series,
        learned: dict[str, Any],
        rationale: str = "",
        run_id: str = "",
        accepted: bool = False,
    ) -> dict[str, Any]:
        """Materialize a stage's output version (overwriting any prior attempt).

        Reconstitutes the full frame (features + target) so a version reads back
        exactly like the upload. Fits nothing — the caller has already applied
        the train-fit transform to both splits. Records the learned params +
        rationale in the stage manifest (the provenance that replaces the pickled
        fitted objects) and updates state.json. Does NOT advance HEAD until the
        stage is accepted (see accept_stage), so resume never continues from a
        rejected intermediate.
        """
        if stage not in STAGE_VERSION:
            raise WorkspaceError(f"unknown stage: {stage!r}")
        target = self.target_column
        train_df = X_train.copy()
        train_df[target] = y_train.to_numpy()
        test_df = X_test.copy()
        test_df[target] = y_test.to_numpy()

        version_id = STAGE_VERSION[stage]
        vdir = self.version_dir(version_id)
        vdir.mkdir(parents=True, exist_ok=True)
        train_path = vdir / "train.parquet"
        test_path = vdir / "test.parquet"
        train_df.to_parquet(train_path, index=False)
        test_df.to_parquet(test_path, index=False)
        sha_train = _file_sha256(train_path)
        sha_test = _file_sha256(test_path)

        # Manifest — learned params + rationale (provenance, not the handoff).
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "stage": stage,
            "input_version": self._input_version_for(stage)["id"],
            "output_version": version_id,
            "run_id": run_id,
            "sha256_train": sha_train,
            "sha256_test": sha_test,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
            "n_features": int(X_train.shape[1]),
            "learned": learned,
            "rationale": rationale,
        }
        (self.manifests_dir / f"{stage}.json").write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False, default=str) + "\n"
        )

        # State — upsert this stage's version (do NOT advance HEAD yet).
        state = self.load_state()
        entry = {
            "id": version_id,
            "stage": stage,
            "train": str(train_path),
            "test": str(test_path),
            "sha256_train": sha_train,
            "sha256_test": sha_test,
            "accepted": accepted,
            "run_id": run_id,
        }
        versions = [v for v in state["versions"] if v["id"] != version_id]
        versions.append(entry)
        state["versions"] = versions
        if accepted:
            state["head"] = version_id
        self._save_state(state)
        return manifest

    def accept_stage(self, stage: str) -> None:
        """Mark a stage's committed output accepted and advance HEAD to it.

        Called after the critic approves (or on the final attempt). HEAD only
        moves here, so a rejected/uncommitted stage never becomes a resume point
        or a scoring input.
        """
        version_id = STAGE_VERSION[stage]
        state = self.load_state()
        found = False
        for v in state["versions"]:
            if v["id"] == version_id:
                v["accepted"] = True
                found = True
        if not found:
            raise WorkspaceError(
                f"cannot accept stage {stage!r}: no committed {version_id} version"
            )
        state["head"] = version_id
        self._save_state(state)

    def discard_stage(self, stage: str) -> bool:
        """Drop a stage's committed-but-unaccepted version so its working matrix
        reverts to the stage input. Returns True if a version was removed.

        Used to restart a stage cleanly on a critic revision: the rejected
        attempt committed ``v{n}`` (unaccepted), and because the stage's tools read
        the *working* matrix (:meth:`read_stage_working`), a naive re-run would
        transform on top of that rejected output. Discarding it — the state entry,
        the parquet pair, and the stage manifest (whose ``learned`` block
        :func:`commit_stage` otherwise merges into) — makes the re-run's first tool
        fall back to the prior accepted stage's output (or the ``v0_raw`` seed).

        Refuses to discard an ACCEPTED version: that is HEAD/handoff, not a
        rejected attempt, and must never be silently dropped. HEAD is untouched
        either way — it never points at an unaccepted version.
        """
        if stage not in STAGE_VERSION:
            raise WorkspaceError(f"unknown stage: {stage!r}")
        version_id = STAGE_VERSION[stage]
        state = self.load_state()
        existing = next((v for v in state["versions"] if v["id"] == version_id), None)
        if existing is None:
            return False
        if existing.get("accepted"):
            raise WorkspaceError(
                f"refusing to discard accepted stage {stage!r} ({version_id}) — "
                "it is HEAD/handoff, not a rejected attempt"
            )
        state["versions"] = [v for v in state["versions"] if v["id"] != version_id]
        self._save_state(state)
        # Best-effort cleanup; state.json (updated above) is the source of truth.
        vdir = self.version_dir(version_id)
        for name in ("train.parquet", "test.parquet"):
            f = vdir / name
            try:
                if f.is_file():
                    f.unlink()
            except OSError:
                pass
        manifest_path = self.manifests_dir / f"{stage}.json"
        try:
            if manifest_path.is_file():
                manifest_path.unlink()
        except OSError:
            pass
        return True
