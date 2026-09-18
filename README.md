# ASAREE

[![Latest release](https://img.shields.io/github/v/release/EpistasisLab/ASAREE?display_name=tag&sort=semver)](https://github.com/EpistasisLab/ASAREE/releases/latest)
[![CI](https://github.com/EpistasisLab/ASAREE/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/EpistasisLab/ASAREE/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.12-3776AB?logo=python&logoColor=white)](https://github.com/EpistasisLab/ASAREE/blob/main/pyproject.toml)
[![License](https://img.shields.io/github/license/EpistasisLab/ASAREE)](https://github.com/EpistasisLab/ASAREE/blob/main/LICENSE)

**A**nalytical **S**andbox for **A**gentic **R**esearch, **E**ngineering, and
**E**xperimentation — a workbench for running LLM agents as designed
experiments rather than one-off prompts.

You build a pipeline of agents on a visual protocol canvas, declare the factors
you want to vary across it (model, effort, whether a critic gate is enabled,
anything else bound to a node's config), and ASAREE materializes the full
factorial design as cells, runs every replicate, and collects each replicate's
metrics so the comparison is a measured result instead of an impression.
Datasets are registered, split, and versioned as they pass between agents, and
the tools the agents reach for are MCP servers, so a run is reproducible end
to end.

ASAREE is built on top of [Motoro](https://github.com/EpistasisLab/motoro),
which provides the agent runtime, execution patterns, LLM service, and MCP
integration. Motoro ships no HTTP layer, no auth, and no UI; ASAREE adds those,
plus the experiment/protocol/dataset model, and depends on Motoro as a pinned
library — in-process, not a service call.

## Releases and development

For installation, reproducible research, and production deployments, use a
tagged [GitHub Release](https://github.com/EpistasisLab/ASAREE/releases). The
[latest release](https://github.com/EpistasisLab/ASAREE/releases/latest) is the
recommended version for new installations. The project treats every published
release tag as permanent so a deployment or experiment can be recreated from
the same source later. Repository administrators enforce this policy with
GitHub's release immutability setting; ordinary Git tags are not inherently
immutable.

The `main` branch contains the latest development version. It may include
changes that have not yet been released or fully validated for production, so
do not use `main` when an exact, stable version matters.

## Get started

You need **git** and **Docker with Compose v2** (`docker compose version`),
about 10 GB of free disk, and 10–20 minutes for the first build.

**1. Install a release and start it.** Open the
[releases page](https://github.com/EpistasisLab/ASAREE/releases), choose a tag,
and replace `vX.Y.Z` below with that tag (for example, `v0.3.0`).

```bash
git clone --branch vX.Y.Z --depth 1 https://github.com/EpistasisLab/ASAREE.git
cd ASAREE
cp .env.example .env
docker compose up -d --build
```

Contributors who intentionally want the current development version can clone
`main` instead:

```bash
git clone --branch main https://github.com/EpistasisLab/ASAREE.git
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

## Measurements and Results

The Design panel offers built-in runtime and supervised-ML metrics with their
producer, required inputs, units, aggregation, direction, and current readiness.
Custom metrics can read a typed numeric/Boolean structured-output field or use a
model judge with an explicit model, rubric, bounds, and selected run inputs. A
judge is an extra model call on every replicate, so its recurring call and cost
estimate is shown before save. The optional wizard creates the same typed draft
as the manual editor and cannot bypass the normal validator.

Every completed attempt records one state for every declared metric:
`measured`, `unavailable` (the required value was absent), `failed` (the producer
could not evaluate it), or `not_applicable` (the metric does not apply to that
task). Results keeps those states distinct. Confusion matrices, calibration
data, and per-class reports are evaluation artifacts: they appear in attempt
detail but are never ranked or averaged as scalar metrics.

The Results CSV exports scalar values as ordinary analysis columns. Its
`observation_statuses` JSON column retains each metric's state and error, and
`evaluation_artifacts` retains structured diagnostics without flattening them
into misleading scalar columns. The `legacy_values` JSON column keeps old text
or other non-rankable values with `legacy.unknown` provenance when their
original producer was never recorded; choose an explicit producer before
running that legacy declaration again.

## Everyday commands

```bash
docker compose logs -f asaree-app     # or asaree-worker, asaree-frontend
docker compose up -d --build          # rebuild after pulling new code
docker compose restart asaree-app     # apply an edited .env
docker compose down                   # stop, keep all data
```

Run the backend test suite in Compose when you do not already have the dev
database exposed to the host. The one-shot runner waits for both migration
chains and connects to Postgres over the internal Compose network, so it does
not depend on `localhost`, `POSTGRES_PORT`, or a host PostgreSQL installation:

```bash
docker compose run --rm --build asaree-tests
```

The tests themselves still run under pytest; only pytest and its real Postgres
dependency are placed on the same network. The `test` profile keeps this
service out of a normal `docker compose up`.

The frontend hot-reloads from your checkout; backend changes need a rebuild.

`docker compose up -d --build` also runs pending database migrations. The
one-shot `motoro-migrate` service upgrades Motoro's `motoro` database first;
`asaree-migrate` then upgrades ASAREE's `asaree` database. The API and worker
start only after both migration services exit successfully.
`docker compose restart asaree-app` restarts only that service and does not
rerun the migration services.

Before upgrading an existing production deployment, back up both databases.
Then check out the desired release tag and recreate the stack:

```bash
git fetch --tags
git switch --detach vX.Y.Z
docker compose up -d --build
docker compose ps
```

To run the migrations explicitly against an external PostgreSQL server, build
the release's migration image, supply URLs for both databases, and run the two
chains in order. URL-encode any special characters in the credentials.

```bash
docker compose build motoro-migrate asaree-migrate

MOTORO_DB_URL='postgresql+asyncpg://USER:PASSWORD@HOST:PORT/motoro'
ASAREE_DB_URL='postgresql+asyncpg://USER:PASSWORD@HOST:PORT/asaree'

docker compose run --rm --no-deps motoro-migrate deploy --url "$MOTORO_DB_URL"
docker compose run --rm --no-deps asaree-migrate upgrade --url "$ASAREE_DB_URL"

docker compose run --rm --no-deps motoro-migrate current --url "$MOTORO_DB_URL"
docker compose run --rm --no-deps asaree-migrate current --url "$ASAREE_DB_URL"
```

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
