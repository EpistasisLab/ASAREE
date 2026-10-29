"""Execute a statistics snapshot without blocking the API or worker event loop."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update

from asaree.models.database import get_session
from asaree.models.stats_analysis import StatsAnalysis


async def check_stale_stats(ctx: dict) -> None:
    """Close snapshots left behind by expired queues or a killed worker."""
    now = datetime.now(UTC)
    async with get_session() as db:
        await db.execute(
            update(StatsAnalysis)
            .where(
                or_(
                    (StatsAnalysis.status == "pending") & (StatsAnalysis.updated_at < now - timedelta(minutes=65)),
                    (StatsAnalysis.status == "running") & (StatsAnalysis.updated_at < now - timedelta(minutes=35)),
                )
            )
            .values(status="failed", error="Worker interrupted or queue expired; rerun the saved configuration.")
        )


async def execute_stats_task(ctx: dict, analysis_id: str) -> None:
    async with get_session() as db:
        analysis = await db.get(StatsAnalysis, uuid.UUID(analysis_id), with_for_update=True)
        if analysis is None or analysis.status != "pending":
            return
        analysis.status = "running"
        request = dict(analysis.request)
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "asaree.services.stats_runner",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), timeout=1800)
        if process.returncode:
            raise ValueError(stderr.decode(errors="replace")[-3000:])
        output = json.loads(stdout)
        async with get_session() as db:
            analysis = await db.get(StatsAnalysis, uuid.UUID(analysis_id))
            if analysis is not None:
                analysis.output, analysis.status = output, "completed"
    except (Exception, asyncio.CancelledError) as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
        async with get_session() as db:
            analysis = await db.get(StatsAnalysis, uuid.UUID(analysis_id))
            if analysis is not None:
                analysis.status = "failed"
                analysis.error = (
                    "Analysis exceeded 30 minutes."
                    if isinstance(exc, TimeoutError)
                    else str(exc)[-3000:] or "Worker interrupted; rerun the analysis."
                )
        if isinstance(exc, asyncio.CancelledError):
            raise
