"""Requeue rebuildable result summaries without rewriting measurement evidence.

Run in the deployed application environment: python scripts/rebuild_result_summaries.py
The results worker performs the bounded backfill and rebuild asynchronously.
"""

import asyncio

from sqlalchemy import update

from asaree.models.database import get_session
from asaree.models.result_read_model import ResultRefresh


async def main() -> None:
    async with get_session() as db:
        await db.execute(update(ResultRefresh).values(generation=ResultRefresh.generation + 1))


if __name__ == "__main__":
    asyncio.run(main())
