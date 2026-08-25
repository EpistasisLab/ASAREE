"""asaree-script — executes the Script node wired into an agent, as plain Python.

The gap this fills: a Script node is a REFERENCE (its code is written to disk
and published as ambient ``_meta`` — see ``protocol_execution._ambient_meta_for``),
and ASAREE itself never executes one. Until this server existed, the only things
that could were ``asaree-sklearn-model``'s ``run_model_script`` and
``scikit-learn-mcp``'s ``run_script`` — both model-fitting harnesses that
require a dataset and a ``predict_proba``/``predict`` callable. So a Script node
wired without one of those servers was inert: the agent was told a script was
waiting and had no tool that could run it.

One tool, ``run_wired_script``, with no code-shaped argument in the normal case:
the script arrives ambiently, so what runs is byte-for-byte what the user wrote
on the canvas. It is ordinary Python — no contract about what the script must
define, no dataset required — which is what makes it the executor for the
scripts the sklearn harnesses reject.

Bundled and auto-registered as a global system server
(``services/system_mcp_servers.py``), and granted implicitly to any agent with a
Script node wired (``protocol_execution._resolve_script_tool_config``) — the same
arrangement as the Dataset connector and the workspace tools, so wiring a script
is the only gesture needed to let the agent run it.

**This is isolation, not a sandbox.** The script runs as a subprocess of this
server, with a deny-by-default environment (see ``_ENV_PASSTHROUGH``) and a
timeout, but it runs as the same user with the same filesystem. That is the trust
level ASAREE already operates at — the sklearn servers ``exec`` user-supplied
code in-process — and a subprocess is strictly better than that, not a security
boundary. Anything stronger (a container, a seccomp profile, a resource limit)
belongs here as a future change, and is why every spawn goes through one function.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server import FastMCP
from mcp.server.fastmcp import Context

INSTRUCTIONS = """\
Run the Python script wired into this step and report what it printed.

Call run_wired_script() with no arguments: the script arrives as ambient run \
context, so there is nothing to paste and nothing to retype. It is plain \
Python -- no required entry point, no dataset needed. Use it for anything the \
sklearn servers' script tools would reject, and read its stdout for the \
result."""

mcp = FastMCP("asaree-script", instructions=INSTRUCTIONS)

# stderr, never stdout: stdout is the MCP transport itself on a stdio server.
logger = logging.getLogger(__name__)

# Where ASAREE wrote the wired Script node's code, published under Motoro's
# caller-ambient prefix, and the cell workspace the run is working in. Both are
# out of the model's reach by design (``_ambient_meta_for``).
_META_KEY_SCRIPT_PATH = "motoro.ambient.script_path"
_META_KEY_WORKSPACE_ID = "motoro.workspace_id"

# Truncation budgets, matching the sklearn servers': a tool result is read by a
# model, so a script that prints in a loop must not cost more context than the
# answer it was called for. stdout gets the larger share because it is where a
# script puts its result; stderr is usually a traceback, whose useful part is
# the tail.
_STDOUT_CHARS = 4000
_STDERR_CHARS = 2000

_DEFAULT_TIMEOUT = 300
_MAX_TIMEOUT = 900

# Deny-by-default, and the deny half matters more than the pass half: this
# server is spawned with ASAREE_MCP_ALLOWED_ENV_VARS in its own environment
# (the product database URL and the internal API key among them — see .env), and
# a wired script is user-authored code that has no business reading either.
# Inheriting os.environ would hand every one of them over. What's left is what a
# script plausibly needs to run and find the workspace.
_ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "ASAREE_DATASET_WORKSPACE_DIR")


def _ambient(ctx: Context[Any, Any, Any] | None, key: str) -> str:
    """The caller's ambient ``_meta`` value for *key*, or ``""``.

    Deliberately total: no request context at all (a direct call, a client that
    doesn't use the convention) is a normal case outside an agent run.
    """
    if ctx is None:
        return ""
    try:
        extra = getattr(ctx.request_context.meta, "model_extra", None) or {}
    except Exception:  # noqa: BLE001 -- no request context outside a live call
        return ""
    value = extra.get(key)
    return value if isinstance(value, str) else ""


