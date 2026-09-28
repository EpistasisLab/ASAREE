"""Regression tests for parity gaps found in the SDK audit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from asaree_client._multipart import multipart_directory
from asaree_client._transport import RetryPolicy, RetryTransport
from asaree_client.models import Agent
from asaree_client.resources.skills import Skills
from asaree_client.resources.tools import Tools


class SequenceTransport(httpx.BaseTransport):
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = iter(statuses)
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(next(self.statuses), request=request)


class RecordingClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _get(self, path: str, **kwargs: Any) -> Any:
        self.calls.append(("GET", path, kwargs))
        return self.response

    def _post(self, path: str, **kwargs: Any) -> Any:
        self.calls.append(("POST", path, kwargs))
        return self.response

    def _patch(self, path: str, **kwargs: Any) -> Any:
        self.calls.append(("PATCH", path, kwargs))
        return self.response


def _skill_response() -> dict[str, Any]:
    now = datetime.now(tz=UTC).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "name": "audit",
        "description": "Checks an experiment",
        "body": "# Audit",
        "frontmatter": {},
        "is_system": False,
        "source_filename": None,
        "files": [],
        "created_at": now,
        "updated_at": now,
    }


def _mcp_response() -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "name": "local",
        "transport": "stdio",
        "command": "server",
        "url": None,
        "status": "connected",
        "error_message": None,
        "capabilities": None,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }


def test_agent_parses_current_response_fields() -> None:
    now = datetime.now(tz=UTC)
    source_plan_id = uuid.uuid4()

    agent = Agent.model_validate(
        {
            "id": uuid.uuid4(),
            "name": "analyst",
            "goal": "analyze",
            "description": "",
            "system_prompt": "Work carefully",
            "model_config": {},
            "tool_config": {},
            "memory_config": {},
            "is_system": True,
            "skill_config": {"skill_ids": []},
            "auto_eval_enabled": False,
            "auto_eval_model": "gpt-5",
            "source_plan_id": source_plan_id,
            "source_plan_title": "Analysis plan",
            "created_at": now,
            "updated_at": now,
        }
    )

    assert agent.is_system is True
    assert agent.skill_config == {"skill_ids": []}
    assert agent.auto_eval_enabled is False
    assert agent.auto_eval_model == "gpt-5"
    assert agent.source_plan_id == source_plan_id
    assert agent.source_plan_title == "Analysis plan"


def test_post_is_not_retried_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = SequenceTransport([500, 200])
    transport = RetryTransport(inner, RetryPolicy(max_retries=1, base_delay=0, jitter=False))
    monkeypatch.setattr("asaree_client._transport.time.sleep", lambda _seconds: None)

    response = transport.handle_request(httpx.Request("POST", "https://example.test/runs"))

    assert response.status_code == 500
    assert inner.calls == 1


def test_get_retries_transient_status(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = SequenceTransport([500, 200])
    transport = RetryTransport(inner, RetryPolicy(max_retries=1, base_delay=0, jitter=False))
    monkeypatch.setattr("asaree_client._transport.time.sleep", lambda _seconds: None)

    response = transport.handle_request(httpx.Request("GET", "https://example.test/runs"))

    assert response.status_code == 200
    assert inner.calls == 2


def test_post_can_explicitly_opt_in_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = SequenceTransport([500, 200])
    transport = RetryTransport(inner, RetryPolicy(max_retries=1, base_delay=0, jitter=False))
    monkeypatch.setattr("asaree_client._transport.time.sleep", lambda _seconds: None)
    request = httpx.Request(
        "POST",
        "https://example.test/tools/read-only/call",
        extensions={"asaree_allow_retry": True},
    )

    response = transport.handle_request(request)

    assert response.status_code == 200
    assert inner.calls == 2


def test_skills_list_page_retains_total_and_list_remains_compatible() -> None:
    response = {"items": [_skill_response()], "total": 7}
    client = RecordingClient(response)
    skills = Skills(client)

    page = skills.list_page(limit=25)
    items = skills.list(limit=25)

    assert page.total == 7
    assert [skill.name for skill in page.items] == ["audit"]
    assert [skill.name for skill in items] == ["audit"]


def test_create_skill_from_url_defaults_to_repository_root() -> None:
    client = RecordingClient(_skill_response())

    Skills(client).create_from_url("https://github.com/example/skills")

    assert client.calls == [
        (
            "POST",
            "/skills/from-url",
            {"json": {"url": "https://github.com/example/skills", "subdirectory": ""}},
        )
    ]


def test_mcp_update_omits_none_fields() -> None:
    client = RecordingClient(_mcp_response())

    Tools(client).update_server("server-id", name="renamed", command=None, headers=None)

    assert client.calls == [("PATCH", "/mcp-servers/server-id", {"json": {"name": "renamed"}})]


def test_tool_call_retry_is_explicit() -> None:
    client = RecordingClient({"is_error": False, "content": "ok"})
    tools = Tools(client)

    tools.call_tool("server-id", "read_only")
    tools.call_tool("server-id", "read_only", retry=True)

    assert client.calls == [
        (
            "POST",
            "/mcp-servers/server-id/tools/read_only/call",
            {"json": {"arguments": {}}},
        ),
        (
            "POST",
            "/mcp-servers/server-id/tools/read_only/call",
            {
                "json": {"arguments": {}},
                "extensions": {"asaree_allow_retry": True},
            },
        ),
    ]


def test_directory_upload_preserves_relative_paths_and_closes_files(tmp_path: Path) -> None:
    folder = tmp_path / "skill"
    nested = folder / "references"
    nested.mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Skill")
    (nested / "notes.md").write_text("notes")

    with multipart_directory(str(folder)) as files:
        assert [item[1][0] for item in files] == ["skill/SKILL.md", "skill/references/notes.md"]
        handles = [item[1][1] for item in files]
        assert all(not handle.closed for handle in handles)

    assert all(handle.closed for handle in handles)
