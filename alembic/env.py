"""Alembic environment for Listik's SQLite database."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make ``listik`` importable when Alembic is run from another directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

config = context.config


def _database_url() -> str:
    """Require an explicit database path for every Alembic invocation."""
    configured = (os.environ.get("LISTIK_DB") or "").strip()
    if not configured:
        raise SystemExit(
            "Alembic database is not configured; set LISTIK_DB to a temporary "
            "SQLite file, for example: LISTIK_DB=/tmp/listik-alembic.db alembic upgrade head"
        )
    return f"sqlite:///{Path(configured).expanduser().resolve().as_posix()}"


config.set_main_option("sqlalchemy.url", _database_url())
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"check_same_thread": False},
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
