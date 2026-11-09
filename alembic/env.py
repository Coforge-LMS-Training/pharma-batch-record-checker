"""Alembic migration runner.

The store uses psycopg. SQLAlchemy appears in this file only because Alembic's
context needs an engine. The revisions themselves are SQL.
"""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def admin_url() -> str:
    """DATABASE_ADMIN_URL, written so SQLAlchemy uses the psycopg 3 driver."""
    url = os.environ.get("DATABASE_ADMIN_URL")
    if not url:
        raise RuntimeError("DATABASE_ADMIN_URL is not set")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def run_migrations_offline() -> None:
    """Emit SQL without opening a connection."""
    context.configure(
        url=admin_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against DATABASE_ADMIN_URL."""
    connectable = create_engine(admin_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
