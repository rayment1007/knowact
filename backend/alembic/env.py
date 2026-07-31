"""Alembic migration environment for KnowAct.

This environment is wired to the application's own configuration and ORM
metadata so migrations always reflect the code:

* The database URL is taken from ``app.config.Settings.database_url`` (loaded
  from the environment / ``.env``) rather than being hard-coded in
  ``alembic.ini``. This keeps a single source of truth for the connection URL.
* ``target_metadata`` is ``app.database.Base.metadata``. Both model modules
  (``app.core.models`` and ``app.modules.cwi.models``) are imported for their
  side effect of registering every table on ``Base.metadata`` so
  ``--autogenerate`` sees the full schema.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---------------------------------------------------------------------------
# Make the ``app`` package importable when Alembic runs from ``backend/``.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402
from app.database import Base  # noqa: E402

# Import model modules for their registration side effects so that every table
# is present on ``Base.metadata`` for autogenerate. The imports are intentional
# and must not be pruned by linters/formatters.
import app.core.models  # noqa: E402,F401
import app.modules.cwi.models  # noqa: E402,F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Inject the application's database URL so it is never hard-coded in
# alembic.ini. Escape any ``%`` so ConfigParser interpolation does not choke on
# URL-encoded credentials.
_database_url = get_settings().database_url
config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for 'autogenerate' support: the single source of truth for
# the schema across core and advisor modules.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""
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
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
