# ASAREE

The product built on [`agentic-core`](https://github.com/jay-m-dev/agentic-core).
Core ships no HTTP layer, no auth, no UI — ASAREE provides those, and depends
on core as a pinned library dependency (in-process, not a service call).

See `project_plan/core_asaree_use_case.md` in the ARES repo for the design
record: repo topology, what's already decided, and why.

See [`vision.md`](vision.md) for what ASAREE is meant to be — and, at the
bottom, how that compares to what's actually built so far.

## Resetting your dev environment

ASAREE has no database of its own to reset in isolation. `docker compose up -d`
in the `agentic-core` repo (where `compose.yml` lives) brings up a single
Postgres server that hosts *two* databases side by side — `agentic_core`
(core's own schema) and `asaree` (this repo's) — both in the same named
volume. Wiping it wipes both at once: every user, agent, experiment, dataset,
MCP server registration, and LLM credential.

```bash
# from the agentic-core repo
cd path/to/agentic-core
docker compose down -v          # destroys both databases + redis
docker compose up -d            # fresh containers; core's own schema is
                                 # applied automatically (agentic-core-migrate)

# core's compose only applies core's schema -- ASAREE's own still needs migrating
cd path/to/ASAREE
uv run python -m asaree.migrations upgrade
```

You're now at true zero: no users, nothing registered. To get back to a
working state:

1. Create a user and issue a token (see the SDK's [Auth bootstrap](sdk/README.md#auth-bootstrap)).
2. Register any use-case-specific MCP servers, e.g. from `asaree-spinal-use-case`:
   `uv run python register_servers.py`.
3. Re-run a use case notebook's early setup cells (experiment, dataset, agent
   creation, and the LLM credential cell if the account needs its own).

ASAREE's own bundled servers (`asaree-workspace`, `agentic-core-okf`) don't need
a manual step — they auto-register the next time the app starts (`app.py`'s
lifespan), the same as they did the very first time.

## Running with Docker

Requires agentic-core's stack already running (`docker compose up -d` in the
`agentic-core` repo) — this only joins its network by name, it doesn't start
Postgres/Redis itself. `agentic-core` is a private git dependency
([`pyproject.toml`](pyproject.toml)), so building needs a GitHub token with
read access to it, passed as a build secret rather than baked into the image:

```bash
cp .env.example .env    # fill in provider credentials as usual
GH_TOKEN=$(gh auth token) docker compose up -d --build
```

This runs `asaree-migrate` (applies ASAREE's own schema, creating the
database first if needed — see `src/asaree/migrations`) to completion, then
starts `asaree-app` on `:8000`. Both join `agentic-core_default` (the network
`agentic-core`'s own `compose.yml` creates) to reach Postgres/Redis by
container name — the host-side URLs in `.env` (`localhost:...`) are
overridden inside `docker-compose.yml` for exactly this reason, the same
pattern as `agentic-core`'s own `docker/Dockerfile.migrate`.

One gotcha if you've also run ASAREE directly on the host against the same
Postgres instance: `asaree-workspace`/`agentic-core-okf`'s persisted
`command` column holds whichever filesystem path registered them first. A
row registered from the host (`uv run --directory /path/on/host ...`) fails
to reconnect inside the container (that path doesn't exist there) — delete
the two rows from `mcp_server_configs` and restart to have them
re-register fresh with the container's own path.
