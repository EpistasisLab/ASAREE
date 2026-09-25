"""Dataset-scope boundaries on the ASAREE workspace MCP server."""

from __future__ import annotations

import json
from typing import Any

from asaree.mcp_servers import workspace_server as ws


class _FakeCtx:
    def __init__(self, extra: dict[str, Any]) -> None:
        self.request_context = type("_R", (), {"meta": type("_M", (), {"model_extra": extra})()})()


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
