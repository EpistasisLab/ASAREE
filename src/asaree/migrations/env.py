"""Alembic environment for ASAREE's own migration chain.

Uses the plain Alembic default version table (``alembic_version``) since this
chain owns its database outright — no second chain to collide with, unlike
Motoro's ``alembic_version_motoro``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing for the metadata side effect — a model not imported here is a
# table this chain cannot see. Add each new model module here as it's added.
import asaree.models.audit_log_entry  # noqa: F401
import asaree.models.dataset  # noqa: F401
import asaree.models.dataset_workspace_event  # noqa: F401
import asaree.models.experiment  # noqa: F401
import asaree.models.experiment_artifact  # noqa: F401
import asaree.models.factorial_cell_result  # noqa: F401
import asaree.models.password_reset_token  # noqa: F401
import asaree.models.protocol  # noqa: F401
import asaree.models.protocol_run  # noqa: F401
import asaree.models.user  # noqa: F401
import asaree.models.user_api_token  # noqa: F401
import asaree.models.user_llm_setting  # noqa: F401
from asaree.config import get_settings
from asaree.models.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().product_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
