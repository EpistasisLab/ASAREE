"""Script subprocesses receive only the invoking Agent's authorized inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from asaree.mcp_servers import script_server as ss


class _FakeCtx:
    def __init__(self, extra: dict[str, Any]) -> None:
        self.request_context = type("_R", (), {"meta": type("_M", (), {"model_extra": extra})()})()


def _run(ctx: _FakeCtx) -> dict[str, Any]:
    return json.loads(ss.run_wired_script(ctx=ctx))


def _row_context(tmp_path: Path, path: Path, *, workspace_id: str, row_inputs: list[dict[str, Any]]) -> _FakeCtx:
    script = tmp_path / "wired.py"
    script.write_text(
        "import csv, json\n"
        "from asaree.script_context import training_inputs\n"
        "inputs = training_inputs()\n"
        "rows = list(csv.DictReader(inputs[0].path.open())) if inputs else []\n"
        "item = inputs[0] if inputs else None\n"
        "print(json.dumps({'inputs': [{'name': x.name, 'path': str(x.path), 'mode': x.mode, "
        "'target': x.target_column, 'dataset_id': x.dataset_id, 'raw_sha256': x.raw_sha256, "
        "'row_index': x.row_index, 'columns': x.columns} for x in inputs], 'rows': rows}))\n"
    )
    return _FakeCtx(
        {
            "motoro.ambient.script_path": str(script),
            "motoro.workspace_id": workspace_id,
            "motoro.ambient.dataset_mode": "per_row",
            "motoro.ambient.row_inputs": row_inputs,
            "motoro.ambient.data_path": "/stale/full.csv",
            "motoro.ambient.dataset_names": ["stale"],
        }
    )


def test_prediction_script_reads_only_authorized_question_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(tmp_path / "workspaces"))
    row_dir = tmp_path / "workspaces" / "_protocol_runs" / "attempt-1" / "agents" / "agent-a"
    row_dir.mkdir(parents=True)
    row_csv = row_dir / "authorized.csv"
    row_csv.write_text("question\nWhat is 2+2?\n")
    monkeypatch.setattr(ss, "raw_training_data_locators", lambda _workspace_id: pytest.fail("stale lookup"))
    row = {
        "name": "prompt",
        "path": str(row_csv),
        "target_column": "",
        "mode": "per_row",
        "dataset_id": "dataset-1",
        "raw_sha256": "a" * 64,
        "row_index": 3,
        "columns": ["question"],
    }

    out = _run(_row_context(tmp_path, row_csv, workspace_id="_protocol_runs/attempt-1", row_inputs=[row]))

    payload = json.loads(out["stdout"])
    assert payload["rows"] == [{"question": "What is 2+2?"}]
    assert len(payload["inputs"]) == 1
    assert payload["inputs"][0] == {
        "name": "prompt",
        "path": str(row_csv),
        "mode": "per_row",
        "target": "",
        "dataset_id": "dataset-1",
        "raw_sha256": "a" * 64,
        "row_index": 3,
        "columns": ["question"],
    }


def test_grading_script_receives_same_row_reference_and_legacy_state_is_narrow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(tmp_path / "workspaces"))
    row_dir = tmp_path / "workspaces" / "_protocol_runs" / "attempt-grade" / "agents" / "agent-b"
    row_dir.mkdir(parents=True)
    row_csv = row_dir / "grade.csv"
    row_csv.write_text("question,reference\nQ,A\n")
    row = {
        "name": "grading",
        "path": str(row_csv),
        "target_column": "reference",
        "mode": "per_row",
        "dataset_id": "dataset-1",
        "raw_sha256": "b" * 64,
        "row_index": 3,
        "columns": ["question", "reference"],
    }
    ctx = _row_context(tmp_path, row_csv, workspace_id="_protocol_runs/attempt-grade", row_inputs=[row])
    script = tmp_path / "grading.py"
    script.write_text(
        "import json\nfrom pathlib import Path\n"
        "state = json.loads(Path('state.json').read_text())\n"
        "from asaree.script_context import training_input\n"
        "item = training_input()\n"
        "print(json.dumps({'path': str(item.path), 'state': state}))\n"
    )
    ctx.request_context.meta.model_extra["motoro.ambient.script_path"] = str(script)

    payload = json.loads(_run(ctx)["stdout"])

    state = payload["state"]
    assert payload["path"] == str(row_csv)
    assert state == {
        "target_column": "reference",
        "head": "v0_raw",
        "versions": [{"id": "v0_raw", "train": str(row_csv)}],
    }
    assert set(state["versions"][0]) == {"id", "train"}


def test_empty_authorized_row_inputs_do_not_inherit_stale_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(tmp_path / "workspaces"))
    workspace = tmp_path / "workspaces" / "_protocol_runs" / "attempt-empty"
    workspace.mkdir(parents=True)
    (workspace / "state.json").write_text('{"head":"stale"}')
    monkeypatch.setattr(ss, "raw_training_data_locators", lambda _workspace_id: pytest.fail("stale lookup"))
    out = _run(
        _row_context(
            tmp_path,
            tmp_path / "unused.csv",
            workspace_id="_protocol_runs/attempt-empty",
            row_inputs=[],
        )
    )
    assert out["exit_code"] == 0
    assert json.loads(out["stdout"]) == {"inputs": [], "rows": []}


def test_retry_uses_a_different_private_attempt_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(root))
    for attempt in ("attempt-one", "attempt-two"):
        (root / "_protocol_runs" / attempt).mkdir(parents=True)

    paths = []
    for attempt in ("attempt-one", "attempt-two"):
        ctx = _row_context(tmp_path, tmp_path / "unused.csv", workspace_id=f"_protocol_runs/{attempt}", row_inputs=[])
        script = tmp_path / f"{attempt}.py"
        script.write_text("from pathlib import Path\nprint(Path.cwd())")
        ctx.request_context.meta.model_extra["motoro.ambient.script_path"] = str(script)
        paths.append(Path(_run(ctx)["stdout"].strip()))

    assert paths == [root / "_protocol_runs" / "attempt-one", root / "_protocol_runs" / "attempt-two"]
    assert paths[0] != paths[1]


def test_whole_context_inputs_keep_existing_manifest_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = tmp_path / "whole.csv"
    raw.write_text("question\nhello\n")
    monkeypatch.setattr(ss, "raw_training_data_locators", lambda _workspace_id: {})
    manifest = ss._runtime_manifest(
        _FakeCtx(
            {
                "motoro.ambient.dataset_mode": "raw_unsplit",
                "motoro.ambient.dataset_names": ["whole"],
                "motoro.ambient.data_path": str(raw),
                "motoro.ambient.target_column": "",
            }
        ),
        "",
    )
    assert manifest == {
        "schema_version": 1,
        "training_inputs": [
            {"name": "whole", "path": str(raw), "target_column": "", "mode": "raw_unsplit", "slot": None,
             "workspace_version": None}
        ],
    }


def test_whole_mode_subprocess_and_result_envelope_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "whole.csv"
    raw.write_text("question\nhello\n")
    script = tmp_path / "whole.py"
    script.write_text(
        "from asaree.script_context import training_input\n"
        "item = training_input()\n"
        "print(item.mode, item.path)\n"
    )
    monkeypatch.setattr(ss, "raw_training_data_locators", lambda _workspace_id: {})
    ctx = _FakeCtx(
        {
            "motoro.ambient.script_path": str(script),
            "motoro.ambient.dataset_mode": "raw_unsplit",
            "motoro.ambient.dataset_names": ["whole"],
            "motoro.ambient.data_path": str(raw),
            "motoro.ambient.target_column": "",
        }
    )
    out = _run(ctx)
    assert out["exit_code"] == 0
    assert out["stdout"] == f"raw_unsplit {raw}\n"
    assert len(out["code_sha256"]) == 64
    assert "stderr" in out

    failed_script = tmp_path / "failed.py"
    failed_script.write_text("raise ValueError('expected failure')")
    ctx.request_context.meta.model_extra["motoro.ambient.script_path"] = str(failed_script)
    failed = _run(ctx)
    assert failed["exit_code"] == 1
    assert "expected failure" in failed["stderr"]
    assert "error" in failed

    failed_script.write_text("print('x' * 20000)")
    truncated = _run(ctx)
    assert "truncated" in truncated["stdout"]
