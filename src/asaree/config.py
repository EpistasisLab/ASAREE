"""ASAREE's settings — and the one instance that also configures Motoro.

Subclasses :class:`CoreSettings` per its own documented contract (see
``examples/settings.py`` in Motoro): pick a prefix, add product fields,
call :func:`motoro.config.configure` before anything reads a setting.

Two databases, one Postgres server, deliberately not the same field:
``database_url`` (inherited from ``CoreSettings``) is core's own connection —
Motoro's engine reads it for its own schema, and ASAREE's code never
touches it directly. ``product_database_url`` is ASAREE's own tables, on the
same server, in a separate database (``asaree``, alongside core's
``motoro``) so nothing shares a schema and nothing needs a
cross-database join.
"""

from __future__ import annotations

from motoro import CoreSettings
from pydantic_settings import SettingsConfigDict


class AsareeSettings(CoreSettings):
    """ASAREE's settings. Installed via ``motoro.config.configure()``."""

    model_config = SettingsConfigDict(env_prefix="ASAREE_", env_file=".env", extra="ignore")

    # ASAREE's own product database — distinct from the inherited
    # `database_url`, which this same instance also carries and which
    # Motoro reads for its own schema.
    product_database_url: str = "postgresql+asyncpg://agentic:agentic@localhost:5453/asaree"

    # Minimal auth. A real secret is required outside development — no
    # fallback silently accepted in production, see get_settings(). Also the
    # JWT signing secret for browser sessions (services/auth_service.py) —
    # one secret, not a second field, since both are "the thing that must
    # never leak or a caller can forge identity."
    auth_secret_key: str = "dev-only-not-for-production"
    access_token_expiry_seconds: int = 3600  # 1 hour
    refresh_token_expiry_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # The frontend's own origin(s), comma-separated (same convention as
    # Motoro's mcp_allowed_env_vars — a plain str, split at the point of
    # use) — no wildcard: the refresh cookie needs allow_credentials, which
    # browsers refuse to combine with "*". Defaults to the Vite dev server.
    cors_allowed_origins: str = "http://localhost:5173"

    # Local disk, matching ARES's current approach — see
    # project_plan/core_asaree_use_case.md §9: metadata (this database) and
    # hashes are what matter; where the bytes physically live is a separate,
    # deliberately deferred decision.
    dataset_storage_dir: str = "./data/datasets"

    # The one directory a user may browse and register OKF bundles under
    # (GET /okf/browse, POST /okf/bundles -- services/okf_bundles.py). Every
    # path is jailed inside this root, so it is the whole reach of that API
    # and of the per-bundle MCP servers it spawns.
    #
    # "~" (the home directory of whoever runs the server process) is the
    # default because the case this exists for is a researcher running ASAREE
    # on their own machine: there, the server's filesystem IS their
    # filesystem, so their bundle is already reachable and typing a path is
    # enough. Narrow it on any deployment where that isn't true -- under
    # compose the API/worker containers see only their own mounts, so this
    # points at the bundle mount instead (see compose.yml). A bundle on a
    # laptop with the server somewhere else is out of reach either way, and
    # deliberately so: nothing here tunnels to a client machine.
    okf_bundle_root: str = "~"

    # Where UPLOADED single-concept OKF documents are stored (POST
    # /okf/documents -- services/okf_documents.py). Deliberately NOT inside
    # okf_bundle_root: that root is the jail for paths a user *picks*, and
    # these are storage ASAREE owns and creates on the user's behalf, the same
    # way dataset_storage_dir is. Keeping them apart also means an uploaded
    # document never shows up as a stray folder in the bundle browser.
    okf_document_dir: str = "./data/okf-documents"

    # Encrypts per-user LLM provider API keys at rest (user_llm_settings).
    # Deliberately ASAREE's own — core's services.encryption is explicitly a
    # single server-side secret with no user in the picture at all, the
    # opposite of what per-user credential storage needs.
    encryption_key: str = "dev-only-not-for-production"

    # Ceiling on how long the worker lets a single run's execute_run() call
    # run before it force-fails it (asaree.worker.tasks.execute_run_task) —
    # the fallback when the run's own agent has no max_run_duration_seconds
    # set. Generous by design: this is a backstop, not the primary control:
    # 24h to comfortably clear the spinal-fusion notebook's own AGENT_MAX_
    # DURATION. An agent's max_run_duration_seconds should stay under this,
    # since the arq worker's own job_timeout is set from this same value.
    worker_job_timeout_seconds: int = 60 * 60 * 24

    # How long a RUNNING run may go without a heartbeat (AgentRun.
    # last_heartbeat_at, written every phase of every iteration by the
    # orchestrator) before asaree.worker.tasks.check_stale_runs treats its
    # worker as dead and force-fails it. Generous relative to the heartbeat
    # cadence (per-phase, so at most a few LLM calls apart) — a slow provider
    # response should never trip this on a run that is actually still alive.
    run_heartbeat_stale_seconds: int = 300


_instance: AsareeSettings | None = None


def get_settings() -> AsareeSettings:
    """Return the process-wide settings, constructing them on first use."""
    global _instance
    if _instance is None:
        _instance = AsareeSettings()
    return _instance
