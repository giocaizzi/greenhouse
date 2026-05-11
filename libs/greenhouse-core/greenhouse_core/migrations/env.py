"""Alembic environment.

Resolves the database URL from ``$IRRIGATION_DB_URL`` (matching the server
default) or falls back to ``sqlite:///data/irrigation.db``. The target
metadata is the project's :class:`Base.metadata`, so ``alembic
revision --autogenerate`` can detect ORM drift on every release.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from greenhouse_core.models import Base

config = context.config

if config.config_file_name is not None:
    from logging.config import fileConfig

    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DB_URL = os.environ.get("IRRIGATION_DB_URL", "sqlite:///data/irrigation.db")
config.set_main_option("sqlalchemy.url", DB_URL)


def run_migrations_offline() -> None:
    """Run migrations against the URL only (no live connection)."""
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live engine connection.

    When a connection is passed via ``config.attributes['connection']`` (the
    standard pattern used by tests and the in-process ``init_db`` helper),
    that connection is reused so migrations target the same engine the
    application is using — critical for in-memory SQLite test fixtures.
    """
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        context.configure(
            connection=existing_connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
