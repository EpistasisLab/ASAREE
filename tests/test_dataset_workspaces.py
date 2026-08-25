"""Reading a seeded cell workspace back out -- see services/dataset_workspaces.py.

Only the on-disk half is covered here: seeding itself needs a registration in
Postgres and is exercised through ``test_protocol_execution``'s pre-seed tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asaree_workspace_core import workspace as ws_module

from asaree.services.dataset_workspaces import head_data_locator


def _write_state(root: Path, workspace_id: str, state: dict) -> None:
    directory = root / workspace_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "state.json").write_text(json.dumps(state))


def test_head_data_locator_names_the_head_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # HEAD, not the v0_raw seed: a Score step after DC/FTE/FS must fit on the
    # engineered matrix, the same version the workspace-reading tools use.
    monkeypatch.setattr(ws_module, "WORKSPACE_ROOT", str(tmp_path))
    _write_state(
        tmp_path,
        "exp1/cellA",
        {
            "target_column": "outcome",
            "head": "v1_dc",
            "versions": [
                {"id": "v0_raw", "train": "/uploads/train.parquet", "test": "/uploads/test.parquet"},
                {"id": "v1_dc", "train": "/ws/v1_dc/train.parquet", "test": "/ws/v1_dc/test.parquet"},
            ],
        },
    )
    # The TRAIN side only: the tools this is for make their own held-out split
    # from the file they're handed, so naming the frozen test parquet would
    # invite fitting on it.
    assert head_data_locator("exp1/cellA") == ("/ws/v1_dc/train.parquet", "outcome")


def test_head_data_locator_is_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Every failure returns ("", "") rather than raising: the tool then asks
    # for an explicit data_path, which is the pre-existing behaviour.
    monkeypatch.setattr(ws_module, "WORKSPACE_ROOT", str(tmp_path))
    assert head_data_locator("exp1/never-seeded") == ("", "")  # no state.json
    assert head_data_locator("not-a-workspace-id") == ("", "")  # rejected by the workspace layer

    _write_state(tmp_path, "exp1/cellB", {"target_column": "outcome", "head": "v9", "versions": []})
    assert head_data_locator("exp1/cellB") == ("", "")  # HEAD missing from state
