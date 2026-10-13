"""Read-only telemetry adapter at the Motoro storage seam.

Motoro's public runner reads eagerly hydrate Agent and RunStep relationships.
Operational accounting needs only scalar columns. Keep that storage knowledge
here, use its public audited system_session, and never join product/core tables.
No transcript, input, output, or step documents cross this seam.
"""

from __future__ import annotations

import uuid
from typing import Any

from motoro.models.database import system_session
from motoro.models.run import AgentRun
from sqlalchemy import false, or_, select

PAGE_SIZE = 200


def usage_query(
    owner_id: uuid.UUID,
    protocol_run_ids: list[uuid.UUID],
    referenced_ids: list[uuid.UUID],
    after_id: uuid.UUID | None = None,
) -> Any:
    if len(protocol_run_ids) > PAGE_SIZE or len(referenced_ids) > PAGE_SIZE:
        raise ValueError("telemetry_batch_too_large")
    matches = []
    if protocol_run_ids:
        matches.append(
            AgentRun.run_metadata["protocol_run_id"].astext.in_([str(identifier) for identifier in protocol_run_ids])
        )
    if referenced_ids:
        matches.append(AgentRun.id.in_(referenced_ids))
    statement = select(
        AgentRun.id,
        AgentRun.token_usage,
        AgentRun.cost_estimate,
        AgentRun.run_metadata["protocol_run_id"].astext.label("protocol_run_id"),
        AgentRun.run_metadata["node_id"].astext.label("node_id"),
    )
    statement = statement.where(AgentRun.owner_id == owner_id, or_(*matches) if matches else false())
    if after_id:
        statement = statement.where(AgentRun.id > after_id)
    return statement.order_by(AgentRun.id).limit(PAGE_SIZE)


async def read_usage_page(
    owner_id: uuid.UUID,
    protocol_run_ids: list[uuid.UUID],
    referenced_ids: list[uuid.UUID],
    after_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    async with system_session(reason="asaree.results: capture bounded operational telemetry") as db:
        return [
            dict(row)
            for row in (
                await db.execute(
                    usage_query(owner_id, protocol_run_ids, referenced_ids, after_id),
                )
            ).mappings()
        ]
