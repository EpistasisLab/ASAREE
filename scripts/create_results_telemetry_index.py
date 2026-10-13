"""Create the read-only telemetry adapter's core index without blocking writers.

Run once during deployment after core migrations. Uses configured application
settings; never prints credentials. This belongs to core's separate database,
so it deliberately is not an ASAREE Alembic migration.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from asaree.config import get_settings


async def main() -> None:
    engine = create_async_engine(get_settings().database_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_asaree_agent_run_attribution
                ON agent_runs(owner_id, (run_metadata ->> 'protocol_run_id'), id)
            """)
            )
        print("Operational telemetry index ready.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
