from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.dialects import postgresql

from asaree.services.motoro_telemetry import usage_query
from asaree.services.result_usage import provider_usage, summarize_usage


def test_usage_read_is_owner_scoped_batched_and_never_loads_traces():
    sql = str(
        usage_query(UUID(int=1), [UUID(int=2)], [UUID(int=3)]).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "agent_runs.owner_id" in sql
    assert "protocol_run_id" in sql
    assert "LIMIT 200" in sql
    assert "run_steps" not in sql
    assert "agent_runs.input" not in sql
    assert "agent_runs.output" not in sql
    assert "config_snapshot" not in sql


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
