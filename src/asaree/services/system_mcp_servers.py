"""The MCP servers ASAREE ships and registers for itself, on every boot.

Both processes that spawn MCP subprocesses run :func:`ensure_system_servers`
before ``hydrate_registry`` and :func:`refresh_system_server_capabilities`
after it -- the API (``asaree.app``'s lifespan) and the run worker
(``asaree.worker.settings.on_startup``). It lives here rather than in
``app.py`` so the worker can call it without importing the FastAPI app and
every router with it.

Both, not just the API, because ``motoro.mcp.registry.get_registry()`` is a
per-process singleton and the worker is the process that actually spawns these
subprocesses for a run. If the worker only hydrated, a fresh deployment would
race: both containers start at once, the worker reads an empty
``mcp_server_configs`` before the API has written it, and every run until the
next worker restart would find no tools. Calling this in both is idempotent --
whichever process gets there first writes the rows, the other one finds them
already correct and just reads.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from motoro.mcp.registry import get_registry
from motoro.services.mcp_service import get_server_by_name, refresh_server, register_server, update_server

logger = logging.getLogger(__name__)

# ASAREE's own repo root: services/system_mcp_servers.py -> services ->
# asaree -> src -> root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

WORKSPACE_SERVER_NAME = "asaree-workspace"
OKF_SERVER_NAME = "motoro-okf"
SCIKIT_LEARN_SERVER_NAME = "scikit-learn-mcp"

# (server name, module to run). Every module here is importable from this
# repo's own venv -- asaree.* is ASAREE, motoro.* comes from the pinned Motoro
# dependency, and the asaree_sklearn_* packages are the mcp-servers/ path
# dependencies (see pyproject.toml). That uniformity is the point: because
# they're installed rather than checked out somewhere, `uv run --no-sync
# --directory <this repo>` reaches all of them, so no stored command embeds a path outside
# the deployment and a fresh `docker compose up` comes up with the full set
# connected and no manual registration step.
#
# The six asaree-sklearn-* servers are domain-general (cleaning, EDA, feature
# transformation, feature selection, modeling, statistics), which is why they
# ship with the product rather than with any one use case. They previously
# lived in asaree-spinal-use-case and were registered per-user by its
# register_servers.py, whose stored command was an absolute HOST path -- the
# reason compose.yml used to need a bind mount replaying that exact path.
SYSTEM_MCP_SERVERS: Final[tuple[tuple[str, str], ...]] = (
    (WORKSPACE_SERVER_NAME, "asaree.mcp_servers.workspace_server"),
    # Motoro's own bundled OKF server, not ASAREE's code. Registered
    # unconditionally, even if AGENTIC_OKF_BUNDLE_DIR is unset -- connecting
    # needs no bundle to exist yet; a tool call without one just returns a
    # clear {"error": ...} rather than failing registration.
    (OKF_SERVER_NAME, "motoro.mcp_servers.okf"),
    ("asaree-sklearn-dc", "asaree_sklearn_dc"),
    ("asaree-sklearn-eda", "asaree_sklearn_eda"),
    ("asaree-sklearn-fs", "asaree_sklearn_fs"),
    ("asaree-sklearn-fte", "asaree_sklearn_fte"),
    ("asaree-sklearn-model", "asaree_sklearn_model"),
    ("asaree-sklearn-stats", "asaree_sklearn_stats"),
    # Not part of the asaree-sklearn-* family despite the subject matter: it
    # imports nothing from this repo and takes its dataset as a path/URI
    # argument instead of reading the workspace layout, so it stands alone as a
    # publishable server. Registered here for the same reason as the rest --
    # this is the process that spawns it -- and it is the one server the canvas
    # currently offers, the six above being hidden from the picker.
    (SCIKIT_LEARN_SERVER_NAME, "scikit_learn_mcp"),
)


def command_for(module: str) -> str:
    """The stored ``command`` for a bundled server module.

    ``--no-sync`` is load-bearing, not an optimization. ``uv run`` syncs the
    project environment first by default, which means rebuilding ``asaree``
    itself from ``/app`` -- and since the version is derived from the git tag
    (hatch-vcs), that build needs a ``.git`` the image deliberately does not
    carry: it is bind-mounted for the final ``uv sync`` and never copied into a
    layer. Without this flag every one of these servers dies at spawn with
    "Error getting the version from source `vcs`", surfacing only as
    ``McpError: Connection closed``, and a deployment comes up with no tools at
    all. The venv these servers need is already fully installed by then, so
    there is nothing for the sync to do anyway -- it also stops each spawn from
    re-resolving (and downloading) the dev dependency group.
    """
    return f"uv run --no-sync --directory {_REPO_ROOT} python -m {module}"


async def _ensure_system_server(name: str, command: str) -> None:
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
    serving it, and makes a future rename of a module path self-healing instead
    of needing a migration to rewrite the column.

    That same API restriction is what makes overwriting safe for a row this
    function created: no user can have customized the command, so there is no
    intent here to clobber. ``update_server`` reconnects with the new settings,
    so the repaired server is live in this boot rather than the next one.

    One row it can repair but not fully adopt: a name registered by hand
    *before* it shipped with ASAREE (the six ``asaree-sklearn-*`` servers, which
    ``asaree-spinal-use-case``'s ``register_servers.py`` used to POST per-user).
    The command gets corrected, but ``update_server`` can't reassign
    ``owner_id``/``is_system``, so such a row stays owned by whoever registered
    it and therefore stays invisible to other users on that deployment. It's
    the original owner's to ``DELETE /mcp-servers/{id}`` if they want the
    global row instead — the next boot recreates it as a system server.

    **Must run before** ``hydrate_registry``, which is why both callers do.
    ``update_server`` reconnects by re-registering the name, and re-registering
    a name that is already live tears down its existing stdio client from
    whichever task happens to be running — not the one that opened it — which
    anyio rejects (``Attempted to exit cancel scope in a different task``) and
    which fails startup. Running while the registry is still empty means there
    is no prior client to tear down; ``hydrate_registry`` then skips these,
    since it leaves already-registered names alone.
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
        # Per server, not per boot: one server that won't connect (a missing
        # OKF bundle dir, a package that failed to install) must not take the
        # process down or block the servers after it in the list.
        logger.exception("system_mcp_server_registration_failed", extra={"server": name})


async def ensure_system_servers() -> None:
    """Register/repair every server in :data:`SYSTEM_MCP_SERVERS`."""
    for name, module in SYSTEM_MCP_SERVERS:
        await _ensure_system_server(name, command_for(module))


async def refresh_system_server_capabilities() -> None:
    """Re-persist ``capabilities`` for any system server whose tool set moved.

    **Must run after** ``hydrate_registry``, unlike everything above: it reads
    the live client, which only exists once the server is in the registry.

    The gap this closes: ``capabilities.tools`` is written exactly once, by
    ``register_server`` at first registration. ``hydrate_registry`` reconnects
    on every boot but never writes the discovered tools back, and
    :func:`_ensure_system_server` only reconciles when the stored *command*
    differs -- which it doesn't when a bundled server gains or loses a tool in
    a code change, since the command is ``python -m <module>`` either way. So
    the row keeps advertising the tool list from whenever the deployment first
    booted. Nothing at *run* time notices (an agent's tools come from the live
    registry), but the canvas reads this column: a new tool is missing from the
    MCP node's allow-list, and a removed one lingers as a checkbox for a tool
    that no longer exists.

    Scoped to :data:`SYSTEM_MCP_SERVERS` because those are the ones whose code
    ships with ASAREE and therefore changes underneath a live database. A
    user-registered server is theirs to ``POST /mcp-servers/{id}/refresh``.
    Compared before writing so the ordinary boot -- nothing changed -- costs one
    in-memory set comparison per server and no database round-trip at all.
    """
    registry = get_registry().servers
    for name, _ in SYSTEM_MCP_SERVERS:
        try:
            entry = registry.get(name)
            if entry is None or not entry.client.connected:
                continue
            config = await get_server_by_name(name)
            if config is None:
                continue
            live = sorted(t.name for t in entry.client.tools)
            stored = sorted(t.get("name", "") for t in (config.capabilities or {}).get("tools") or [])
            if live == stored:
                continue
            logger.warning(
                "system_mcp_server_capabilities_refreshed",
                extra={
                    "server": name,
                    "added": sorted(set(live) - set(stored)),
                    "removed": sorted(set(stored) - set(live)),
                },
            )
            await refresh_server(config.id)
        except Exception:
            # Per server, for the same reason _ensure_system_server swallows:
            # a stale tool list is a cosmetic problem and must not fail a boot.
            logger.exception("system_mcp_server_capability_refresh_failed", extra={"server": name})
