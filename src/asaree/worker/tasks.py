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
from collections.abc import Coroutine
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
import asaree.models.experiment_design_revision  # noqa: F401 -- ProtocolRun.design_revision_id's FK target
import asaree.models.user  # noqa: F401 -- registers users for Protocol/ProtocolRun's owner_id FK
from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.services.protocol_execution import run_protocol
from asaree.services.protocol_runs import fail_protocol_run, get_protocol_run, list_stale_protocol_runs
from asaree.services.run_tools import gather_tools

logger = logging.getLogger(__name__)

# Statuses execute_run_task will still act on. Anything else means some other
# path (a previous attempt, a racing check_stale_runs) already closed the run
# out -- re-running it would re-execute an agent loop that already happened.
_ACTIONABLE_RUN_STATUSES = frozenset({RunStatus.PENDING, RunStatus.RUNNING})

# How long a cancelled task may spend recording why it died before we give up
# and leave the row to the stale-run cron. Short on purpose: this runs while
# the worker is already being torn down.
_CANCEL_CLEANUP_TIMEOUT_SECONDS = 5.0


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
    except asyncio.CancelledError:
        # CancelledError is a BaseException, so the `except Exception` below
        # does NOT catch it -- without this the run row is left exactly as the
        # cancellation found it (PENDING/RUNNING, no error) while arq retries
        # and then gives up, and nothing ever says why. See the same handler in
        # execute_protocol_run_task for the full reasoning.
        logger.warning("execute_run_task_cancelled", extra={"run_id": run_id_str})
        await _best_effort(fail_run(run_id, error="run was cancelled before it could finish"))
        raise
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
    except asyncio.CancelledError:
        # CancelledError is a BaseException, so `except Exception` below does
        # NOT catch it. Without this handler a cancelled run keeps whatever
        # status the cancellation interrupted -- "pending" if it hadn't got as
        # far as its first status write -- with a null error, arq retries it to
        # max_tries and drops it, and the row is then indistinguishable from a
        # job that was never picked up. A user sees "Run all cells" produce
        # cells that simply never ran, with nothing anywhere saying why.
        #
        # Cancellation reaches here from arq's job timeout, worker shutdown, or
        # a SIGTERM mid-flight. (It also used to arrive as collateral damage
        # from a concurrent MCP re-registration tearing down a transport this
        # task owned -- fixed in Motoro, but the durability gap is this task's
        # own regardless of what does the cancelling.)
        logger.warning("execute_protocol_run_task_cancelled", extra={"protocol_run_id": protocol_run_id_str})
        await _best_effort(_fail_protocol_run_now(protocol_run_id, "protocol run was cancelled before it could finish"))
        raise
    except Exception as e:  # noqa: BLE001 -- same boundary reasoning as execute_run_task
        logger.exception("execute_protocol_run_task_failed", extra={"protocol_run_id": protocol_run_id_str})
        async with get_session() as db:
            await fail_protocol_run(db, protocol_run_id, error=f"{type(e).__name__}: {e}")


async def _fail_protocol_run_now(protocol_run_id: uuid.UUID, error: str) -> None:
    async with get_session() as db:
        await fail_protocol_run(db, protocol_run_id, error=error)


async def _best_effort(coro: Coroutine[Any, Any, Any]) -> None:
    """Run *coro* to completion even though the calling task is being cancelled.

    A bare ``await`` in a cancellation handler is not reliable: the task already
    carries a pending cancellation, so the first suspension point can re-raise
    ``CancelledError`` and abandon the write half-done. ``shield`` keeps the
    inner coroutine running through that, and the timeout means a wedged
    connection delays shutdown by seconds rather than blocking it -- this is a
    last-ditch attempt to record *why* a run died, never something worth
    hanging the worker over. The stale-run cron is the backstop when it fails.
    """
    try:
        await asyncio.wait_for(asyncio.shield(coro), timeout=_CANCEL_CLEANUP_TIMEOUT_SECONDS)
    except (TimeoutError, asyncio.CancelledError):
        logger.warning("cancellation_cleanup_incomplete")
    except Exception:
        logger.exception("cancellation_cleanup_failed")


async def check_stale_runs(ctx: dict[str, Any]) -> None:
    """Fail any run whose worker appears to have died mid-flight.

    Covers both kinds: Motoro's agent ``Run``s and ASAREE's own
    ``ProtocolRun``s. The latter had no reconciler at all, so a protocol run
    interrupted before it could record a reason stayed non-terminal forever --
    see ``protocol_runs.list_stale_protocol_runs``.

    Keyed on ``last_heartbeat_at`` (written every phase of every iteration by
    the orchestrator), falling back to ``created_at`` for a run that died
    before its first heartbeat -- ``started_at`` is never populated anywhere
    in Motoro, so it is not a usable fallback.
    """
    settings = get_settings()
    threshold_s = settings.run_heartbeat_stale_seconds
    pending_threshold_s = settings.protocol_run_pending_stale_seconds
    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(seconds=threshold_s)

    running = await list_runs(status=RunStatus.RUNNING, limit=1000)
    stale = [r for r in running if (r.last_heartbeat_at or r.created_at) < cutoff]
    for run in stale:
        await fail_run(run.id, error=f"worker lost — no heartbeat for >= {threshold_s}s")
    if stale:
        logger.warning(
            "check_stale_runs_failed_stale_runs",
            extra={"count": len(stale), "run_ids": [str(r.id) for r in stale]},
        )

    async with get_session() as db:
        stale_protocol_runs = await list_stale_protocol_runs(
            db,
            running_cutoff=cutoff,
            pending_cutoff=now - timedelta(seconds=pending_threshold_s),
        )
        for protocol_run in stale_protocol_runs:
            waited_s = threshold_s if protocol_run.status == "running" else pending_threshold_s
            await fail_protocol_run(
                db,
                protocol_run.id,
                error=f"worker lost — no progress for >= {waited_s}s",
            )
    if stale_protocol_runs:
        logger.warning(
            "check_stale_runs_failed_stale_protocol_runs",
            extra={
                "count": len(stale_protocol_runs),
                "protocol_run_ids": [str(r.id) for r in stale_protocol_runs],
            },
        )
