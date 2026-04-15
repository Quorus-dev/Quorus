"""SQLite-backed room history backend.

Provides durable, zero-dependency room message persistence using Python's
built-in sqlite3 module via asyncio.to_thread.  This is the default history
backend in dev/single-node mode (no Redis required).

History survives relay restarts and lets any agent joining a room query
the full conversation context — the foundation for Summary Cascade v2.

Configuration:
    MURMUR_HISTORY_DB  path to the SQLite file (default: .quorus/history.db)
    MAX_ROOM_HISTORY   max messages retained per room (default: 2000)
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

_DEFAULT_DB = ".quorus/history.db"
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS room_history (
    id          TEXT    NOT NULL UNIQUE,
    tenant_id   TEXT    NOT NULL,
    room_id     TEXT    NOT NULL,
    from_name   TEXT    NOT NULL,
    room_name   TEXT    NOT NULL DEFAULT '',
    content     TEXT    NOT NULL,
    message_type TEXT   NOT NULL DEFAULT 'chat',
    timestamp   TEXT    NOT NULL,
    reply_to    TEXT,
    extra       TEXT,
    seq         INTEGER PRIMARY KEY AUTOINCREMENT
);
"""
_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_rh_room ON room_history (tenant_id, room_id, seq);",
    "CREATE INDEX IF NOT EXISTS ix_rh_msg_id ON room_history (id);",
    "CREATE INDEX IF NOT EXISTS ix_rh_sender ON room_history (tenant_id, room_id, from_name);",
]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(_CREATE_TABLE)
    for idx in _CREATE_INDEXES:
        conn.execute(idx)
    conn.commit()
    return conn


def _row_to_msg(row: sqlite3.Row) -> dict:
    msg: dict = {
        "id": row["id"],
        "from": row["from_name"],
        "from_name": row["from_name"],
        "room": row["room_name"],
        "content": row["content"],
        "message_type": row["message_type"],
        "timestamp": row["timestamp"],
    }
    if row["reply_to"]:
        msg["reply_to"] = row["reply_to"]
    extra = row["extra"]
    if extra:
        try:
            msg.update(json.loads(extra))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return msg


def _msg_extra(message: dict) -> str | None:
    """Serialize any non-standard fields to JSON for the extra column."""
    known = {
        "id", "from", "from_name", "room", "content",
        "message_type", "timestamp", "reply_to",
    }
    rest = {k: v for k, v in message.items() if k not in known}
    return json.dumps(rest) if rest else None


class SQLiteRoomHistoryBackend:
    """Append-only room history backed by SQLite.

    Thread-safe: all DB I/O runs in a thread pool via asyncio.to_thread.
    A single sqlite3.Connection is reused across calls (WAL mode is safe
    for concurrent reads).
    """

    def __init__(self, db_path: str | None = None, max_history: int = 2000) -> None:
        self._db_path = db_path or os.environ.get("MURMUR_HISTORY_DB", _DEFAULT_DB)
        self._max_history = max_history
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = _connect(self._db_path)
        return self._conn

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    async def append(
        self, tenant_id: str, room_id: str, message: dict
    ) -> None:
        msg_id = message.get("id", "")
        from_name = message.get("from_name") or message.get("from", "")
        room_name = message.get("room", "")
        content = message.get("content", "")
        message_type = message.get("message_type", "chat")
        timestamp = message.get("timestamp", "")
        reply_to = message.get("reply_to")
        extra = _msg_extra(message)

        def _write() -> None:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR IGNORE INTO room_history
                    (id, tenant_id, room_id, from_name, room_name,
                     content, message_type, timestamp, reply_to, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, tenant_id, room_id, from_name, room_name,
                 content, message_type, timestamp, reply_to, extra),
            )
            # Trim excess history (keep newest max_history rows)
            conn.execute(
                """
                DELETE FROM room_history
                WHERE tenant_id = ? AND room_id = ?
                  AND seq NOT IN (
                    SELECT seq FROM room_history
                    WHERE tenant_id = ? AND room_id = ?
                    ORDER BY seq DESC
                    LIMIT ?
                  )
                """,
                (tenant_id, room_id, tenant_id, room_id, self._max_history),
            )
            conn.commit()

        async with self._lock:
            await asyncio.to_thread(_write)

    async def get_recent(
        self, tenant_id: str, room_id: str, limit: int
    ) -> list[dict]:
        def _read() -> list[dict]:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM room_history
                    WHERE tenant_id = ? AND room_id = ?
                    ORDER BY seq DESC
                    LIMIT ?
                ) sub
                ORDER BY seq ASC
                """,
                (tenant_id, room_id, limit),
            ).fetchall()
            return [_row_to_msg(r) for r in rows]

        async with self._lock:
            return await asyncio.to_thread(_read)

    async def get_by_id(
        self, tenant_id: str, room_id: str, message_id: str
    ) -> dict | None:
        def _read() -> dict | None:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM room_history WHERE id = ? AND tenant_id = ? AND room_id = ?",
                (message_id, tenant_id, room_id),
            ).fetchone()
            return _row_to_msg(row) if row else None

        async with self._lock:
            return await asyncio.to_thread(_read)

    async def get_thread(
        self, tenant_id: str, room_id: str, parent_id: str, limit: int = 50
    ) -> list[dict]:
        def _read() -> list[dict]:
            conn = self._get_conn()
            parent_row = conn.execute(
                "SELECT * FROM room_history WHERE id = ? AND tenant_id = ? AND room_id = ?",
                (parent_id, tenant_id, room_id),
            ).fetchone()
            replies = conn.execute(
                """
                SELECT * FROM room_history
                WHERE tenant_id = ? AND room_id = ? AND reply_to = ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (tenant_id, room_id, parent_id, limit),
            ).fetchall()
            result = []
            if parent_row:
                result.append(_row_to_msg(parent_row))
            result.extend(_row_to_msg(r) for r in replies)
            return result[-limit:]

        async with self._lock:
            return await asyncio.to_thread(_read)

    async def search(
        self,
        tenant_id: str,
        room_id: str,
        q: str = "",
        sender: str = "",
        message_type: str = "",
        limit: int = 50,
    ) -> list[dict]:
        def _read() -> list[dict]:
            conn = self._get_conn()
            clauses = ["tenant_id = ?", "room_id = ?"]
            params: list = [tenant_id, room_id]
            if q:
                clauses.append("content LIKE ?")
                params.append(f"%{q}%")
            if sender:
                clauses.append("from_name = ?")
                params.append(sender)
            if message_type:
                clauses.append("message_type = ?")
                params.append(message_type)
            params.append(limit)
            sql = (
                f"SELECT * FROM room_history WHERE {' AND '.join(clauses)} "
                f"ORDER BY seq DESC LIMIT ?"
            )
            rows = conn.execute(sql, params).fetchall()
            rows.reverse()
            return [_row_to_msg(r) for r in rows]

        async with self._lock:
            return await asyncio.to_thread(_read)

    async def delete(self, tenant_id: str, room_id: str) -> None:
        def _write() -> None:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM room_history WHERE tenant_id = ? AND room_id = ?",
                (tenant_id, room_id),
            )
            conn.commit()

        async with self._lock:
            await asyncio.to_thread(_write)

    async def rename_room_in_history(
        self, tenant_id: str, room_id: str, new_name: str
    ) -> None:
        def _write() -> None:
            conn = self._get_conn()
            conn.execute(
                "UPDATE room_history SET room_name = ? WHERE tenant_id = ? AND room_id = ?",
                (new_name, tenant_id, room_id),
            )
            conn.commit()

        async with self._lock:
            await asyncio.to_thread(_write)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
