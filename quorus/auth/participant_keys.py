"""Per-participant keys for shared-secret relays (docs/design/cross-machine-code.md).

A relay with no database has only its shared ``RELAY_SECRET``, which every
invitee holds, so it can't tell participants apart. Here a secret holder may
claim a name once; the relay keeps only sha256(key) on disk and later turns
the key into a JWT for exactly that name. First claim wins; the relay owner
releases a name locally (``quorus release-name``).

JWTs are signed with a random server-only key stored at 0600 next to the
claims, never derived from ``RELAY_SECRET``: otherwise every invitee could
forge any participant (review of e1ca9088).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

KEY_PREFIX = "quorus_pk_"
SIGNING_KEY_FILE = "jwt-signing.key"
CLAIMS_DB = "participant_keys.db"


def data_dir() -> Path:
    """Where a shared-secret relay keeps its state: next to the history DB,
    else next to MESSAGES_FILE, else ~/.quorus. Never relative to the working
    directory: under launchd that is "/" and the relay crashed on start with
    "Read-only file system: '/.quorus'" (2026-10-17)."""
    db = os.environ.get("QUORUS_HISTORY_DB") or os.environ.get("MURMUR_HISTORY_DB")
    if db:
        return Path(db).expanduser().resolve().parent
    messages = os.environ.get("MESSAGES_FILE")
    if messages:
        return Path(messages).expanduser().resolve().parent
    return Path.home() / ".quorus"


def load_or_create_signing_key(folder: Path) -> str:
    """The relay's own JWT key: created once, owner-only, never sent anywhere."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SIGNING_KEY_FILE
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    with os.fdopen(fd, "w") as fh:
        fh.write(key)
    return key


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ParticipantKeyStore:
    """name -> sha256(key), in SQLite so claims survive relay restarts."""

    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self._path = folder / CLAIMS_DB
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS claims (name TEXT PRIMARY KEY, "
                       "key_hash TEXT NOT NULL, created_at REAL NOT NULL)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(claims)")}
            if "claim_id" not in columns:
                # Each claim is a generation: tokens carry it, so a released and
                # reclaimed name invalidates the old holder's tokens (review of
                # 98b488c7). Rows from before this column get a fresh id, which
                # retires any token issued from them.
                db.execute("ALTER TABLE claims ADD COLUMN claim_id TEXT")
                for (name,) in db.execute("SELECT name FROM claims").fetchall():
                    db.execute("UPDATE claims SET claim_id = ? WHERE name = ?",
                               (secrets.token_hex(16), name))
        os.chmod(self._path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, isolation_level="IMMEDIATE")

    def claim(self, name: str) -> str | None:
        """A new key for *name*, or None when someone already claimed it."""
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        with self._lock, self._connect() as db:
            try:
                db.execute("INSERT INTO claims (name, key_hash, created_at, claim_id) "
                           "VALUES (?, ?, ?, ?)",
                           (name, _hash(key), time.time(), secrets.token_hex(16)))
            except sqlite3.IntegrityError:
                return None
        return key

    def lookup(self, key: str) -> tuple[str, str] | None:
        """``(name, claim_id)`` for a participant key, or None."""
        if not key.startswith(KEY_PREFIX):
            return None
        digest = _hash(key)
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT name, key_hash, claim_id FROM claims").fetchall()
        # Constant-time compare per row; the table is small (one row per person).
        found = None
        for name, stored, claim_id in rows:
            if hmac.compare_digest(stored, digest):
                found = (name, claim_id)
        return found

    def name_for(self, key: str) -> str | None:
        found = self.lookup(key)
        return found[0] if found else None

    def is_current(self, name: str, claim_id: str) -> bool:
        """True while *claim_id* is still *name*'s claim (not released/reclaimed)."""
        with self._lock, self._connect() as db:
            row = db.execute("SELECT claim_id FROM claims WHERE name = ?", (name,)).fetchone()
        return bool(row and row[0] and claim_id and hmac.compare_digest(row[0], claim_id))

    def release(self, name: str) -> bool:
        with self._lock, self._connect() as db:
            return db.execute("DELETE FROM claims WHERE name = ?", (name,)).rowcount > 0
