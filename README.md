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
