from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from asaree.services import motoro_telemetry
from asaree.services.result_usage import provider_usage, summarize_usage


async def test_usage_read_is_owner_scoped_batched_through_motoro(monkeypatch):
    calls = []

    async def summaries(**kwargs):
        calls.append(kwargs)
        return [{"id": UUID(int=9), "status": "completed", "token_usage": None, "cost_estimate": None,
                 "run_metadata": {"protocol_run_id": str(UUID(int=2)), "node_id": "agent-1"}}]

    monkeypatch.setattr(motoro_telemetry.runner, "list_run_summaries", summaries)
    rows = await motoro_telemetry.read_usage_page(UUID(int=1), [UUID(int=2)], [UUID(int=3)])
    assert calls == [{"owner_id": UUID(int=1), "metadata_key": "protocol_run_id", "metadata_values": [str(UUID(int=2))],
                      "run_ids": [UUID(int=3)], "after_id": None, "limit": 200}]
    assert rows[0]["protocol_run_id"] == str(UUID(int=2)) and rows[0]["node_id"] == "agent-1"
    assert "run_metadata" not in rows[0]
    with pytest.raises(ValueError):
        await motoro_telemetry.read_usage_page(UUID(int=1), [UUID(int=2)] * 201, [])


def test_usage_sums_distinct_causal_runs_and_excludes_queue_time():
    started = datetime(2026, 10, 12, tzinfo=UTC)
    rows = [
        {
            "id": UUID(int=1),
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 10},
            "cost_estimate": Decimal("0.01"),
        },
        {
            "id": UUID(int=2),
            "token_usage": {"input_tokens": 200, "output_tokens": 20},
            "cost_estimate": Decimal("0.02"),
        },
    ]
    result = summarize_usage([*rows, rows[0]], started_at=started, completed_at=started + timedelta(seconds=15))
    assert result["agent_run_count"] == 2
    assert result["total_tokens"] == 330
    assert result["cost_usd"] == 0.03
    assert result["duration_seconds"] == 15
    assert result["complete"]


def test_missing_provider_fields_and_legacy_timestamps_stay_unknown():
    assert provider_usage({"input_tokens": 0}, 0) == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cost_usd": None,
    }
    result = summarize_usage([{"id": UUID(int=1)}], started_at=None, completed_at=None)
    assert result["duration_seconds"] is None
    assert result["cost_usd"] is None
    assert result["total_tokens"] is None
    assert result["settled"]
    assert not result["complete"]


def test_node_accounting_includes_rejected_calls_without_double_counting():
    rows = [
        {"id": UUID(int=i), "node_id": node, "token_usage": {"total_tokens": 10}, "cost_estimate": 0.01}
        for i, node in enumerate(("critic", "critic", "worker"), start=1)
    ]
    result = summarize_usage([*rows, rows[0]], started_at=None, completed_at=None)
    assert result["agent_run_count"] == 3
    assert result["by_node"]["critic"]["agent_run_count"] == 2
    assert result["by_node"]["critic"]["cost_usd"] == 0.02
    assert result["by_node"]["worker"]["total_tokens"] == 10


def test_unfinished_causal_work_keeps_accounting_pending():
    result = summarize_usage(
        [{"id": UUID(int=1), "status": "running", "node_id": "worker"}], started_at=None, completed_at=None
    )
    assert not result["settled"]
    assert not result["by_node"]["worker"]["settled"]
