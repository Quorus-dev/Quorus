"""SQLite fallback for the Phase-1 mirror when no Redis is configured.

Without Redis, persistent memory, capabilities and room tool catalogs lived
only in process memory, so a relay restart wiped them: "persistent" memory
wasn't (memory slice 1). These blocking helpers keep the same buckets in one
stdlib sqlite3 file next to the relay's other state; ``p1_persistence`` calls
them through ``asyncio.to_thread``.

* The file is created owner-only (0600) and re-tightened on every open.
* Every statement is parameterized.
* A corrupt file is reported once and then skipped for the life of the
  process, so the services run in memory instead of failing; a locked file
  raises, and the caller treats that as a transient miss.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("quorus.services.p1_sqlite")

DB_FILE = "p1_state.db"
TTL_S = 60 * 60 * 24 * 30  # same 30 days as the Redis mirror, refreshed on read and write
_BUSY_S = 1.0  # wait this long for a lock, then give up (the caller logs it)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS p1_entries ("
    " ns_key TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,"
    " touched REAL NOT NULL, PRIMARY KEY (ns_key, field))"
)

_broken: set[str] = set()
_broken_lock = threading.Lock()


class Unavailable(Exception):
    """This file can't be used (corrupt); run in memory instead."""


def db_path() -> Path:
    # Next to the history DB / MESSAGES_FILE, else ~/.quorus: the same place
    # as the relay's participant keys and invites (never the working dir).
    from quorus.auth.participant_keys import data_dir

    return data_dir() / DB_FILE


def _connect(path: Path) -> sqlite3.Connection:
    key = str(path)
    if key in _broken:
        raise Unavailable(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    os.chmod(path, 0o600)
    conn = sqlite3.connect(key, timeout=_BUSY_S)
    try:
        conn.execute(_SCHEMA)
    except sqlite3.OperationalError:
        conn.close()
        raise  # locked: transient
    except sqlite3.DatabaseError as exc:  # "file is not a database"
        conn.close()
        with _broken_lock:
            first = key not in _broken
            _broken.add(key)
        if first:
            logger.error("p1 store %s is unreadable (%s); memory, capabilities and "
                         "tool catalogs stay in memory until it is fixed", key, exc)
        raise Unavailable(key) from exc
    return conn


def set_entry(ns_key: str, field: str, entry: dict[str, Any]) -> None:
    conn = _connect(db_path())
    try:
        with conn:
            conn.execute(
                "INSERT INTO p1_entries (ns_key, field, value, touched) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (ns_key, field) DO UPDATE SET value = excluded.value, "
                "touched = excluded.touched",
                (ns_key, field, json.dumps(entry), time.time()),
            )
    finally:
        conn.close()


def delete_entry(ns_key: str, field: str) -> None:
    conn = _connect(db_path())
    try:
        with conn:
            conn.execute("DELETE FROM p1_entries WHERE ns_key = ? AND field = ?",
                         (ns_key, field))
    finally:
        conn.close()


def load(ns_key: str) -> dict[str, dict[str, Any]]:
    conn = _connect(db_path())
    try:
        now = time.time()
        with conn:
            conn.execute("DELETE FROM p1_entries WHERE ns_key = ? AND touched < ?",
                         (ns_key, now - TTL_S))
            rows = conn.execute("SELECT field, value FROM p1_entries WHERE ns_key = ?",
                                (ns_key,)).fetchall()
            if rows:
                conn.execute("UPDATE p1_entries SET touched = ? WHERE ns_key = ?",
                             (now, ns_key))
    finally:
        conn.close()
    out: dict[str, dict[str, Any]] = {}
    for field, value in rows:
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            out[field] = parsed
    return out


def drop(ns_key: str) -> None:
    conn = _connect(db_path())
    try:
        with conn:
            conn.execute("DELETE FROM p1_entries WHERE ns_key = ?", (ns_key,))
    finally:
        conn.close()
