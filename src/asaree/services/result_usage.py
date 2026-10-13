"""Resumable operational accounting and preview backfill, outside HTTP reads."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Any

from sqlalchemy import and_, case, cast, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB

from asaree.models.database import get_session
from asaree.models.protocol import Protocol
from asaree.models.protocol_run import ProtocolRun
from asaree.services.motoro_telemetry import PAGE_SIZE, read_usage_page

logger = logging.getLogger(__name__)
BATCH_SIZE = 200
TERMINAL = ("completed", "failed", "cancelled", "limit_reached")


@dataclass
class UsageAccumulator:
    totals: dict[str, float | int | None] = field(
        default_factory=lambda: {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cost_usd": None,
        }
    )
    calls: int = 0
    reported_tokens: int = 0
    reported_cost: int = 0
    by_node: dict[str, UsageAccumulator] = field(default_factory=dict)
    track_nodes: bool = True
    pending_calls: int = 0

    def add(self, row: dict[str, Any]) -> None:
        node_id = row.get("node_id")
        if self.track_nodes and isinstance(node_id, str) and node_id:
            self.by_node.setdefault(node_id, UsageAccumulator(track_nodes=False)).add(row)
        usage = provider_usage(row.get("token_usage"), row.get("cost_estimate"))
        self.calls += 1
        self.pending_calls += row.get("status") in {"pending", "running", "paused", "awaiting_human"}
        self.reported_tokens += usage["total_tokens"] is not None
        self.reported_cost += usage["cost_usd"] is not None
        for key, value in usage.items():
            if value is not None:
                self.totals[key] = (self.totals[key] or 0) + value

    def finish(self, started_at: datetime | None, completed_at: datetime | None) -> dict[str, Any]:
        return {
            **self.totals,
            "agent_run_count": self.calls,
            "reported_usage_count": self.reported_tokens,
            "reported_cost_count": self.reported_cost,
            "complete": self.calls > 0
            and self.pending_calls == 0
            and self.reported_tokens == self.calls
            and self.reported_cost == self.calls,
            "settled": self.pending_calls == 0,
            "captured_at": datetime.now(UTC).isoformat(),
            "by_node": {node: totals.finish(None, None) for node, totals in self.by_node.items()},
            "duration_seconds": max(0, (completed_at - started_at).total_seconds())
            if started_at and completed_at
            else None,
        }


def provider_usage(raw: Any, cost: Any) -> dict[str, float | int | None]:
    """Preserve unreported provider fields, including Motoro's ambiguous zero defaults."""
    source = raw if isinstance(raw, dict) else {}
    result: dict[str, float | int | None] = {}
    for key, aliases in {
        "input_tokens": ("input_tokens", "prompt_tokens", "input_token_count"),
        "output_tokens": ("output_tokens", "completion_tokens", "output_token_count"),
        "total_tokens": ("total_tokens", "total_token_count"),
    }.items():
        values = [source.get(alias) for alias in aliases]
        result[key] = next(
            (
                int(value)
                for value in values
                if isinstance(value, int | float | Decimal)
                and not isinstance(value, bool)
                and isfinite(float(value))
                and value > 0
            ),
            None,
        )
    if result["total_tokens"] is None and result["input_tokens"] is not None and result["output_tokens"] is not None:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    result["cost_usd"] = (
        float(cost) if isinstance(cost, int | float | Decimal) and isfinite(float(cost)) and cost > 0 else None
    )
    return result


def summarize_usage(
    rows: list[dict[str, Any]],
    *,
    started_at: datetime | None,
    completed_at: datetime | None,
) -> dict[str, Any]:
    unique = {row["id"]: row for row in rows}
    accumulator = UsageAccumulator()
    for row in unique.values():
        accumulator.add(row)
    return accumulator.finish(started_at, completed_at)


