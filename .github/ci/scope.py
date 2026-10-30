"""Select CI suites from the full Git diff; unknown paths conservatively run both."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def classify(paths: list[str]) -> tuple[bool, bool]:
    backend = frontend = False
    for path in paths:
        name = Path(path).name
        if (
            path.startswith(("docs/", ".github/ISSUE_TEMPLATE/", ".github/PULL_REQUEST_TEMPLATE/"))
            or name in {"README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "LICENSE"}
            or ("/" not in path and path.endswith((".md", ".rst")))
            or path in {".github/CODEOWNERS", ".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md"}
        ):
            continue
        if path.startswith("frontend/"):
            frontend = True
        elif path.startswith(
            ("src/", "tests/", "sdk/", "workspace-core/", "mcp-servers/", "scripts/", "personas/")
        ) or path in {"pyproject.toml", "uv.lock"}:
            backend = True
        else:
            backend = frontend = True
    return backend, frontend


def needs_stats_validation(paths: list[str]) -> bool:
    """Numerical inputs, tests and dependencies can invalidate simulation evidence."""
    for path in paths:
        if classify([path]) == (False, False):
            continue
        if (
            path.startswith(("src/asaree/stats/", "src/asaree/services/stats_", "scripts/stats_"))
            or path in {
                "src/asaree/api/stats.py",
                "src/asaree/worker/stats_task.py",
                "src/asaree/models/stats_analysis.py",
                "tests/conftest.py",
                "pyproject.toml",
                "uv.lock",
            }
            or path.startswith(("tests/test_stats_", "tests/test_beta_", "tests/fixtures/"))
            or classify([path]) == (True, True)
        ):
            return True
    return False


def changed_paths(event_name: str, event: dict[str, Any], *, cwd: Path | None = None) -> list[str]:
    if event_name == "pull_request":
        base, head = event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
        separator = "..."  # Changes introduced by this PR, excluding base-branch advances.
    elif event_name == "push":
        base, head = event["before"], event["after"]
        separator = ".."  # Include every commit in this push, not just the last one.
    else:
        raise ValueError("Unknown event; run both suites")
    for sha in (base, head):
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) or set(sha) == {"0"}:
            raise ValueError("Missing comparison commit; run both suites")
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}{separator}{head}", "--"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    # Renames appear as deletion + addition so moving code across suites selects both.
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def main() -> None:
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        if os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
            backend = frontend = stats_validation = True
            print("Manual full validation: running all suites.")
        else:
            paths = changed_paths(os.environ["GITHUB_EVENT_NAME"], event)
            backend, frontend = classify(paths)
            stats_validation = needs_stats_validation(paths)
            print(f"Examined {len(paths)} changed paths.")
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        print("Unable to determine the complete change scope; running all suites.")
        backend = frontend = stats_validation = True
    output = (
        f"backend={str(backend).lower()}\nfrontend={str(frontend).lower()}\n"
        f"stats_validation={str(stats_validation).lower()}\n"
    )
    print(output, end="")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write(output)


if __name__ == "__main__":
    main()
