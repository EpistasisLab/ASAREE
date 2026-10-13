"""Dedicated result maintenance/command worker, isolated from LLM execution slots.

arq asaree.worker.result_settings.ResultWorkerSettings
"""

from typing import Any

from arq.connections import RedisSettings
from arq.cron import cron
from motoro.config import configure

from asaree.config import get_settings
from asaree.services.result_exports import execute_result_export, relay_exports
from asaree.services.result_jobs import drain_result_commands
from asaree.services.result_rollups import refresh_result_summaries
from asaree.services.result_usage import capture_result_usage
from asaree.services.row_schema_reads import prepare_row_schemas
from asaree.worker.settings import on_shutdown


async def startup(_ctx: dict[str, Any]) -> None:
    configure(get_settings())


class ResultWorkerSettings:
    functions = [execute_result_export]
    cron_jobs = [
        cron(prepare_row_schemas, second=set(range(60))),
        cron(drain_result_commands, second=set(range(60))),
        cron(capture_result_usage, second=set(range(0, 60, 5))),
        cron(refresh_result_summaries, second=set(range(0, 60, 5))),
        cron(relay_exports, second=set(range(0, 60, 5))),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = "arq:results"
    on_startup = startup
    on_shutdown = on_shutdown
    max_jobs = 6
    max_tries = 1
    job_timeout = 3600
    keep_result = 0
