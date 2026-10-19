"""Invited members bring their own agents (docs/internal/bets/invitee-agents.md).

On a shared-secret relay only the secret's holder could claim names, so an
invited member could chat but never add an agent: `quorus agent add` was refused
(bet 1's invitee persona, 3b597b7b). This is invite-roles.md's agent section:

- a member's own key (a human claim) claims ``<member>-<suffix>`` as an agent
  owned by that member and that member's claim generation;
- at most :data:`MAX_AGENTS` per owner generation;
- the agent's tokens die with the owner's generation (``is_current``), and
  releasing the owner removes their agents (``release``);
- in a room, the agent acts only while its owner is a member, and never above
  the owner's role (``routes/room_auth.py``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

MAX_AGENTS = 8


class MintError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def mint(store, owner: str, owner_claim_id: str, name: str,
         known: Iterable[str] = ()) -> str:
    """Claim *name* as an agent of *owner*, atomically; returns its key once.

    *owner_claim_id* is the generation the caller's token carries: it must still
    be the owner's current human claim. *known* holds the names this relay
    already answers to (room members, registered participants): authority
    follows the name on a shared-secret relay, so an agent can't be minted into
    one of them, case-insensitively, like an invite redeem."""
    from quorus.auth.invite_codes import _connect
    from quorus.auth.participant_keys import insert_claim

    if not name.startswith(f"{owner}-") or len(name) <= len(owner) + 1:
        raise MintError(400, f"an agent of {owner!r} is named {owner}-<tool>")
    folded = name.casefold()
    with store._lock:
        db = _connect(store)
        try:
            db.execute("BEGIN IMMEDIATE")
            parent = db.execute("SELECT claim_id, kind FROM claims WHERE name = ?",
                                (owner,)).fetchone()
            if parent is None or parent[0] != owner_claim_id or parent[1] != "human":
                db.execute("ROLLBACK")
                raise MintError(403, "only an invited person's own key can add agents")
            count = db.execute("SELECT COUNT(*) FROM claims WHERE owner = ? AND "
                               "owner_claim_id = ?", (owner, owner_claim_id)).fetchone()[0]
            if count >= MAX_AGENTS:
                db.execute("ROLLBACK")
                raise MintError(409, f"{owner} already has {MAX_AGENTS} agents on this relay")
            taken = (any(n.casefold() == folded for n in known)
                     or db.execute("SELECT 1 FROM claims WHERE lower(name) = ?",
                                   (folded,)).fetchone()
                     or db.execute("SELECT 1 FROM reserved_names WHERE folded = ?",
                                   (folded,)).fetchone())
            key = None if taken else insert_claim(db, name, "agent", owner, owner_claim_id)
            if key is None:
                db.execute("ROLLBACK")
                raise MintError(409, f"the name {name!r} is already taken on this relay")
            db.execute("COMMIT")
        except sqlite3.Error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
    return key


def owner_of(store, name: str) -> str | None:
    """The member who owns agent *name*, or None for anyone else."""
    info = store.info(name)
    if info and info["kind"] == "agent" and info["owner"]:
        return info["owner"]
    return None


def owner_claim_of(store, name: str) -> str | None:
    """The claim generation of the member who owns agent *name*, or None."""
    info = store.info(name)
    if info and info["kind"] == "agent" and info["owner"] and info["owner_claim_id"]:
        return info["owner_claim_id"]
    return None
