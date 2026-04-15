"""Postgres async engine and session factory for Quorus."""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# Workaround for SQLAlchemy asyncpg dialect bug: it passes 'channel_binding'
# directly to asyncpg.connect() which doesn't accept it as a keyword argument.
_FILTERED_CONNECT_ARGS = {"channel_binding"}


def _filter_connect_args(dialect, conn_rec, cargs, cparams):
    """Remove unsupported connect args that SQLAlchemy's asyncpg dialect adds."""
    for key in _FILTERED_CONNECT_ARGS:
        cparams.pop(key, None)


def normalize_database_url(url: str) -> str:
    """Make a raw DATABASE_URL safe for the asyncpg driver.

    Handles two common incompatibilities:
    - Non-asyncpg schemes (``postgresql://``, ``postgresql+psycopg2://``) are
      rewritten to ``postgresql+asyncpg://``.
    - libpq-style ``sslmode=require`` (what Neon/Supabase/Render emit) is
      translated to ``ssl=require``, which asyncpg + SQLAlchemy understand.
    """
    url = re.sub(r"^postgresql(\+\w+)?://", "postgresql+asyncpg://", url)
    url = url.replace("sslmode=", "ssl=")
    return url


def get_database_url() -> str:
    """Return the normalized DATABASE_URL from environment, or raise."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Set it to a postgresql+asyncpg:// connection string."
        )
    return normalize_database_url(url)


async def init_engine(database_url: str | None = None) -> None:
    """Create the async engine and session factory. Call once at startup.

    The URL is always passed through :func:`normalize_database_url` so callers
    can hand us raw libpq-style connection strings (e.g. Neon's
    ``postgresql://...?sslmode=require``) without worrying about asyncpg's
    narrower query-param vocabulary.
    """
    global _engine, _session_factory
    raw = database_url or os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Set it to a postgresql:// or postgresql+asyncpg:// connection string."
        )
    url = normalize_database_url(raw)
    _engine = create_async_engine(
        url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=os.environ.get("SQL_ECHO", "").lower() in ("1", "true"),
    )
    # Filter out channel_binding before asyncpg.connect() is called
    event.listen(_engine.sync_engine, "do_connect", _filter_connect_args)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def close_engine() -> None:
    """Dispose the engine. Call on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session, committing on success or rolling back on error."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_engine() first.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_connection() -> bool:
    """Return True if the database is reachable."""
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
        return True
    except Exception:
        return False
