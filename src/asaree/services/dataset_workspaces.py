"""Seeding a cell workspace from a registered dataset.

Extracted from ``mcp_servers/workspace_server.py``'s ``open_workspace`` so
that ASAREE itself can seed a workspace *before* an agent's first turn --
see ``protocol_execution._preseed_dataset_workspace``. Wiring a Dataset node
onto the canvas is meant to be the whole user-facing gesture; a researcher
should not also have to know that a workspace is a thing an agent opens.

The MCP tool is still the entry point for everything this module doesn't
cover (calling from outside a run, overriding the target column, resuming
from the notebook), and it now delegates the seeding itself here so there is
one implementation of "turn a registration into ``v0_raw`` on disk", not two
that can drift.

Lives under ``services/`` rather than in the MCP server module because the
run worker imports it: importing the server module would construct its
``FastMCP`` instance as a side effect of a plain seeding call.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from asaree_workspace_core import SEED_VERSION, Workspace, WorkspaceError

from asaree.models.database import get_session
from asaree.services.datasets import get_dataset_by_name

logger = logging.getLogger(__name__)

DATA_DICTIONARY_FILENAME = "data_dictionary.json"


class WorkspaceSeedError(Exception):
    """A workspace could not be seeded, with a message fit to show a caller.

    One exception type for every failure mode (unknown/unowned dataset, no
    target column, a cell already holding a different dataset, unreadable
    parquet) because every one of them lands in the same place: an ``error``
    string in the tool response, or a log line and a prompt fallback on the
    pre-seed path. Distinguishing them in code would give neither caller
    anything to branch on.
    """


@dataclass(frozen=True)
class SeededWorkspace:
    """A workspace open at ``v0_raw`` (or later, if the cell is resuming)."""

    workspace: Workspace
    dataset_name: str
    target_column: str
    data_dictionary_available: bool


async def fetch_owned_registration(name: str, owner_id: uuid.UUID) -> dict[str, Any] | None:
    """Look up a registered dataset by name, scoped to one owner.

    In-process DB call, not an HTTP round-trip -- every caller is ASAREE's own
    code. A dataset that exists but belongs to someone else is reported the
    same as "not found", matching the HTTP API's own 404-not-403 convention
    (``asaree/api/datasets.py``).
    """
    async with get_session() as db:
        dataset = await get_dataset_by_name(db, name)
        if dataset is None or dataset.owner_id != owner_id:
            return None
        return {
            "target_column": dataset.target_column,
            "train_path": dataset.train_path,
            "test_path": dataset.test_path,
            "dictionary_json": dataset.dictionary_json,
        }


def publish_data_dictionary(ws: Workspace, dictionary_json: str | None) -> bool:
    """Copy the registration's data dictionary into the cell's workspace.

    The train/test data reaches the domain MCP servers over the shared
    filesystem, but ``dictionary_json`` lives only in Postgres -- and those
    servers deliberately hold no DB credentials, so the only channel left to
    them was an authenticated HTTP call back into the API with a token
    (``ASAREE_INTERNAL_MCP_API_KEY``) that a fresh deployment doesn't have.
    Writing it here, next to the ``state.json`` they already read, puts the
    dictionary on the same route as everything else they consume: no token, no
    network, no per-deployment setup step.

    Ownership is already enforced upstream (see ``fetch_owned_registration``),
    and the file lands inside a workspace named for its own experiment/cell,
    so it's scoped exactly like the parquet beside it.

    Returns whether a dictionary is available for this cell. A write failure is
    swallowed on purpose: a dictionary is an aid to an agent, never a
    precondition for the run, and the reader falls back to the API anyway.
    """
    if not dictionary_json:
        return False
    try:
        ws.dir.mkdir(parents=True, exist_ok=True)
        path = ws.dir / DATA_DICTIONARY_FILENAME
        # Atomic, and rewritten on every open so an edited registration can't
        # leave a resumed cell reading a stale copy.
        tmp = ws.dir / f"{DATA_DICTIONARY_FILENAME}.tmp"
        tmp.write_text(dictionary_json)
        tmp.replace(path)
    except OSError as e:
        logger.warning("workspace_data_dictionary_write_failed", extra={"workspace": ws.workspace_id, "error": str(e)})
    return True


async def seed_cell_workspace(
    *, workspace_id: str, dataset_name: str, owner_id: uuid.UUID, target_column: str = ""
) -> SeededWorkspace:
    """Open (creating if absent) *workspace_id*, seeded from a registration.

    Seeds ``v0_raw`` from the dataset's pre-split train/test parquet -- the
    split is frozen at upload and never re-split -- and publishes the data
    dictionary alongside it. Idempotent: a cell that already has accepted
    stages keeps them, which is what makes this safe to call unconditionally
    at the start of every agent turn as well as from the MCP tool.

    Raises :class:`WorkspaceSeedError` on anything that leaves the cell
    without usable data.
    """
    reg = await fetch_owned_registration(dataset_name, owner_id)
    if reg is None:
        raise WorkspaceSeedError(f"Dataset '{dataset_name}' not found in registry.")

    resolved_target = target_column or reg.get("target_column") or ""
    if not resolved_target:
        raise WorkspaceSeedError("target_column not provided and not set in registry.")

    try:
        ws = Workspace.open(
            workspace_id,
            target_column=resolved_target,
            seed_train_path=reg["train_path"],
            seed_test_path=reg["test_path"],
        )
    except (WorkspaceError, FileNotFoundError, OSError) as e:
        raise WorkspaceSeedError(f"workspace: {e}") from e

    # Workspace.open is idempotent by design (a resumed cell must keep its
    # accepted versions), which means a SECOND dataset opened into the same
    # cell silently gets the first one's data back. A cell workspace is keyed
    # by experiment_id/cell_label only -- the dataset name is not part of the
    # id -- so two datasets cannot both live here, and the ambient
    # workspace_id every downstream stage tool resolves is a single value
    # anyway. Report the collision instead of handing back a workspace holding
    # data the caller didn't ask for.
    seeded = next((v for v in ws.load_state().get("versions", []) if v.get("id") == SEED_VERSION), None)
    if seeded is not None and seeded.get("train") not in (None, reg["train_path"]):
        raise WorkspaceSeedError(
            f"workspace {workspace_id!r} is already seeded from a different dataset. "
            f"A cell workspace holds one dataset; {dataset_name!r} needs its own cell."
        )

    return SeededWorkspace(
        workspace=ws,
        dataset_name=dataset_name,
        target_column=resolved_target,
        data_dictionary_available=publish_data_dictionary(ws, reg.get("dictionary_json")),
    )


def head_data_locator(workspace_id: str) -> tuple[str, str]:
    """``(train parquet path, target column)`` for *workspace_id*'s HEAD, or
    ``("", "")`` when there's nothing to point at.

    For the tools that take a dataset as a plain path instead of reading the
    workspace layout themselves -- ``scikit-learn-mcp``, the one sklearn server
    the canvas offers, which imports nothing from this repo by design. Without
    a path bound for it, the only dataset identity such a tool's caller could
    see was the workspace id ``workspace_status`` reports, and agents passed
    THAT as ``data_path``: "no such file: '<experiment_id>/<cell_label>'".

    HEAD, not the ``v0_raw`` seed, so a Score step after DC/FTE/FS fits on the
    engineered matrix -- the same version ``asaree-sklearn-model``'s own
    workspace-reading tools use. Read at turn start, so it names the HEAD the
    agent's turn begins from; a stage this same turn accepts moves HEAD on and
    the bound path goes stale, which is why it's a fallback for a tool that
    can't read the workspace rather than the route for one that can.

    The TRAIN side only: those tools make their own held-out split from the
    file they're given, so handing over the frozen test parquet as well would
    invite fitting on it. Scoring on a split of train is a weaker claim than
    the workspace's frozen split, never a leaking one.

    Total by design -- an unseeded or unreadable workspace returns ``("", "")``
    and the tool asks for an explicit ``data_path`` as before. "Not seeded
    yet" is the expected answer, not a fault (every caller asks before knowing
    whether this cell has a workspace at all), so it returns quietly; only a
    workspace that exists and still can't be read is worth a log line.
    """
    try:
        ws = Workspace(workspace_id)
        if not ws.exists():
            return "", ""
        state = ws.load_state()
        head = next((v for v in state.get("versions", []) if v.get("id") == state.get("head")), None)
        train = str((head or {}).get("train") or "")
        target = str(state.get("target_column") or "")
    except WorkspaceError:
        return "", ""  # a malformed workspace_id -- nothing existed to read
    except (OSError, ValueError) as e:
        logger.warning("workspace_head_locator_failed", extra={"workspace_id": workspace_id, "error": str(e)})
        return "", ""
    return (train, target) if train else ("", "")
