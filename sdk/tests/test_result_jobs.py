from typing import Any

import pytest

from asaree_client.resources.experiments import Experiments


class Client:
    def __init__(self, pages: list[dict[str, Any]]):
        self.pages = iter(pages)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((path, kwargs))
        return next(self.pages)

    def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((path, kwargs))
        return next(self.pages)


def test_iteration_preserves_scope_and_uses_server_cursor():
    client = Client([{"items": [{"id": 1}], "next_cursor": "next"}, {"items": [{"id": 2}], "next_cursor": None}])
    assert list(Experiments(client).iter_executions("experiment", scope={"protocol_revision_id": "version"})) == [
        {"id": 1},
        {"id": 2},
    ]
    assert client.calls[1][1]["params"] == {"protocol_revision_id": "version", "limit": 100, "cursor": "next"}
    assert "cursor" not in client.calls[0][1]["params"]


def test_preview_creation_does_not_confirm_or_execute():
    client = Client([{"id": "job", "status": "pending"}])
    assert Experiments(client).create_result_job(
        "experiment", kind="plan", request_key="stable", replicate_ids=["parent"]
    ) == {"id": "job", "status": "pending"}
    assert len(client.calls) == 1
    assert client.calls[0][1]["json"] == {"kind": "plan", "request_key": "stable", "replicate_ids": ["parent"]}


def test_invalid_page_size_never_sends_request():
    with pytest.raises(ValueError):
        list(Experiments(Client([])).iter_executions("experiment", page_size=101))
