# ASAREE

**A**nalytical **S**andbox for **A**gentic **R**esearch, **E**ngineering, and
**E**xperimentation — a workbench for running LLM agents as designed
experiments rather than one-off prompts.

You build a pipeline of agents on a visual protocol canvas, declare the factors
you want to vary across it (model, effort, whether a critic gate is enabled,
anything else bound to a node's config), and ASAREE materializes the full
factorial design as cells, runs them, and collects each cell's metrics so the
comparison is a measured result instead of an impression. Datasets are
registered, split, and versioned as they pass between agents, and the tools the
agents reach for are MCP servers, so a run is reproducible end to end.

ASAREE is built on top of [Motoro](https://github.com/EpistasisLab/motoro),
which provides the agent runtime, execution patterns, LLM service, and MCP
integration. Motoro ships no HTTP layer, no auth, and no UI; ASAREE adds those,
plus the experiment/protocol/dataset model, and depends on Motoro as a pinned
library — in-process, not a service call.

## Quick start

```bash
cp .env.example .env    # fill in provider credentials
docker compose up -d --build
```

Then open the frontend on `:5173` (API on `:8000`) and register an account.

That one command also brings up Postgres, Redis, and both migration steps:
`compose.yml` `include`s Motoro's own compose file, so core's schema is applied
by `motoro-migrate` and ASAREE's by `asaree-migrate` before `asaree-app` starts.
It assumes a sibling checkout at `../Motoro`; set `MOTORO_DIR` in `.env` if
yours lives elsewhere.

For a complete worked example, see the public myocardial-infarction use case:
[`publications/bioinformatics2026/README.md`](publications/bioinformatics2026/README.md).

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
   the public myocardial-infarction use case, follow its walkthrough:
   [`publications/bioinformatics2026/README.md`](publications/bioinformatics2026/README.md).

No MCP server registration step: every bundled server — `asaree-workspace`,
`motoro-okf`, and the six domain servers (`asaree-sklearn-dc`, `-eda`, `-fs`,
`-fte`, `-model`, `-stats`, from `mcp-servers/`) — is an installed dependency of
the app itself, and auto-registers the next time the app or worker starts (see
`asaree.services.system_mcp_servers`).
