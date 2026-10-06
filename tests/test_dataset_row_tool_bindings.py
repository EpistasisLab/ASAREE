from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.server import Settings as FastMCPSettings

# FastMCP otherwise reads the application .env while this isolated test module
# imports the server. These tests use only synthetic ambient context.
FastMCPSettings.model_config["env_file"] = None

from asaree.mcp_servers import workspace_server  # noqa: E402
from asaree.services.protocol_execution import (  # noqa: E402
    _ambient_meta_for,
    _node_run_context,
    _resolve_dataset_tool_config,
)


def _ctx(meta: dict):
    return SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(model_extra=meta)))


def _graph(*, driver: bool = True):
    agent = {"id": "agent", "type": "agent", "data": {"config": {}}}
    model = {"id": "model", "type": "llm", "data": {"config": {}}}
    nodes = [agent, model]
    edges = [{"source": "model", "target": "agent", "targetHandle": "model"}]
    if driver:
        nodes.append({"id": "dataset", "type": "dataset", "data": {"config": {
            "dataset_id": "11111111-1111-4111-8111-111111111111", "dataset_name": "driver",
            "target_column": "secret", "description": "FULL DICTIONARY", "dictionary_available": True,
        }}})
        edges.append({"source": "dataset", "target": "agent", "targetHandle": "dataset",
                      "data": {"dataset_input": {"mode": "per_row", "columns": ["question"]}}})
    return {"nodes": nodes, "edges": edges}


@pytest.mark.asyncio
async def test_row_node_context_is_private_and_never_inherits_cell_head(monkeypatch, tmp_path):
    async def forbid_registration(*_args, **_kwargs):
        pytest.fail("a supplied row view must never look up the full driver")

    monkeypatch.setattr("asaree.services.protocol_execution.fetch_owned_registration", forbid_registration)
    monkeypatch.setattr(
        "asaree.services.protocol_execution.head_data_locator",
        lambda _wid: ("/shared/train.csv", "secret"),
    )
    inputs = [{"name": "driver", "dataset_id": "11111111-1111-4111-8111-111111111111",
               "raw_sha256": "a" * 64, "row_index": 0, "columns": ["question"],
               "values": {"question": "only row"}, "path": str(tmp_path / "row.csv"),
               "mode": "per_row", "target_column": ""}]
    meta, dataset = await _node_run_context(
        _graph(), "agent", "shared-cell", uuid.uuid4(), protocol_run_id=uuid.uuid4(),
        row_input_context={"dataset_id": inputs[0]["dataset_id"], "raw_sha256": "a" * 64,
                           "row_index": 0, "columns": ["question"], "values": {"question": "only row"}},
        row_inputs=inputs,
    )
    assert dataset.seeded == ()
    assert meta["dataset_mode"] == "per_row"
    assert meta["row_inputs"] == inputs
    assert meta["data_path"] == inputs[0]["path"]
    assert "/shared/" not in json.dumps(meta)
    assert _resolve_dataset_tool_config(_graph(), "agent", row_mode=True)["tool_names"] == [
        "asaree-workspace.open_workspace", "asaree-workspace.workspace_status", "scikit-learn-mcp.describe_dataset"
    ]


def test_row_without_driver_has_authoritative_empty_inputs_and_no_locator(monkeypatch):
    monkeypatch.setattr("asaree.services.protocol_execution.head_data_locator", lambda _wid: ("/shared/train.csv", "y"))
    meta = _ambient_meta_for(_graph(driver=False), "agent", "shared", row_mode=True, row_inputs=[])
    assert meta["dataset_mode"] == "per_row"
    assert meta["row_inputs"] == []
    assert "data_path" not in meta and "data_slots" not in meta


@pytest.mark.asyncio
async def test_row_open_and_status_are_bound_and_reject_overrides(tmp_path):
    row_file = tmp_path / "view.csv"
    row_file.write_text("question\nhello\n", encoding="utf-8")
    dataset_id = "11111111-1111-4111-8111-111111111111"
    row = {"dataset_id": dataset_id, "raw_sha256": "b" * 64, "row_index": 2}
    entry = {"name": "driver", "dataset_id": dataset_id, "mode": "per_row", "path": str(row_file),
             "columns": ["question"], "target_column": ""}
    ctx = _ctx({"motoro.workspace_id": "_protocol_runs/run-a", "motoro.ambient.dataset_row": row,
                "motoro.ambient.row_inputs": [entry]})
    result = json.loads(await workspace_server.open_workspace(ctx=ctx))
    assert result == {"workspace_id": "_protocol_runs/run-a", "dataset_mode": "per_row",
                     "dataset_id": dataset_id, "raw_sha256": "b" * 64, "row_index": 2,
                     "columns": ["question"], "data_path": str(row_file), "target_column": ""}
    assert json.loads(await workspace_server.open_workspace(name="other", ctx=ctx)) == {
        "error": "row workspace is fixed to the authorized Agent view"}
    assert json.loads(workspace_server.workspace_status("_protocol_runs/run-b", ctx)) == {
        "error": "workspace override is not authorized for this Agent"}
    status = json.loads(workspace_server.workspace_status(ctx=ctx))
    assert status["inputs"][0]["path"] == str(row_file)
    assert "values" not in json.dumps(status)


def test_row_tool_config_and_ambient_do_not_leak_target_or_dictionary():
    config = _resolve_dataset_tool_config(_graph(), "agent", row_mode=True)
    assert all("dictionary" not in tool and "split" not in tool for tool in config["tool_names"])
    assert "asaree-workspace.train_test_split" not in config["tool_names"]
    assert "asaree-eda.get_data_dictionary" not in config["tool_names"]
    assert "FULL DICTIONARY" not in json.dumps(_ambient_meta_for(
        _graph(), "agent", None, row_mode=True, row_inputs=[]
    ))