async def capture_result_usage(_ctx: dict[str, Any], *, only_run_id: uuid.UUID | None = None) -> None:
    """Select bounded durable work, capture all causally attributed runs, then publish.

    A null/unfinished usage document is the durable work item. Retrying replaces
    the projection; it never edits immutable measurement/evidence documents.
    Advisory locking prevents duplicate collectors across worker processes.
    """
    async with get_session() as db:
        locked = await db.scalar(select(func.pg_try_advisory_xact_lock(73912841)))
        if not locked:
            return
        now = datetime.now(UTC)
        node = (
            func.jsonb_each(
                case(
                    (func.jsonb_typeof(ProtocolRun.node_runs) == "object", ProtocolRun.node_runs),
                    else_=cast(literal("{}"), JSONB),
                )
            )
            .table_valued("key", "value")
            .lateral()
        )
        runs = (
            (
                await db.execute(
                    select(
                        ProtocolRun.id,
                        ProtocolRun.owner_id,
                        ProtocolRun.started_at,
                        ProtocolRun.completed_at,
                        ProtocolRun.attempt_result["task_completed_at"].astext.label("task_completed_at"),
                        ProtocolRun.usage_summary,
                        # Extract only final pointers, not complete output/trace documents.
                        select(func.jsonb_agg(func.jsonb_build_object("run_id", cast(node.c.value, JSONB)["run_id"])))
                        .select_from(node)
                        .correlate(ProtocolRun)
                        .scalar_subquery()
                        .label("pointers"),
                    )
                    .join(Protocol, Protocol.id == ProtocolRun.protocol_id)
                    .where(
                        Protocol.experiment_id.is_not(None),
                        ProtocolRun.is_test_run.is_(False),
                        ProtocolRun.target_node_id.is_(None),
                        ProtocolRun.status.in_(TERMINAL),
                        ProtocolRun.id == only_run_id if only_run_id else literal(True),
                        or_(
                            ProtocolRun.usage_summary.is_(None),
                            ProtocolRun.usage_summary == cast(literal("null"), JSONB),
                            and_(
                                ProtocolRun.usage_summary["settled"].as_boolean().is_not(True),
                                ProtocolRun.usage_summary["retry_at"].astext <= now.isoformat(),
                            ),
                        ),
                    )
                    .order_by(
                        case(
                            (
                                or_(
                                    ProtocolRun.usage_summary.is_(None),
                                    ProtocolRun.usage_summary == cast(literal("null"), JSONB),
                                ),
                                0,
                            ),
                            else_=1,
                        ),
                        ProtocolRun.usage_summary["retry_at"].astext,
                        ProtocolRun.created_at,
                        ProtocolRun.id,
                    )
                    .limit(BATCH_SIZE)
                )
            )
            .mappings()
            .all()
        )
        if not runs:
            return
        owners: defaultdict[uuid.UUID, list[Any]] = defaultdict(list)
        for run in runs:
            owners[run["owner_id"]].append(run)
        for owner_id, owned_runs in owners.items():
            try:
                async with db.begin_nested():
                    identifiers = [run["id"] for run in owned_runs]
                    references: defaultdict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
                    for run in owned_runs:
                        for pointer in run["pointers"] or []:
                            try:
                                references[uuid.UUID(pointer["run_id"])].add(run["id"])
                            except (ValueError, TypeError, KeyError):
                                continue
                    # Pointers are compatibility fallback for runs predating causal metadata.
                    # Attribution queries also include rejected critics/delegated work.
                    accumulators: defaultdict[uuid.UUID, UsageAccumulator] = defaultdict(UsageAccumulator)
                    identifier_set = set(identifiers)
                    seen_references: set[uuid.UUID] = set()
                    reference_ids = list(references)
                    reference_batches = [
                        reference_ids[index : index + PAGE_SIZE] for index in range(0, len(reference_ids), PAGE_SIZE)
                    ]
                    for attribution_ids, fallback in [(identifiers, []), *[([], batch) for batch in reference_batches]]:
                        after = None
                        while True:
                            page = await read_usage_page(owner_id, attribution_ids, fallback, after)
                            for row in page:
                                if fallback and row["id"] in seen_references:
                                    continue
                                if row["id"] in references:
                                    seen_references.add(row["id"])
                                attributed = row.get("protocol_run_id")
                                try:
                                    subjects = (
                                        {uuid.UUID(attributed)} if attributed else references.get(row["id"], set())
                                    )
                                except ValueError:
                                    subjects = references.get(row["id"], set())
                                for subject in subjects & identifier_set:
                                    accumulators[subject].add(row)
                            if len(page) < PAGE_SIZE:
                                break
                            after = page[-1]["id"]
                    for run in owned_runs:
                        task_end = run["task_completed_at"]
                        try:
                            completed = datetime.fromisoformat(task_end) if task_end else run["completed_at"]
                        except ValueError:
                            completed = run["completed_at"]
                        if completed and run["started_at"] and completed.tzinfo is None:
                            completed = None
                        summary = accumulators[run["id"]].finish(run["started_at"], completed)
                        if not summary["settled"]:
                            summary["retry_at"] = (now + timedelta(seconds=5)).isoformat()
                        await db.execute(
                            update(ProtocolRun)
                            .where(ProtocolRun.id == run["id"])
                            .values(
                                usage_summary=summary,
                                updated_at=ProtocolRun.updated_at,
                                result_summary=func.asaree_result_preview(
                                    ProtocolRun.attempt_result, ProtocolRun.node_runs
                                ),
                            )
                        )
            except Exception:
                logger.exception("result_usage_capture_failed", extra={"batch_size": len(owned_runs)})
                for run in owned_runs:
                    tries = min(10, int((run["usage_summary"] or {}).get("tries", 0)) + 1)
                    await db.execute(
                        update(ProtocolRun)
                        .where(ProtocolRun.id == run["id"])
                        .values(
                            usage_summary={
                                "settled": False,
                                "tries": tries,
                                "retry_at": (now + timedelta(seconds=min(300, 5 * 2**tries))).isoformat(),
                            },
                            updated_at=ProtocolRun.updated_at,
                            result_summary=func.asaree_result_preview(
                                ProtocolRun.attempt_result, ProtocolRun.node_runs
                            ),
                        )
                    )
