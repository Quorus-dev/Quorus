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
    " touched REAL NOT NULL, stamp REAL NOT NULL DEFAULT 0,"
    " PRIMARY KEY (ns_key, field))"
)


def written_at(entry: dict[str, Any] | None) -> float:
    """When an entry was written: memory and capabilities stamp
    ``updated_at``, tool records ``registered_at``, tombstones ``updated_at``."""
    if not entry:
        return 0.0
    stamp = entry.get("updated_at", entry.get("registered_at", 0.0))
    return float(stamp) if isinstance(stamp, (int, float)) else 0.0

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
        columns = {row[1] for row in conn.execute("PRAGMA table_info(p1_entries)")}
        if "stamp" not in columns:  # a file from before the stamp column
            _add_stamps(conn)
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


def _add_stamps(conn: sqlite3.Connection) -> None:
    """Add the stamp column and fill it from each row's own timestamp, under
    one write lock. Left at 0, an older write replaced a newer tombstone from
    before the upgrade (codex, a86d9b76). sqlite3 doesn't open a transaction
    before DDL, so the ALTER committed alone and another connection could
    write before the backfill (codex, b4c8d2d4): BEGIN IMMEDIATE takes the
    lock first, and the column check is repeated under it."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(p1_entries)")}
        if "stamp" not in columns:
            conn.execute("ALTER TABLE p1_entries ADD COLUMN stamp REAL NOT NULL DEFAULT 0")
            rows = conn.execute("SELECT ns_key, field, value FROM p1_entries").fetchall()
            updates = []
            for ns_key, field, value in rows:
                try:
                    parsed = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(parsed, dict) and written_at(parsed):
                    updates.append((written_at(parsed), ns_key, field))
            conn.executemany(
                "UPDATE p1_entries SET stamp = ? WHERE ns_key = ? AND field = ?", updates)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def set_entry(ns_key: str, field: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Write *entry* unless the store already holds a newer write to this
    field, in one statement, and return what the store holds afterwards.

    Comparing in the store, not in the caller, closes the window where
    another relay process writes between a caller's read and its write
    (codex, review of 6d825f38)."""
    conn = _connect(db_path())
    try:
        with conn:
            conn.execute(
                "INSERT INTO p1_entries (ns_key, field, value, touched, stamp) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (ns_key, field) DO UPDATE SET value = excluded.value, "
                "touched = excluded.touched, stamp = excluded.stamp "
                "WHERE excluded.stamp >= p1_entries.stamp",
                (ns_key, field, json.dumps(entry), time.time(), written_at(entry)),
            )
            row = conn.execute("SELECT value FROM p1_entries WHERE ns_key = ? AND field = ?",
                               (ns_key, field)).fetchone()
        try:
            winner = json.loads(row[0]) if row else entry
        except (json.JSONDecodeError, TypeError):
            winner = entry
        return winner if isinstance(winner, dict) else entry
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
    expired: list[tuple[str, str]] = []
    for field, value in rows:
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        # A delete's tombstone holds no content, only when it happened; it
        # goes once it is older than the TTL like any other entry.
        if parsed.get("deleted") is True and float(parsed.get("updated_at") or 0) < now - TTL_S:
            expired.append((field, value))
            continue
        out[field] = parsed
    if expired:
        # Only the exact tombstone that was read: a write that replaced it in
        # the meantime must survive (codex, review of a86d9b76).
        conn = _connect(db_path())
        try:
            with conn:
                conn.executemany(
                    "DELETE FROM p1_entries WHERE ns_key = ? AND field = ? AND value = ?",
                    [(ns_key, f, v) for f, v in expired])
        finally:
            conn.close()
    return out


def drop(ns_key: str) -> None:
    conn = _connect(db_path())
    try:
        with conn:
            conn.execute("DELETE FROM p1_entries WHERE ns_key = ?", (ns_key,))
    finally:
        conn.close()
