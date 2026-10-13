"""Read-only telemetry adapter at the Motoro storage seam.

Every read goes through Motoro's runner (``list_run_summaries``,
``list_run_step_summaries``, ``read_run_input_chunk``, ``read_run_step_chunk``),
which owns the queries and the indexes behind them -- ASAREE never queries or
indexes core's tables. Accounting never loads evidence.
Selected evidence crosses this seam only in bounded text or trace pages after the
product verifies ownership.
"""

from __future__ import annotations

import uuid
from typing import Any

from motoro import runner

PAGE_SIZE = 200
# The key ASAREE stamps on every Motoro run it starts (see runtime_metrics).
ATTRIBUTION_KEY = "protocol_run_id"


async def read_usage_page(
    owner_id: uuid.UUID,
    protocol_run_ids: list[uuid.UUID],
    referenced_ids: list[uuid.UUID],
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    if len(protocol_run_ids) > PAGE_SIZE or len(referenced_ids) > PAGE_SIZE:
        raise ValueError("telemetry_batch_too_large")
    rows = await runner.list_run_summaries(
        owner_id=owner_id,
        metadata_key=ATTRIBUTION_KEY,
        metadata_values=[str(identifier) for identifier in protocol_run_ids],
        run_ids=referenced_ids,
        after_id=after_id,
        limit=PAGE_SIZE,
    )
    for row in rows:
        metadata = row.pop("run_metadata") or {}
        row["protocol_run_id"] = metadata.get(ATTRIBUTION_KEY)
        row["node_id"] = metadata.get("node_id")
    return rows


async def read_prompt(owner_id: uuid.UUID, agent_run_id: uuid.UUID, offset: int, limit: int) -> dict[str, Any]:
    chunk = await runner.read_run_input_chunk(agent_run_id, owner_id=owner_id, offset=offset, limit=limit)
    if chunk is None:
        raise ValueError("prompt_unavailable")
    return chunk


async def read_trace(owner_id: uuid.UUID, agent_run_id: uuid.UUID, after: int = -1) -> dict[str, Any]:
    return await runner.list_run_step_summaries(agent_run_id, owner_id=owner_id, after_sequence=after, limit=20)


async def read_step(
    owner_id: uuid.UUID, agent_run_id: uuid.UUID, step_id: uuid.UUID, offset: int, limit: int
) -> dict[str, Any]:
    chunk = await runner.read_run_step_chunk(agent_run_id, step_id, owner_id=owner_id, offset=offset, limit=limit)
    if chunk is None:
        raise ValueError("step_unavailable")
    return chunk
