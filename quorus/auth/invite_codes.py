"""Invite codes on shared-secret relays (docs/weekend/invite-roles.md, step 1).

An invite used to carry ``RELAY_SECRET``, which is admin. Now the relay keeps
an invite record and hands out an opaque random code, stored only as sha256.
Redeeming it claims a name and returns that name's participant key, so the
joiner never holds the secret. Expiry, revocation and a bounded number of uses
are checked and spent in one SQLite transaction (codex, review of e1798ca0).
Nothing is signed, so there is no signing secret to fall back to.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass

ROLES = ("member",)  # "observer" arrives with its enforcement (step 3), not before
MAX_USES = 20
MAX_TTL_S = 7 * 86400
CODE_PREFIX = "qi_"


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def create_table(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS invites (id TEXT PRIMARY KEY, "
               "code_hash TEXT NOT NULL UNIQUE, room_id TEXT NOT NULL, "
               "room_name TEXT NOT NULL, role TEXT NOT NULL, issuer TEXT NOT NULL, "
               "created_at REAL NOT NULL, expires_at REAL NOT NULL, "
               "max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0, "
               "revoked INTEGER NOT NULL DEFAULT 0)")
    # Names the relay has granted authority to (rooms, messages, presence) on
    # behalf of the shared-secret holder. Kept beside the claims so a redeem
    # and a grant serialize on one SQLite write lock (codex, review of 8e6fa5c2).
    db.execute("CREATE TABLE IF NOT EXISTS reserved_names (folded TEXT PRIMARY KEY, "
               "name TEXT NOT NULL, reserved_at REAL NOT NULL)")


@dataclass(frozen=True)
class Invite:
    id: str
    room_id: str
    room_name: str
    role: str
    expires_at: float
    max_uses: int
    uses: int


class InviteError(Exception):
    """Redeem refused. ``status`` is the HTTP code: 403 unusable, 409 name taken."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def _connect(store) -> sqlite3.Connection:
    # Autocommit mode so BEGIN IMMEDIATE takes the write lock *before* the read:
    # two redeems racing for the last use can't both see it free.
    return sqlite3.connect(store._path, isolation_level=None)


