"""ASAREE's FastAPI app.

Startup order matters: Motoro must be configured before anything in it
runs (``configure()`` raises if a setting was read first), and
``hydrate_registry()`` needs that configuration in place to know which
database's persisted MCP servers to reconnect.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motoro.config import configure
from motoro.services.credentials import set_credential_resolver
from motoro.services.mcp_service import hydrate_registry

from asaree.api.agents import router as agents_router
from asaree.api.auth import router as auth_router
from asaree.api.datasets import router as datasets_router
from asaree.api.experiments import router as experiments_router
from asaree.api.llm_settings import router as llm_settings_router
from asaree.api.mcp_servers import router as mcp_servers_router
from asaree.api.okf import router as okf_router
from asaree.api.protocols import router as protocols_router
from asaree.api.runs import router as runs_router
from asaree.api.skills import router as skills_router
from asaree.api.users import router as users_router
from asaree.config import get_settings
from asaree.models.database import dispose_engine
from asaree.redis_client import dispose_redis
from asaree.services.credential_resolver import resolve as resolve_credentials
from asaree.services.system_mcp_servers import ensure_system_servers, refresh_system_server_capabilities

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure(get_settings())
    set_credential_resolver(resolve_credentials)

    # Before hydrate_registry, deliberately: these connect into a still-empty
    # registry, so none has a live client to tear down first. Reversing the
    # order breaks startup outright -- see _ensure_system_server.
    await ensure_system_servers()

    # Skips anything the call above already connected (it leaves names already
    # in the registry alone), so this only picks up everything else -- servers
    # a user registered themselves.
    failed = await hydrate_registry()
    if failed:
        logger.warning("mcp_servers_failed_to_reconnect", extra={"servers": failed})

    # After hydration, not before: this one reads the live clients. Keeps the
    # tool list the canvas shows in step with the code that shipped.
    await refresh_system_server_capabilities()

    yield

    await dispose_engine()
    await dispose_redis()


def create_app() -> FastAPI:
    app = FastAPI(title="ASAREE", lifespan=lifespan)

    # The frontend (a separate origin in dev — the Vite dev server; a
    # separate origin in prod too, unless it's served from behind the same
    # reverse proxy) needs cookies (the refresh token) to actually be sent
    # and read cross-origin, hence allow_credentials + an explicit origin
    # list rather than "*" (browsers refuse credentialed requests with a
    # wildcard origin anyway).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in get_settings().cors_allowed_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router, prefix="/api")
    app.include_router(users_router, prefix="/api")
    app.include_router(datasets_router, prefix="/api")
    app.include_router(mcp_servers_router, prefix="/api")
    app.include_router(skills_router, prefix="/api")
    app.include_router(okf_router, prefix="/api")
    app.include_router(llm_settings_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(experiments_router, prefix="/api")
    app.include_router(protocols_router, prefix="/api")

    return app


app = create_app()
