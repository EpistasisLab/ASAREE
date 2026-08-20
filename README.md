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
docker compose up -d --build
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

## Resetting your dev environment

One Postgres server hosts two databases in a single volume — `motoro` (core's
schema) and `asaree` (this repo's). Wiping it wipes both at once: every user,
agent, experiment, dataset, MCP server registration, and LLM credential.

```bash
docker compose down -v
docker compose up -d --build
```

You're now at true zero. To get back to a working state:

1. Create a user and issue a token (see the SDK's
   [Auth bootstrap](sdk/README.md#auth-bootstrap)).
2. Re-run a use case notebook's early setup cells (experiment, dataset, agent
   creation, and the LLM credential cell if the account needs its own) — or, for
   the public myocardial-infarction use case, one command:
   [`publications/bioinformatics/README.md`](publications/bioinformatics/README.md).

No MCP server registration step: every bundled server — `asaree-workspace`,
`motoro-okf`, and the six domain servers (`asaree-sklearn-dc`, `-eda`, `-fs`,
`-fte`, `-model`, `-stats`, from `mcp-servers/`) — is an installed dependency of
the app itself, and auto-registers the next time the app or worker starts (see
`asaree.services.system_mcp_servers`).

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
