"""Alembic environment configuration for async Postgres migrations."""

import asyncio
import os

from alembic import context
from sqlalchemy import event, pool
from sqlalchemy.ext.asyncio import create_async_engine

# Import every ORM module so its tables register on Base.metadata.
# Required for Alembic autogenerate to see the full schema; runtime
# migrations don't strictly need it but the import is cheap.
import quorus.admin.models  # noqa: E402,F401
import quorus.models.audit  # noqa: E402,F401
import quorus.models.outbox  # noqa: E402,F401
from quorus.storage.base import Base
from quorus.storage.postgres import normalize_database_url

target_metadata = Base.metadata

# Workaround for SQLAlchemy asyncpg dialect bug: it passes 'channel_binding'
# directly to asyncpg.connect() which doesn't accept it as a keyword argument.
_FILTERED_CONNECT_ARGS = {"channel_binding"}


def get_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL must be set for migrations")
    return normalize_database_url(url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without a live connection."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def _filter_connect_args(dialect, conn_rec, cargs, cparams):
    """Remove unsupported connect args that SQLAlchemy's asyncpg dialect adds."""
    for key in _FILTERED_CONNECT_ARGS:
        cparams.pop(key, None)


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an async engine."""
    url = get_url()

    connectable = create_async_engine(url, poolclass=pool.NullPool)

    # Filter out channel_binding before asyncpg.connect() is called
    event.listen(connectable.sync_engine, "do_connect", _filter_connect_args)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