def _as_text(value: Any) -> str:
    """Whatever a subprocess handed back, as text.

    ``TimeoutExpired.stdout``/``.stderr`` carry raw BYTES even when the call
    asked for text mode (CPython builds the exception from the undecoded
    buffers), and either can be ``None`` when nothing was captured -- so the
    partial output of a killed script needs decoding that a completed one
    doesn't.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8", errors="replace")
    return ""


def _clip(text: str, budget: int, *, tail: bool = False) -> str:
    """*text* trimmed to *budget* chars, with a marker saying what was dropped."""
    if len(text) <= budget:
        return text
    dropped = len(text) - budget
    if tail:
        return f"[... {dropped} chars truncated ...]\n{text[-budget:]}"
    return f"{text[:budget]}\n[... {dropped} chars truncated ...]"


def _working_dir(workspace_id: str, script: Path) -> Path:
    """The cell's workspace directory when there is one, else the script's own.

    Running in the workspace is what lets a wired script reach this cell's data
    with relative paths (``state.json``, the staged parquet files) without being
    told where it is -- the same reasoning as every other tool resolving the
    workspace from ambient ``_meta`` instead of an argument.
    """
    if workspace_id:
        root = Path(os.environ.get("ASAREE_DATASET_WORKSPACE_DIR", "./data/workspaces")).resolve()
        candidate = (root / workspace_id).resolve()
        if root in candidate.parents and candidate.is_dir():
            return candidate
    return script.parent


@mcp.tool()
def run_wired_script(
    code: str = "",
    timeout_seconds: int = _DEFAULT_TIMEOUT,
    ctx: Context[Any, Any, Any] | None = None,
) -> str:
    """Execute the Python script wired into this step; return its output.

    Call this with NO arguments. The script is bound as ambient run context, so
    it never passes through you: what executes is byte-for-byte what the user
    wrote, and retyping it could only mangle it.

    Plain Python, run as a subprocess: nothing has to be defined, nothing is
    pre-bound, and any installed package can be imported. It runs in this cell's
    workspace directory, so relative paths reach the cell's own data.

    Read ``stdout`` for the result -- a script reports by printing. A non-zero
    ``exit_code`` means it raised; ``stderr`` holds the traceback. Both are
    truncated if long, and ``code_sha256`` identifies exactly what ran.

    Args:
        code: Python source to run INSTEAD of the wired script. Only for a
            genuine one-off; when a script is wired, omit this.
        timeout_seconds: Kill the script after this long (default 300, max 900).
            A timeout returns whatever it printed before it was killed.
    """
    script_path = "" if code.strip() else _ambient(ctx, _META_KEY_SCRIPT_PATH)
    if code.strip():
        source = code
    elif script_path:
        try:
            source = Path(script_path).read_text()
        except OSError as e:
            return json.dumps({"error": f"could not read the wired script at {script_path!r}: {e}"})
    else:
        return json.dumps({"error": "no script to run: none passed as `code`, and no script is wired into this step."})
    if not source.strip():
        return json.dumps({"error": "the wired script is empty."})

    code_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    timeout = max(1, min(int(timeout_seconds), _MAX_TIMEOUT))
    # A one-off `code` argument has no file of its own, so it is written next to
    # the workspace-resolved script when there is one and to a temp file
    # otherwise -- running a file (rather than piping to `python -`) keeps
    # tracebacks pointing at real line numbers.
    workspace_id = _ambient(ctx, _META_KEY_WORKSPACE_ID)
    if script_path:
        script = Path(script_path)
    else:
        cwd = _working_dir(workspace_id, Path.cwd())
        script = cwd / f"inline-{code_sha256[:12]}.py"
        try:
            script.write_text(source)
        except OSError as e:
            return json.dumps(
                {"error": f"could not write the inline script to {script}: {e}", "code_sha256": code_sha256}
            )

    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    # Unbuffered so a script killed by the timeout has still flushed what it
    # printed -- the whole value of a partial result is that it survives.
    env["PYTHONUNBUFFERED"] = "1"
    result: dict[str, Any] = {"code_sha256": code_sha256, "script": script.name}
    try:
        completed = subprocess.run(  # noqa: S603 -- user-authored script, by design; see module docstring
            [sys.executable, str(script)],
            cwd=str(_working_dir(workspace_id, script)),
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        logger.warning("wired_script_timeout", extra={"script": str(script), "timeout": timeout})
        return json.dumps(
            {
                **result,
                "timed_out": True,
                "error": f"the script was killed after {timeout}s.",
                "stdout": _clip(_as_text(e.stdout), _STDOUT_CHARS),
                "stderr": _clip(_as_text(e.stderr), _STDERR_CHARS, tail=True),
            }
        )
    except OSError as e:
        return json.dumps({**result, "error": f"could not start the script: {e}"})

    result["exit_code"] = completed.returncode
    result["stdout"] = _clip(completed.stdout, _STDOUT_CHARS)
    result["stderr"] = _clip(completed.stderr, _STDERR_CHARS, tail=True)
    if completed.returncode != 0:
        # Named as an error too, not just a non-zero code: a model skimming the
        # payload should not have to know that 0 is the good one.
        result["error"] = f"the script exited with code {completed.returncode}; see stderr."
    return json.dumps(result)


@mcp.tool()
def ping() -> str:
    """Health check — returns 'pong' to verify the server is running."""
    return "pong"


if __name__ == "__main__":
    mcp.run()
