"""Alembic environment.

Wires Alembic to the backend's `Settings` so we never duplicate the
DATABASE_URL across `alembic.ini` and the application config. Picks up
target metadata from `app.models` when it exists (lands in P0.05); until
then, autogenerate is a no-op.
"""

from __future__ import annotations

import importlib
from logging.config import fileConfig

from alembic import context
from app.config import get_settings
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject DATABASE_URL from Settings so the URL has one source of truth.
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)


def _load_target_metadata() -> object | None:
    """Import `app.models` and return its Base.metadata if available.

    Until P0.05 lands `app/models/base.py`, this returns None and
    autogenerate emits an empty migration.
    """
    try:
        models = importlib.import_module("app.models")
    except ModuleNotFoundError:
        return None
    base = getattr(models, "Base", None)
    return base.metadata if base is not None else None


target_metadata = _load_target_metadata()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
