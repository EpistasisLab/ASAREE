"""Unit tests for the asaree-script MCP server -- the executor for a wired
Script node. Real subprocesses against real files in tmp_path; no MCP client,
the tool functions are called directly like the other mcp-servers' suites do."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from asaree.mcp_servers import script_server as ss


class _FakeCtx:
    """Stands in for FastMCP's Context -- only the ambient _meta is read."""

    def __init__(self, extra: dict[str, str]) -> None:
        self.request_context = type("_R", (), {"meta": type("_M", (), {"model_extra": extra})()})()


def _wire(tmp_path: Path, code: str) -> _FakeCtx:
    script = tmp_path / "wired.py"
    script.write_text(code)
    return _FakeCtx({"motoro.ambient.script_path": str(script)})


def _run(**kwargs: Any) -> dict[str, Any]:
    return json.loads(ss.run_wired_script(**kwargs))


def test_runs_the_wired_script_with_no_arguments(tmp_path: Path) -> None:
    # The whole point: no `code` argument, so nothing passes through the model.
    out = _run(ctx=_wire(tmp_path, "print('hello world')"))
    assert out["exit_code"] == 0
    assert out["stdout"] == "hello world\n"
    assert len(out["code_sha256"]) == 64


def test_plain_python_needs_no_dataset_or_entry_point(tmp_path: Path) -> None:
    # What the sklearn script tools reject and this exists for: no
    # predict_proba, no data_path, no target column.
    out = _run(ctx=_wire(tmp_path, "total = sum(range(5))\nprint(f'total={total}')"))
    assert out["stdout"].strip() == "total=10"
    assert "error" not in out


def test_a_raising_script_reports_the_traceback(tmp_path: Path) -> None:
    out = _run(ctx=_wire(tmp_path, "raise ValueError('boom')"))
    assert out["exit_code"] == 1
    # Named as an error too: a model skimming the payload shouldn't have to
    # know that 0 is the good exit code.
    assert "exited with code 1" in out["error"]
    assert "boom" in out["stderr"]


def test_timeout_keeps_what_the_script_already_printed(tmp_path: Path) -> None:
    ctx = _wire(tmp_path, "import time\nprint('before')\ntime.sleep(30)")
    out = _run(timeout_seconds=1, ctx=ctx)
    assert out["timed_out"] is True
    # PYTHONUNBUFFERED plus decoding TimeoutExpired's (bytes!) buffers is what
    # makes a partial result survive the kill -- see _as_text.
    assert out["stdout"] == "before\n"


def test_no_script_wired_is_reported_not_guessed(tmp_path: Path) -> None:
    assert "no script to run" in _run(ctx=_FakeCtx({}))["error"]
    assert "no script to run" in _run()["error"]
    gone = _FakeCtx({"motoro.ambient.script_path": str(tmp_path / "gone.py")})
    assert "could not read" in _run(ctx=gone)["error"]
    assert "empty" in _run(ctx=_wire(tmp_path, "   \n"))["error"]


def test_explicit_code_wins_over_the_wired_script(tmp_path: Path) -> None:
    # A genuine one-off is not overridden -- and it still runs from a file, so
    # its traceback keeps real line numbers.
    out = _run(code="print('one-off')", ctx=_wire(tmp_path, "print('wired')"))
    assert out["stdout"] == "one-off\n"
    assert out["script"].startswith("inline-")


def test_credentials_do_not_reach_the_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # This server is spawned with ASAREE_MCP_ALLOWED_ENV_VARS in its own
    # environment -- the product database URL and the internal API key among
    # them -- and user-authored code has no business reading either.
    monkeypatch.setenv("ASAREE_INTERNAL_MCP_API_KEY", "super-secret")
    monkeypatch.setenv("ASAREE_PRODUCT_DATABASE_URL", "postgresql://user:pw@host/db")
    out = _run(ctx=_wire(tmp_path, "import os\nprint(sorted(k for k in os.environ if 'ASAREE' in k))"))
    assert out["stdout"].strip() == "[]"


def test_runs_in_the_cells_workspace_when_there_is_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Relative paths in a wired script should reach this cell's own data
    # without the script being told where it is.
    root = tmp_path / "workspaces"
    workspace = root / "exp1" / "cellA"
    workspace.mkdir(parents=True)
    (workspace / "state.json").write_text('{"head": "v1"}')
    monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(root))

    script = tmp_path / "wired.py"
    script.write_text("print(open('state.json').read())")
    ctx = _FakeCtx({"motoro.ambient.script_path": str(script), "motoro.workspace_id": "exp1/cellA"})
    assert '"head": "v1"' in _run(ctx=ctx)["stdout"]

    # No workspace id (an unlinked protocol run): fall back to the script's own
    # directory rather than wherever this server happens to have been started.
    assert ss._working_dir("", script) == script.parent


def test_a_workspace_id_cannot_escape_the_workspace_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(root))
    script = tmp_path / "wired.py"
    script.write_text("print(1)")
    assert ss._working_dir("../../etc", script) == script.parent


def test_output_is_truncated(tmp_path: Path) -> None:
    # A tool result is read by a model, so a script that prints in a loop must
    # not cost more context than the answer it was called for.
    out = _run(ctx=_wire(tmp_path, "print('x' * 20000)"))
    assert "truncated" in out["stdout"]
    assert len(out["stdout"]) < 20000


def test_timeout_is_capped(tmp_path: Path) -> None:
    assert ss._MAX_TIMEOUT >= ss._DEFAULT_TIMEOUT
    ctx = _wire(tmp_path, "print('ok')")
    # An absurd request is clamped, not honoured, and a zero/negative one still
    # gets a chance to run.
    assert _run(timeout_seconds=10**9, ctx=ctx)["exit_code"] == 0
    assert _run(timeout_seconds=0, ctx=ctx)["exit_code"] == 0


def test_tools_registered() -> None:
    tools = {t.name: t for t in asyncio.run(ss.mcp.list_tools())}
    assert set(tools) == {"run_wired_script", "ping"}
    # ctx is FastMCP's own injection, never a model-visible argument.
    assert set(tools["run_wired_script"].inputSchema["properties"]) == {"code", "timeout_seconds"}
    assert not tools["run_wired_script"].inputSchema.get("required")


def test_env_passthrough_carries_what_a_script_needs() -> None:
    # PATH so a subprocess can find binaries, the workspace dir so a script can
    # resolve its own cell. Anything added here is a deliberate grant.
    assert "PATH" in ss._ENV_PASSTHROUGH
    assert "ASAREE_DATASET_WORKSPACE_DIR" in ss._ENV_PASSTHROUGH
    assert not any(k.endswith("API_KEY") or k.endswith("DATABASE_URL") for k in ss._ENV_PASSTHROUGH)
