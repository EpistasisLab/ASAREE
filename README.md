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

## Get started

You need **git** and **Docker with Compose v2** (`docker compose version`),
about 10 GB of free disk, and 10–20 minutes for the first build.

**1. Clone and start it.**

```bash
git clone https://github.com/EpistasisLab/ASAREE.git
cd ASAREE
cp .env.example .env
docker compose up -d --build
```

That brings up Postgres, Redis, both migration steps, the API, the run worker,
and the frontend.

**2. Check it came up.**

```bash
docker compose ps       # the two migrate services read "Exited (0)" — that's success
curl localhost:8000/health
```

**3. Register.** Open <http://localhost:5173> and create an account.

**4. Add an LLM API key** for Anthropic, OpenAI, or Azure Foundry, under
**Profile → LLM credentials**.

To keep them safe, put your own `ASAREE_ENCRYPTION_KEY` in `.env` before you
save your first one — the sample value shipped in `.env.example` is public, and
rotating the key later means re-entering every stored credential.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose up -d    # picks up the changed .env
```

**5. Run an experiment.** Follow the worked myocardial-infarction use case in
[`publications/bioinformatics/README.md`](publications/bioinformatics/README.md)
— five agents in series building a classifier on a public dataset. It picks up
exactly where this step leaves off.

## Everyday commands

```bash
docker compose logs -f asaree-app     # or asaree-worker, asaree-frontend
docker compose up -d --build          # rebuild after pulling new code
docker compose restart asaree-app     # apply an edited .env
docker compose down                   # stop, keep all data
```

The frontend hot-reloads from your checkout; backend changes need a rebuild.

The stack binds ports 8000 (API), 5173 (frontend), 5453 (Postgres), and 6381
(Redis). If one is taken, set `POSTGRES_PORT` or `REDIS_PORT` in `.env`; the
first two are in `compose.yml`.

Every bundled MCP server — `asaree-workspace`, `motoro-okf`, and the six domain
servers (`asaree-sklearn-dc`, `-eda`, `-fs`, `-fte`, `-model`, `-stats`, from
`mcp-servers/`) — ships as a dependency of the app and registers itself each
time the app or worker starts (see `asaree.services.system_mcp_servers`).

## Resetting your dev environment

One Postgres server hosts two databases in a single volume — `motoro` (core's
schema) and `asaree` (this repo's). Wiping it wipes both at once: every user,
agent, experiment, dataset, MCP server registration, and LLM credential.

```bash
docker compose down -v
docker compose up -d --build
```

You're now at true zero. To get back to a working state:

1. Register a user in the GUI again (and, for SDK/notebook work, issue a token —
   see the SDK's [Auth bootstrap](sdk/README.md#auth-bootstrap)).
2. Re-add the LLM credential, then re-run a use case notebook's early setup
   cells (experiment, dataset, agent creation) — or, for the public
   myocardial-infarction use case, follow its walkthrough:
   [`publications/bioinformatics/README.md`](publications/bioinformatics/README.md).
