# ASAREE

The product built on [`motoro`](https://github.com/EpistasisLab/motoro). Core
ships no HTTP layer, no auth, no UI — ASAREE provides those, and depends on core
as a pinned library dependency (in-process, not a service call).

- [`vision.md`](vision.md) — what ASAREE is meant to be, and at the bottom, how
  that compares to what's actually built so far.
- `project_plan/core_asaree_use_case.md` in the ARES repo — the design record:
  repo topology, what's already decided, and why.

## Quick start

```bash
cp .env.example .env    # fill in provider credentials as usual
GH_TOKEN=$(gh auth token) docker compose up -d --build
```

API on `:8000`, frontend on `:5173`.

`compose.yml` `include`s Motoro's own compose file, so that one command also
brings up `motoro-postgres`/`motoro-redis`/`motoro-migrate`, and both schemas are
applied before `asaree-app` starts — core's by `motoro-migrate`, ASAREE's by
`asaree-migrate`.

- Assumes a sibling checkout at `../Motoro`; set `MOTORO_DIR` if yours is
  elsewhere.
- Don't also run `docker compose up` inside that checkout while this is up — same
  `container_name`s and ports, so the two collide.
- `GH_TOKEN` is only needed for `--build`, and only while Motoro is private. It
  stays harmless once that repo is public, so the command above always works.

## Resetting your dev environment

One Postgres server hosts two databases in a single volume — `motoro` (core's
schema) and `asaree` (this repo's). Wiping it wipes both at once: every user,
agent, experiment, dataset, MCP server registration, and LLM credential.

```bash
docker compose down -v
GH_TOKEN=$(gh auth token) docker compose up -d --build
```

You're now at true zero. To get back to a working state:

1. Create a user and issue a token (see the SDK's
   [Auth bootstrap](sdk/README.md#auth-bootstrap)).
2. Register any use-case-specific MCP servers, e.g. from
   `asaree-spinal-use-case`: `uv run python register_servers.py`.
3. Re-run a use case notebook's early setup cells (experiment, dataset, agent
   creation, and the LLM credential cell if the account needs its own).

ASAREE's own bundled servers (`asaree-workspace`, `motoro-okf`) need no manual
step — they auto-register the next time the app starts (`app.py`'s lifespan).

### If you run Motoro's compose standalone

Host-based dev flow: reset from the Motoro repo, then migrate ASAREE yourself,
because core's compose only applies core's schema.

```bash
cd path/to/Motoro
docker compose down -v          # destroys both databases + redis
docker compose up -d            # fresh containers, core's schema applied

cd path/to/ASAREE
uv run python -m asaree.migrations upgrade
```

## Gotcha: stale MCP server paths

`asaree-workspace`/`motoro-okf`'s persisted `command` column holds whichever
filesystem path registered them first, so a row registered from the host
(`uv run --directory /path/on/host ...`) can't reconnect inside the container —
that path doesn't exist there. Delete both rows from `mcp_server_configs` and
restart to re-register with the container's own path.
