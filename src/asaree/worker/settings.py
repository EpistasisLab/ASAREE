"""arq worker entrypoint: ``arq asaree.worker.settings.WorkerSettings``.

Startup mirrors app.py's lifespan (configure -> set_credential_resolver ->
ensure_system_servers -> hydrate_registry) for the same reason app.py's own
docstring gives:
motoro.mcp.registry.get_registry() is a per-process singleton, and the
worker is a different OS process from the API -- it starts with an empty
registry and has to hydrate its own.

job_timeout is set well above worker_job_timeout_seconds (the ceiling
execute_run_task enforces itself via asyncio.wait_for) so arq's own hard
per-job kill is a backstop that should not fire under normal operation --
execute_run_task's own timeout handling is what's meant to close out a run
that overruns its budget, cleanly, as FAILED rather than as an arq-cancelled
job with no run-row update at all.
"""

from __future__ import annotations

import logging
from typing import Any

from arq.connections import RedisSettings
from arq.cron import cron
from motoro.config import configure
from motoro.services.credentials import set_credential_resolver
from motoro.services.mcp_service import hydrate_registry

from asaree.config import get_settings
from asaree.models.database import dispose_engine
from asaree.redis_client import dispose_redis
from asaree.services.credential_resolver import resolve as resolve_credentials
from asaree.services.system_mcp_servers import ensure_system_servers, refresh_system_server_capabilities
from asaree.worker.tasks import check_stale_runs, execute_protocol_run_task, execute_run_task

logger = logging.getLogger(__name__)


async def on_startup(ctx: dict[str, Any]) -> None:
    configure(get_settings())
    set_credential_resolver(resolve_credentials)
    # Before hydrate_registry, same as app.py's lifespan and for the same
    # ordering reason -- and in this process too, not just the API's, because
    # this is the process that actually spawns those subprocesses for a run.
    # See asaree.services.system_mcp_servers.
    await ensure_system_servers()
    failed = await hydrate_registry()
    if failed:
        logger.warning("worker_mcp_servers_failed_to_reconnect", extra={"servers": failed})
    # After hydration -- it reads the live clients. See the API lifespan.
    await refresh_system_server_capabilities()


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()
    await dispose_redis()


class WorkerSettings:
    functions = [execute_run_task, execute_protocol_run_task]
    cron_jobs = [cron(check_stale_runs, second={0, 30})]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = on_startup
    on_shutdown = on_shutdown
    job_timeout = get_settings().worker_job_timeout_seconds * 2

    # Both task functions already treat their own failures as terminal: they
    # catch, force-fail the run row, and return, precisely so arq never gets a
    # chance to re-run a half-executed agent loop (real tool calls, real side
    # effects -- see each task's own boundary comment). A retry only happens
    # for something they cannot catch and record, i.e. a hard worker kill, and
    # re-running the graph from the top is the wrong answer there too. Making
    # that explicit rather than silently inheriting arq's default of 5.
    max_tries = 1

    # "Run all cells" enqueues one job per cell at once, so the queue depth is
    # user-controlled and the concurrency cap is the only thing bounding how
    # many agent loops (each with its own MCP subprocesses and LLM calls) run
    # in one worker process. arq's default is 10; keeping it explicit so the
    # limit is a decision rather than a default nobody chose.
    max_jobs = get_settings().worker_max_concurrent_jobs
