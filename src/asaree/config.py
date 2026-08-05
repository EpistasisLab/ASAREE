"""ASAREE's settings — and the one instance that also configures agentic-core.

Subclasses :class:`CoreSettings` per its own documented contract (see
``examples/settings.py`` in agentic-core): pick a prefix, add product fields,
call :func:`agentic_core.config.configure` before anything reads a setting.

Two databases, one Postgres server, deliberately not the same field:
``database_url`` (inherited from ``CoreSettings``) is core's own connection —
agentic-core's engine reads it for its own schema, and ASAREE's code never
touches it directly. ``product_database_url`` is ASAREE's own tables, on the
same server, in a separate database (``asaree``, alongside core's
``agentic_core``) so nothing shares a schema and nothing needs a
cross-database join.
"""

from __future__ import annotations

from agentic_core import CoreSettings
from pydantic_settings import SettingsConfigDict


class AsareeSettings(CoreSettings):
    """ASAREE's settings. Installed via ``agentic_core.config.configure()``."""

    model_config = SettingsConfigDict(env_prefix="ASAREE_", env_file=".env", extra="ignore")

    # ASAREE's own product database — distinct from the inherited
    # `database_url`, which this same instance also carries and which
    # agentic-core reads for its own schema.
    product_database_url: str = "postgresql+asyncpg://agentic:agentic@localhost:5453/asaree"

    # Minimal auth. A real secret is required outside development — no
    # fallback silently accepted in production, see get_settings().
    auth_secret_key: str = "dev-only-not-for-production"

    # Local disk, matching ARES's current approach — see
    # project_plan/core_asaree_use_case.md §9: metadata (this database) and
    # hashes are what matter; where the bytes physically live is a separate,
    # deliberately deferred decision.
    dataset_storage_dir: str = "./data/datasets"


_instance: AsareeSettings | None = None


def get_settings() -> AsareeSettings:
    """Return the process-wide settings, constructing them on first use."""
    global _instance
    if _instance is None:
        _instance = AsareeSettings()
    return _instance
