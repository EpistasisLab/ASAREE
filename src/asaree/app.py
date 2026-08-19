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
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motoro.config import configure
from motoro.services.credentials import set_credential_resolver
from motoro.services.mcp_service import get_server_by_name, hydrate_registry, register_server

from asaree.api.agents import router as agents_router
from asaree.api.auth import router as auth_router
from asaree.api.datasets import router as datasets_router
from asaree.api.experiments import router as experiments_router
from asaree.api.llm_settings import router as llm_settings_router
from asaree.api.mcp_servers import router as mcp_servers_router
from asaree.api.protocols import router as protocols_router
from asaree.api.runs import router as runs_router
from asaree.api.users import router as users_router
from asaree.config import get_settings
from asaree.models.database import dispose_engine
from asaree.redis_client import dispose_redis
from asaree.services.credential_resolver import resolve as resolve_credentials

logger = logging.getLogger(__name__)

# ASAREE's own repo root, from src/asaree/app.py -> src/asaree -> src -> root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE_SERVER_NAME = "asaree-workspace"
OKF_SERVER_NAME = "motoro-okf"


async def _ensure_workspace_server_registered() -> None:
    """Register ASAREE's own bundled workspace MCP server, once, as a global
    system server (``is_system=True``, ``owner_id=None``) — not something a
    researcher registers, and not one copy per owner. On every later boot,
    ``hydrate_registry`` (called first) already reconnects the persisted row,
    so this is a no-op after the first successful registration.
    """
    if await get_server_by_name(WORKSPACE_SERVER_NAME) is not None:
        return
    command = f"uv run --directory {_REPO_ROOT} python -m asaree.mcp_servers.workspace_server"
    try:
        await register_server(name=WORKSPACE_SERVER_NAME, transport="stdio", command=command, is_system=True)
    except Exception:
        logger.exception("asaree_workspace_server_registration_failed")


async def _ensure_okf_server_registered() -> None:
    """Register core's own bundled OKF server, once, as a global system
    server — the same reasoning as the workspace server above, except this
    one is Motoro's own code, not ASAREE's (``motoro.mcp_servers.
    okf``), run via ``uv run --directory`` pointed at *this* repo so it uses
    ASAREE's own venv, which already depends on Motoro.

    Registered unconditionally, even if ``AGENTIC_OKF_BUNDLE_DIR`` is unset —
    connecting needs no bundle to exist yet; a tool call without one just
    returns a clear ``{"error": ...}`` rather than failing registration.
    """
    if await get_server_by_name(OKF_SERVER_NAME) is not None:
        return
    command = f"uv run --directory {_REPO_ROOT} python -m motoro.mcp_servers.okf"
    try:
        await register_server(name=OKF_SERVER_NAME, transport="stdio", command=command, is_system=True)
    except Exception:
        logger.exception("okf_server_registration_failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure(get_settings())
    set_credential_resolver(resolve_credentials)

    failed = await hydrate_registry()
    if failed:
        logger.warning("mcp_servers_failed_to_reconnect", extra={"servers": failed})

    await _ensure_workspace_server_registered()
    await _ensure_okf_server_registered()

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
    app.include_router(llm_settings_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(experiments_router, prefix="/api")
    app.include_router(protocols_router, prefix="/api")

    return app


app = create_app()
