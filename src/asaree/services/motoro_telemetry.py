"""Read-only telemetry adapter at the Motoro storage seam.

Motoro's public runner reads eagerly hydrate Agent and RunStep relationships.
Operational accounting needs only scalar columns. Keep that storage knowledge
here, use its public audited system_session, and never join product/core tables.
Accounting never loads evidence. Selected evidence crosses this seam only in
bounded text or trace pages after the product verifies ownership.
"""

from __future__ import annotations

import uuid
from typing import Any

from motoro.models.database import system_session
from motoro.models.run import AgentRun, RunStep
from sqlalchemy import Text, cast, false, func, literal_column, or_, select

PAGE_SIZE = 200
ATTRIBUTION = AgentRun.run_metadata.op("->>", return_type=Text)(literal_column("'protocol_run_id'"))


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
        matches.append(ATTRIBUTION.in_([str(identifier) for identifier in protocol_run_ids]))
    if referenced_ids:
        matches.append(AgentRun.id.in_(referenced_ids))
    statement = select(
        AgentRun.id,
        AgentRun.status,
        AgentRun.token_usage,
        AgentRun.cost_estimate,
        ATTRIBUTION.label("protocol_run_id"),
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


async def read_prompt(owner_id: uuid.UUID, agent_run_id: uuid.UUID, offset: int, limit: int) -> dict[str, Any]:
    async with system_session(reason="asaree.results: selected prompt page") as db:
        row = (
            (
                await db.execute(
                    select(
                        func.substr(AgentRun.input, offset + 1, limit).label("text"),
                        func.length(AgentRun.input).label("length"),
                    ).where(AgentRun.id == agent_run_id, AgentRun.owner_id == owner_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("prompt_unavailable")
        return {"text": row["text"], "next_offset": offset + limit if row["length"] > offset + limit else None}


async def read_trace(owner_id: uuid.UUID, agent_run_id: uuid.UUID, after: int = -1) -> dict[str, Any]:
    async with system_session(reason="asaree.results: selected trace page") as db:
        rows = list(
            (
                await db.execute(
                    select(
                        RunStep.id,
                        RunStep.sequence,
                        RunStep.iteration,
                        RunStep.phase,
                        RunStep.started_at,
                        RunStep.completed_at,
                        func.left(cast(RunStep.output, Text), 2048).label("preview"),
                    )
                    .join(AgentRun, AgentRun.id == RunStep.run_id)
                    .where(AgentRun.id == agent_run_id, AgentRun.owner_id == owner_id, RunStep.sequence > after)
                    .order_by(RunStep.sequence)
                    .limit(21)
                )
            ).mappings()
        )
        return {
            "items": [dict(row) for row in rows[:20]],
            "next_after": rows[19]["sequence"] if len(rows) > 20 else None,
        }


async def read_step(
    owner_id: uuid.UUID, agent_run_id: uuid.UUID, step_id: uuid.UUID, offset: int, limit: int
) -> dict[str, Any]:
    document = cast(
        func.jsonb_build_object(
            "input",
            RunStep.input,
            "output",
            RunStep.output,
            "llm_call",
            RunStep.llm_call,
            "tool_call",
            RunStep.tool_call,
        ),
        Text,
    )
    async with system_session(reason="asaree.results: selected exact step evidence page") as db:
        row = (
            (
                await db.execute(
                    select(
                        func.substr(document, offset + 1, limit).label("text"), func.length(document).label("length")
                    )
                    .join(AgentRun, AgentRun.id == RunStep.run_id)
                    .where(AgentRun.id == agent_run_id, AgentRun.owner_id == owner_id, RunStep.id == step_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("step_unavailable")
        return {"text": row["text"], "next_offset": offset + limit if row["length"] > offset + limit else None}
