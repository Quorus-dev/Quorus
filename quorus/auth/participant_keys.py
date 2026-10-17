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


KINDS = ("human", "agent", "unclassified")


def insert_claim(db: sqlite3.Connection, name: str, kind: str, owner: str | None,
                 owner_claim_id: str | None) -> str | None:
    """Insert one claim inside the caller's transaction; None if *name* is taken."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    try:
        db.execute("INSERT INTO claims (name, key_hash, created_at, claim_id, kind, owner, "
                   "owner_claim_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (name, _hash(key), time.time(), secrets.token_hex(16), kind, owner,
                    owner_claim_id))
    except sqlite3.IntegrityError:
        return None
    return key


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
            if "kind" not in columns:
                # Who a claim belongs to (docs/weekend/invite-roles.md). Rows from
                # before include daemon keys, so they are `unclassified`, never
                # human: they can't approve or own agents until the relay owner
                # classifies them (codex, review of 45e1f921).
                db.execute("ALTER TABLE claims ADD COLUMN kind TEXT NOT NULL "
                           "DEFAULT 'unclassified'")
                db.execute("ALTER TABLE claims ADD COLUMN owner TEXT")
                db.execute("ALTER TABLE claims ADD COLUMN owner_claim_id TEXT")
            from quorus.auth.invite_codes import create_table

            create_table(db)
        os.chmod(self._path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, isolation_level="IMMEDIATE")

    def claim(self, name: str, kind: str = "unclassified", owner: str | None = None,
              owner_claim_id: str | None = None) -> str | None:
        """A new key for *name*, or None when someone already claimed it."""
        with self._lock, self._connect() as db:
            return insert_claim(db, name, kind, owner, owner_claim_id)

    def info(self, name: str) -> dict | None:
        """A claim's kind, owner and generation (never the key hash)."""
        with self._lock, self._connect() as db:
            row = db.execute("SELECT claim_id, kind, owner, owner_claim_id FROM claims "
                             "WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return {"name": name, "claim_id": row[0], "kind": row[1], "owner": row[2],
                "owner_claim_id": row[3]}

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
