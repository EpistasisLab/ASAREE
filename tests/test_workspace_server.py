"""Dataset-scope boundaries on the ASAREE workspace MCP server."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import pytest
from asaree_workspace_core import Workspace

from asaree.mcp_servers import workspace_server as ws


class _FakeCtx:
    def __init__(self, extra: dict[str, Any]) -> None:
        self.request_context = type("_R", (), {"meta": type("_M", (), {"model_extra": extra})()})()


@pytest.fixture
def other_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr("asaree_workspace_core.workspace.WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    path = tmp_path / "source.parquet"
    pd.DataFrame({"x": [1, 2], "y": [0, 1]}).to_parquet(path)
    victim = Workspace.open("experiment/B", target_column="y", seed_train_path=str(path), seed_test_path=str(path))
    ws._seed_scratch(victim, "dc", "y")
    marker = ws._scratch_dir(victim, "dc") / "learned.json"
    marker.write_text('{"private": "attempt B"}')
    return victim, marker


@pytest.mark.parametrize("operation", [
    "workspace_status", "reset_stage", "accept_stage", "check_stage_gate",
    "read_stage_manifest", "read_scratch_learned",
])
@pytest.mark.parametrize("pin_marker", ["dataset_inputs", "dataset_input_scope"])
def test_pinned_workspace_operations_cannot_access_other_attempt(other_attempt, operation, pin_marker):
    victim, marker = other_attempt
    before = victim.state_path.read_bytes()
    ctx = _FakeCtx({"motoro.workspace_id": "experiment/A", f"motoro.ambient.{pin_marker}": []})
    kwargs = {} if operation == "workspace_status" else {"stage": "dc"}
    response = json.loads(getattr(ws, operation)(workspace_id="experiment/B", ctx=ctx, **kwargs))
    assert "attempt workspace" in json.dumps(response)
    assert marker.read_text() == '{"private": "attempt B"}'
    assert victim.state_path.read_bytes() == before


def test_unscoped_notebook_can_select_workspace_explicitly(other_attempt):
    victim, marker = other_attempt
    status = json.loads(ws.workspace_status(workspace_id="experiment/B"))
    assert status["exists"]
    response = json.loads(ws.reset_stage("dc", workspace_id="experiment/B"))
    assert response["discarded"]
    assert not marker.exists()
    assert victim.load_state()["head"] == "v0_raw"


@pytest.mark.parametrize("explicit", [False, True])
def test_pinned_workspace_allows_own_attempt(other_attempt, explicit):
    _victim, marker = other_attempt
    ctx = _FakeCtx({"motoro.workspace_id": "experiment/B", "motoro.ambient.dataset_inputs": []})
    workspace_id = "experiment/B" if explicit else ""
    assert json.loads(ws.workspace_status(workspace_id=workspace_id, ctx=ctx))["exists"]
    assert json.loads(ws.reset_stage("dc", workspace_id=workspace_id, ctx=ctx))["discarded"]
    assert not marker.exists()


async def test_open_workspace_rejects_an_explicit_dataset_not_wired_to_the_run() -> None:
    ctx = _FakeCtx(
        {
            "motoro.workspace_id": "experiment/cell",
            "motoro.ambient.dataset_names": ["attached"],
        }
    )

    result = json.loads(await ws.open_workspace(name="other-registered-dataset", ctx=ctx))

    assert result == {
        "error": "Dataset 'other-registered-dataset' is not wired into this run.",
        "wired_datasets": ["attached"],
    }


async def test_open_workspace_still_allows_the_wired_name_and_outside_run_calls() -> None:
    wired_ctx = _FakeCtx(
        {
            "motoro.workspace_id": "experiment/cell",
            "motoro.ambient.dataset_names": ["attached"],
        }
    )

    wired = json.loads(await ws.open_workspace(name="attached", ctx=wired_ctx))
    outside = json.loads(
        await ws.open_workspace(
            experiment_id="experiment",
            cell_label="cell",
            name="other-registered-dataset",
        )
    )

    # Both calls pass dataset-scope validation and stop later only because this
    # unit-test context deliberately has no authenticated owner id.
    assert "owner resolution" in wired["error"]
    assert "owner resolution" in outside["error"]


async def test_open_workspace_rejects_explicit_data_when_the_run_wires_none() -> None:
    ctx = _FakeCtx({"motoro.workspace_id": "experiment/cell"})

    result = json.loads(await ws.open_workspace(name="registered-but-unwired", ctx=ctx))

    assert result == {
        "error": "Dataset 'registered-but-unwired' is not wired into this run.",
        "wired_datasets": [],
    }
