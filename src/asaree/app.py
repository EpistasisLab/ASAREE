"""ASAREE's FastAPI app.

Startup order matters: agentic-core must be configured before anything in it
runs (``configure()`` raises if a setting was read first), and
``hydrate_registry()`` needs that configuration in place to know which
database's persisted MCP servers to reconnect.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from agentic_core.config import configure
from agentic_core.services.credentials import set_credential_resolver
from agentic_core.services.mcp_service import hydrate_registry
from fastapi import FastAPI

from asaree.api.agents import router as agents_router
from asaree.api.datasets import router as datasets_router
from asaree.api.llm_settings import router as llm_settings_router
from asaree.api.mcp_servers import router as mcp_servers_router
from asaree.api.runs import router as runs_router
from asaree.api.users import router as users_router
from asaree.config import get_settings
from asaree.models.database import dispose_engine
from asaree.services.credential_resolver import resolve as resolve_credentials

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure(get_settings())
    set_credential_resolver(resolve_credentials)

    failed = await hydrate_registry()
    if failed:
        logger.warning("mcp_servers_failed_to_reconnect", extra={"servers": failed})

    yield

    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="ASAREE", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(users_router)
    app.include_router(datasets_router)
    app.include_router(mcp_servers_router)
    app.include_router(llm_settings_router)
    app.include_router(agents_router)
    app.include_router(runs_router)

    return app


app = create_app()
