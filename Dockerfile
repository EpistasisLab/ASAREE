# ASAREE's application image.
#
# uv is used to install (it's the only thing that understands
# [tool.uv.sources] -- agentic-core is a pinned git dependency and
# asaree-workspace-core an editable local path, neither of which a bare
# `pip install .` can resolve), but the app itself is launched with the
# venv's own uvicorn directly -- not `uv run` -- so a running container never
# depends on uv being invoked per request.
#
# uv stays in the final image deliberately (unlike a slimmed multi-stage
# build): app.py spawns its bundled MCP servers (asaree-workspace,
# agentic-core-okf) via `uv run --directory <repo root> python -m ...`, the
# same subprocess convention used in every dev environment so far. Removing
# uv here would just move the same "no uv at runtime" problem one level down.
FROM python:3.13-slim

WORKDIR /app

# git: needed by uv to resolve agentic-core's pinned git dependency below.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

# README.md is not documentation here: pyproject declares `readme =
# "README.md"`, and hatchling refuses to build the wheel without it.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
# Editable local path dependency (asaree-workspace-core) -- must be present
# before `uv sync`, not copied in after.
COPY workspace-core/ ./workspace-core/

# agentic-core is a private repo (pinned git dependency, see pyproject.toml)
# -- uv needs a credential to fetch it that the host's `gh`-backed git
# credential helper doesn't carry into an isolated build. Passed as a
# BuildKit secret (compose.yml's `secrets:`), injected via one-shot
# git config env vars so the token touches only this RUN's environment --
# never a file, never a committed layer.
RUN --mount=type=secret,id=gh_token \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="url.https://oauth2:$(cat /run/secrets/gh_token)@github.com/.insteadOf" \
    GIT_CONFIG_VALUE_0="https://github.com/" \
    uv sync --frozen --no-dev

# Absent on purpose: the repo's .env. AsareeSettings reads host-side URLs
# (localhost:5432) that are wrong inside a compose network; real values
# arrive as environment variables at `docker run`/`docker compose` time
# instead. .dockerignore keeps .env out of the build context so it can't be
# copied in by accident.

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "asaree.app:app", "--host", "0.0.0.0", "--port", "8000"]