def mint(store, *, room_id: str, room_name: str, role: str, issuer: str,
         ttl_s: int, max_uses: int = 1) -> tuple[str, Invite]:
    """A new code (returned once) and its record."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if not 1 <= max_uses <= MAX_USES:
        raise ValueError(f"max_uses must be 1..{MAX_USES}")
    ttl_s = max(60, min(int(ttl_s), MAX_TTL_S))
    code = CODE_PREFIX + secrets.token_urlsafe(24)
    now = time.time()
    inv = Invite(id="inv_" + secrets.token_hex(6), room_id=room_id, room_name=room_name,
                 role=role, expires_at=now + ttl_s, max_uses=max_uses, uses=0)
    with store._lock, store._connect() as db:
        db.execute("INSERT INTO invites (id, code_hash, room_id, room_name, role, issuer, "
                   "created_at, expires_at, max_uses) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (inv.id, _hash(code), room_id, room_name, role, issuer, now,
                    inv.expires_at, max_uses))
    return code, inv


def reserve(store, names: Iterable[str], *, deliver_to_claimed: bool = False) -> str | None:
    """Reserve *names* before the shared-secret holder acts as them. Returns
    the first name an invitee already holds (refuse the grant), else None.

    Runs under the same write lock as :func:`redeem`, so a room created as
    "arav" and an invite redeemed as "arav" can't both win: whichever commits
    second sees the other (codex, review of 8e6fa5c2: the known-names
    snapshot was stale by the time the claim was inserted).

    With *deliver_to_claimed* (a DM's recipients) an invitee's name is not a
    refusal: delivering to an existing member is fine. Only names nobody holds
    yet get reserved, so a DM to a new name can't be claimed and read later
    (codex, review of 6f87fb51).
    """
    wanted = [n for n in dict.fromkeys(names) if n]
    if not wanted:
        return None
    with store._lock:
        db = _connect(store)
        try:
            db.execute("BEGIN IMMEDIATE")
            for name in wanted:
                folded = name.casefold()
                if db.execute("SELECT 1 FROM reserved_names WHERE folded = ?",
                              (folded,)).fetchone():
                    continue
                invited = db.execute(
                    "SELECT 1 FROM claims WHERE lower(name) = ? AND kind = 'human'",
                    (folded,)).fetchone()
                if invited and deliver_to_claimed:
                    continue
                if invited:
                    db.execute("ROLLBACK")
                    return name
                db.execute("INSERT INTO reserved_names (folded, name, reserved_at) "
                           "VALUES (?, ?, ?)", (folded, name, time.time()))
            db.execute("COMMIT")
        except sqlite3.Error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
    return None


def redeem(store, code: str, name: str,
           reserved: Iterable[str] = ()) -> tuple[str, str, Invite]:
    """Spend one use of *code* and claim *name* (kind=human), atomically.

    Returns ``(participant_key, claim_id, invite)``. A taken name raises 409
    without spending the use; an unknown, revoked, expired or used-up code
    raises 403. *reserved* holds the names this relay already knows (room
    members, registered participants): on a shared-secret relay most of them
    never claimed a key, and authority follows the name, so an invitee must
    not be able to claim "arav" and walk into arav's rooms (codex, review of
    3bedbbea). Names compare case-insensitively, so "Arav" is refused too.
    """
    from quorus.auth.participant_keys import insert_claim

    if not code.startswith(CODE_PREFIX):
        raise InviteError(403, "invalid invite code")
    with store._lock:
        db = _connect(store)
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id, room_id, room_name, role, expires_at, max_uses, "
                             "uses, revoked FROM invites WHERE code_hash = ?",
                             (_hash(code),)).fetchone()
            if row is None or row[7] or row[4] <= time.time() or row[6] >= row[5]:
                db.execute("ROLLBACK")
                raise InviteError(403, "this invite is invalid, expired or used up")
            folded = name.casefold()
            taken = (any(r.casefold() == folded for r in reserved)
                     or db.execute("SELECT 1 FROM claims WHERE lower(name) = ?",
                                   (folded,)).fetchone()
                     or db.execute("SELECT 1 FROM reserved_names WHERE folded = ?",
                                   (folded,)).fetchone())
            key = None if taken else insert_claim(db, name, "human", None, None)
            if key is None:
                db.execute("ROLLBACK")
                raise InviteError(409, f"the name {name!r} is already claimed on this "
                                       "relay; pick another with --name")
            claim_id = db.execute("SELECT claim_id FROM claims WHERE name = ?",
                                  (name,)).fetchone()[0]
            db.execute("UPDATE invites SET uses = uses + 1 WHERE id = ?", (row[0],))
            db.execute("COMMIT")
        except sqlite3.Error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
    return key, claim_id, Invite(id=row[0], room_id=row[1], room_name=row[2], role=row[3],
                       expires_at=row[4], max_uses=row[5], uses=row[6] + 1)


def undo_redeem(store, invite_id: str, name: str, claim_id: str) -> bool:
    """Compensation when joining the room failed after a redeem: give the use back
    and free the name, so the joiner can retry.

    Only the claim generation this redeem created is removed: if the name was
    released and claimed again meanwhile, that newer claim stays (codex, review
    of 3bedbbea). The use comes back only when that claim was actually removed,
    so running this twice changes nothing the second time."""
    with store._lock, store._connect() as db:
        gone = db.execute("DELETE FROM claims WHERE name = ? AND claim_id = ?",
                          (name, claim_id)).rowcount > 0
        if gone:
            db.execute("UPDATE invites SET uses = MAX(uses - 1, 0) WHERE id = ?",
                       (invite_id,))
    return gone


def revoke(store, invite_id: str) -> bool:
    with store._lock, store._connect() as db:
        return db.execute("UPDATE invites SET revoked = 1 WHERE id = ?",
                          (invite_id,)).rowcount > 0


def list_invites(store) -> list[dict]:
    """Live and past invites, never their codes."""
    with store._lock, store._connect() as db:
        rows = db.execute("SELECT id, room_name, role, issuer, expires_at, max_uses, uses, "
                          "revoked FROM invites ORDER BY created_at DESC").fetchall()
    keys = ("id", "room", "role", "issuer", "expires_at", "max_uses", "uses", "revoked")
    return [dict(zip(keys, r)) for r in rows]
