"""arq task functions the worker registers: the run executor, the
protocol-run executor, and the stale-run cron that backstops the former.

execute_run_task is a plain core-API caller (get_run/get_agent/execute_run/
fail_run/list_runs), same boundary asaree.api.runs already respects -- no raw
session, no core model import beyond RunStatus (needed to filter/compare, not
to touch a table). execute_protocol_run_task is a thin wrapper the same
shape, delegating the actual graph walk to services.protocol_execution.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from motoro.mcp.registry import get_registry
from motoro.models.run import RunStatus
from motoro.runner import execute_run, fail_run, get_agent, get_run, list_runs

# The worker process never imports asaree.api.*/asaree.deps (unlike the API
# process, where every router module transitively imports these already) --
# without them, ProtocolRun/Protocol's mapper can't resolve their FK targets
# the first time this process touches either table (SQLAlchemy's
# cross-table _sorted_tables() needs every referenced model's module
# actually imported, not just the FK's string target to exist in the DB).
import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for Protocol->experiment's FK
import asaree.models.experiment  # noqa: F401 -- registers research_experiments for Protocol's FK
import asaree.models.user  # noqa: F401 -- registers users for Protocol/ProtocolRun's owner_id FK
from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.services.protocol_execution import run_protocol
from asaree.services.protocol_runs import fail_protocol_run, get_protocol_run
from asaree.services.run_tools import gather_tools

logger = logging.getLogger(__name__)

# Statuses execute_run_task will still act on. Anything else means some other
# path (a previous attempt, a racing check_stale_runs) already closed the run
# out -- re-running it would re-execute an agent loop that already happened.
_ACTIONABLE_RUN_STATUSES = frozenset({RunStatus.PENDING, RunStatus.RUNNING})


async def execute_run_task(ctx: dict[str, Any], run_id_str: str) -> None:
    """Execute one run to completion. *ctx* is arq's worker context (unused
    here -- no per-job state needs it)."""
    run_id = uuid.UUID(run_id_str)
    run = await get_run(run_id)
    if run is None:
        logger.warning("execute_run_task_missing_run", extra={"run_id": run_id_str})
        return
    if run.status not in _ACTIONABLE_RUN_STATUSES:
        logger.info(
            "execute_run_task_skip_non_actionable",
            extra={"run_id": run_id_str, "status": run.status.value},
        )
        return

    agent = await get_agent(run.agent_id)
    if agent is None:
        await fail_run(run_id, error=f"agent {run.agent_id} no longer exists")
        return

    timeout = agent.max_run_duration_seconds or get_settings().worker_job_timeout_seconds
    try:
        await asyncio.wait_for(
            execute_run(run_id=run_id, registry=get_registry(), available_tools=gather_tools(agent)),
            timeout=timeout,
        )
    except TimeoutError:
        await fail_run(run_id, error=f"run exceeded its {timeout}s execution budget")
    except Exception as e:  # noqa: BLE001 -- deliberately broad: this is the task's own
        # boundary. Anything execute_run raises must land on the run as FAILED
        # rather than propagate to arq, which would otherwise retry a whole
        # agent loop (real tool calls, real side effects) up to its default
        # max_tries — not safe to assume idempotent.
        logger.exception("execute_run_task_failed", extra={"run_id": run_id_str})
        await fail_run(run_id, error=f"{type(e).__name__}: {e}")


async def execute_protocol_run_task(ctx: dict[str, Any], protocol_run_id_str: str) -> None:
    """Walk one protocol run to completion. Same shape as execute_run_task:
    resolve the row, bound the whole walk with a timeout, and force-fail on
    timeout/exception rather than letting arq retry a partially-executed
    graph (real agent runs, real tool calls -- not safe to assume idempotent).
    """
    protocol_run_id = uuid.UUID(protocol_run_id_str)
    async with get_session() as db:
        run = await get_protocol_run(db, protocol_run_id)
    if run is None:
        logger.warning("execute_protocol_run_task_missing_run", extra={"protocol_run_id": protocol_run_id_str})
        return
    if run.status not in ("pending", "running"):
        logger.info(
            "execute_protocol_run_task_skip_non_actionable",
            extra={"protocol_run_id": protocol_run_id_str, "status": run.status},
        )
        return

    timeout = get_settings().worker_job_timeout_seconds
    try:
        await asyncio.wait_for(run_protocol(protocol_run_id), timeout=timeout)
    except TimeoutError:
        async with get_session() as db:
            await fail_protocol_run(db, protocol_run_id, error=f"protocol run exceeded its {timeout}s execution budget")
    except Exception as e:  # noqa: BLE001 -- same boundary reasoning as execute_run_task
        logger.exception("execute_protocol_run_task_failed", extra={"protocol_run_id": protocol_run_id_str})
        async with get_session() as db:
            await fail_protocol_run(db, protocol_run_id, error=f"{type(e).__name__}: {e}")


async def check_stale_runs(ctx: dict[str, Any]) -> None:
    """Fail any RUNNING run whose worker appears to have died mid-flight.

    Keyed on ``last_heartbeat_at`` (written every phase of every iteration by
    the orchestrator), falling back to ``created_at`` for a run that died
    before its first heartbeat -- ``started_at`` is never populated anywhere
    in Motoro, so it is not a usable fallback.
    """
    threshold_s = get_settings().run_heartbeat_stale_seconds
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=threshold_s)
    running = await list_runs(status=RunStatus.RUNNING, limit=1000)
    stale = [r for r in running if (r.last_heartbeat_at or r.created_at) < cutoff]
    for run in stale:
        await fail_run(run.id, error=f"worker lost — no heartbeat for >= {threshold_s}s")
    if stale:
        logger.warning(
            "check_stale_runs_failed_stale_runs",
            extra={"count": len(stale), "run_ids": [str(r.id) for r in stale]},
        )
