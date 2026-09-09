"""Hand a run to the background worker instead of executing it inline.

A lazily-created, process-wide arq redis pool -- built once per process
(the API process), not once per call, so enqueueing a run doesn't pay a new
connection's setup cost every time ``POST /runs`` is hit.
"""

from __future__ import annotations

import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from asaree.config import get_settings

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def enqueue_run(run_id: uuid.UUID) -> None:
    """Queue *run_id* for asaree.worker.tasks.execute_run_task.

    ``_job_id`` keyed on the run's own id: arq refuses to enqueue a second job
    under an id that already exists (``enqueue_job`` returns ``None`` instead),
    so a client retry on ``POST /runs`` racing an already-queued job can't
    double-execute it.
    """
    pool = await _get_pool()
    await pool.enqueue_job("execute_run_task", str(run_id), _job_id=f"run:{run_id}")


async def enqueue_protocol_run(protocol_run_id: uuid.UUID) -> None:
    """Same idempotent-enqueue pattern as :func:`enqueue_run`, for
    asaree.worker.tasks.execute_protocol_run_task."""
    pool = await _get_pool()
    await pool.enqueue_job("execute_protocol_run_task", str(protocol_run_id), _job_id=f"protocol-run:{protocol_run_id}")


async def enqueue_metric_evaluation(protocol_run_id: uuid.UUID) -> None:
    """Queue an idempotent backfill/retry of configured post-run metrics."""
    pool = await _get_pool()
    await pool.enqueue_job(
        "evaluate_protocol_run_metrics_task", str(protocol_run_id), _job_id=f"metric-evaluation:{protocol_run_id}"
    )


async def dispose_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
    _pool = None
