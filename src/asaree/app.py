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
from motoro.services.mcp_service import get_server_by_name, hydrate_registry, register_server, update_server

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


async def _ensure_system_server(name: str, command: str, log_key: str) -> None:
    """Register *name* as a global system server, or repair its stored command.

    System servers (``is_system=True``, ``owner_id=None``) are ASAREE's own, not
    something a researcher registers, and not one copy per owner — hence one row
    for the whole deployment rather than one per user.

    The reason this reconciles rather than returning early once the row exists:
    ``command`` embeds :data:`_REPO_ROOT`, which is the location of whichever
    process wrote the row — ``/app`` under compose, the checkout path when run
    on the host. That makes it *deployment* state, not a constant. A
    first-boot-only check strands any database later served from somewhere else:
    the stored path doesn't resolve here, the subprocess never starts, and the
    server drops out of the registry with nothing but a
    ``mcp_servers_failed_to_reconnect`` warning to show for it — and because
    ``PATCH``/``DELETE /mcp-servers/{id}`` both require
    ``owner_id == user.id``, a system server can't be repaired through the API
    either. Comparing on every boot keeps the row true to whoever is actually
    serving it, and makes a future rename of the module path self-healing
    instead of needing a migration to rewrite the column.

    That same API restriction is what makes overwriting safe: no user can have
    customized this command, so there is no intent here to clobber.
    ``update_server`` reconnects with the new settings, so the repaired server
    is live in this boot rather than the next one.

    **Must run before** ``hydrate_registry``, which is why ``lifespan`` calls it
    first. ``update_server`` reconnects by re-registering the name, and
    re-registering a name that is already live tears down its existing stdio
    client from whichever task happens to be running -- not the one that opened
    it -- which anyio rejects (``Attempted to exit cancel scope in a different
    task``) and which fails startup. Running while the registry is still empty
    means there is no prior client to tear down; ``hydrate_registry`` then skips
    these two, since it leaves already-registered names alone.
    """
    try:
        existing = await get_server_by_name(name)
        if existing is None:
            await register_server(name=name, transport="stdio", command=command, is_system=True)
        elif existing.command != command:
            logger.warning(
                "system_mcp_server_command_reconciled",
                extra={"server": name, "stored_command": existing.command, "command": command},
            )
            await update_server(existing.id, command=command)
    except Exception:
        logger.exception(log_key)


async def _ensure_workspace_server_registered() -> None:
    """Register ASAREE's own bundled workspace MCP server."""
    await _ensure_system_server(
        WORKSPACE_SERVER_NAME,
        f"uv run --directory {_REPO_ROOT} python -m asaree.mcp_servers.workspace_server",
        "asaree_workspace_server_registration_failed",
    )


async def _ensure_okf_server_registered() -> None:
    """Register core's own bundled OKF server.

    The same reasoning as the workspace server above, except this one is
    Motoro's own code, not ASAREE's (``motoro.mcp_servers.okf``), run via
    ``uv run --directory`` pointed at *this* repo so it uses ASAREE's own venv,
    which already depends on Motoro.

    Registered unconditionally, even if ``AGENTIC_OKF_BUNDLE_DIR`` is unset —
    connecting needs no bundle to exist yet; a tool call without one just
    returns a clear ``{"error": ...}`` rather than failing registration.
    """
    await _ensure_system_server(
        OKF_SERVER_NAME,
        f"uv run --directory {_REPO_ROOT} python -m motoro.mcp_servers.okf",
        "okf_server_registration_failed",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure(get_settings())
    set_credential_resolver(resolve_credentials)

    # Before hydrate_registry, deliberately: these two connect into a still-empty
    # registry, so neither has a live client to tear down first. Reversing the
    # order breaks startup outright -- see _ensure_system_server.
    await _ensure_workspace_server_registered()
    await _ensure_okf_server_registered()

    # Skips anything the two calls above already connected (it leaves names
    # already in the registry alone), so this only picks up everything else.
    failed = await hydrate_registry()
    if failed:
        logger.warning("mcp_servers_failed_to_reconnect", extra={"servers": failed})

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
