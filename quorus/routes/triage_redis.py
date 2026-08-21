"""Redis backing for the triage auction (WAKE_REBUILD_SPEC R3).

The in-memory auction in ``routes/triage.py`` elects exactly one winner per
mention — per process. Two uvicorn workers (or two Fly machines) each run
their own auction and BOTH elect a winner, so one mention gets two replies.

This module shares the auction through Redis when a connection is
initialised (``quorus.backends.redis_client``), with these structures:

* ``triage:bids:{tid}:{rid}:{mid}`` — hash: participant → bid JSON, plus a
  ``__expires_at`` field for the window deadline. TTL'd.
* ``triage:claim:{tid}:{rid}:{mid}`` — the serialized winning claim, written
  with ``SET NX`` so exactly ONE replica wins the race; everyone else reads
  the stored claim back (idempotent re-claim, same contract as in-memory).
* ``triage:credit:{tid}`` — hash: participant → fairness credit
  (``HINCRBYFLOAT``); winner pays −1.0, losers gain +0.25, applied only by
  the replica whose ``SET NX`` succeeded so credits are never double-counted.

``routes/triage.py`` calls :func:`redis_or_none` per request and falls back
to its process-local path when Redis is absent — file-mode relays and unit
tests keep working unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

_CLAIM_TTL_S = 3600
# Bids at/above this are explicit @-mentions — see routes/triage.py.
MENTION_BID = 1.0
_EXPIRES_FIELD = "__expires_at"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def redis_or_none():
    """Return the initialised Redis connection, or None when Redis is not
    configured at all.

    Deliberately narrow: swallowing every exception here would silently
    drop a configured deployment back to the process-local auction — the
    exact double-winner bug this module exists to prevent — with no log
    line to explain it.
    """
    from quorus.backends.redis_client import get_redis_or_none

    return get_redis_or_none()


def _bids_key(tid: str, rid: str, mid: str) -> str:
    return f"triage:bids:{tid}:{rid}:{mid}"


def _claim_key(tid: str, rid: str, mid: str) -> str:
    return f"triage:claim:{tid}:{rid}:{mid}"


def _credit_key(tid: str) -> str:
    return f"triage:credit:{tid}"


async def get_credit(r: Any, tid: str, participant: str) -> float:
    raw = await r.hget(_credit_key(tid), participant)
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


async def _load_bids(r: Any, tid: str, rid: str, mid: str) -> tuple[dict[str, dict], str | None]:
    """Return ``(bids, expires_at_iso)`` from the shared hash."""
    raw = await r.hgetall(_bids_key(tid, rid, mid))
    bids: dict[str, dict] = {}
    expires_at: str | None = None
    for k, v in (raw or {}).items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        if key == _EXPIRES_FIELD:
            expires_at = val
            continue
        try:
            parsed = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            bids[key] = parsed
    return bids, expires_at


async def _leader(
    r: Any, tid: str, bids: dict[str, dict]
) -> tuple[str, float] | None:
    """Addressed mail first, then (bid + fairness credit), created_at as
    tiebreak — same rule as the in-memory auction.

    A bid at ``MENTION_BID`` or above is an explicit @-mention and outranks
    every credit-adjusted bid: fairness rotates UNADDRESSED work, it must
    never hand your mention to a teammate.
    """
    best: tuple[bool, float, str, str] | None = None
    best_bid = 0.0
    for name, item in bids.items():
        try:
            bid = float(item.get("bid", 0.0))
        except (TypeError, ValueError):
            continue
        score = bid + await get_credit(r, tid, name)
        created = str(item.get("created_at", ""))
        cand = (bid >= MENTION_BID, score, created, name)
        if best is None or cand > best:
            best = cand
            best_bid = bid
    if best is None:
        return None
    return best[3], best_bid


async def record_bid(
    r: Any,
    *,
    tid: str,
    rid: str,
    mid: str,
    participant: str,
    bid: float,
    reason: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Store the bid and return the current leader snapshot."""
    key = _bids_key(tid, rid, mid)
    bids, expires_at = await _load_bids(r, tid, rid, mid)
    now = _now()
    if expires_at is None or expires_at < now.isoformat():
        # A lapsed window starts EMPTY — the in-memory path constructs a
        # fresh _BidWindow here. Only resetting the deadline left the old
        # bids in the hash (they live ttl+1h), so a bidder from an hour ago
        # could be elected winner of an auction they never entered.
        if bids:
            await r.delete(key)
            bids = {}
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        await r.hset(key, _EXPIRES_FIELD, expires_at)
    await r.hset(key, participant, json.dumps({
        "bid": bid, "reason": reason, "created_at": now.isoformat(),
    }))
    # Belt-and-braces expiry: window TTL + claim grace.
    await r.expire(key, ttl_seconds + _CLAIM_TTL_S)

    bids, _ = await _load_bids(r, tid, rid, mid)
    lead = await _leader(r, tid, bids)
    assert lead is not None  # we just wrote at least one bid
    return {
        "leader": lead[0],
        "leader_bid": lead[1],
        "window_expires_at": expires_at,
        "fairness_credit": await get_credit(r, tid, participant),
    }


async def try_claim(
    r: Any,
    *,
    tid: str,
    rid: str,
    mid: str,
    claim_payload_factory,
) -> tuple[dict[str, Any] | None, dict[str, dict], bool]:
    """Race for the claim.

    Returns ``(claim_dict, bids, is_fresh)``. ``is_fresh`` is True only for
    the replica whose ``SET NX`` won; everyone else gets the stored claim
    with ``is_fresh=False`` so they can skip side effects (the in-memory
    path returns early on a re-claim WITHOUT re-broadcasting a wake). When
    no bids exist, returns ``(None, {}, False)`` and the caller 404s.

    ``claim_payload_factory(bids, winner, winner_bid, credits)`` builds the
    claim dict; it is invoked ONLY by the SET-NX winner, and only that
    replica applies fairness credits.
    """
    bids, _ = await _load_bids(r, tid, rid, mid)
    if not bids:
        return None, {}, False

    lead = await _leader(r, tid, bids)
    assert lead is not None
    winner, winner_bid = lead

    # Apply credits optimistically ONLY if our SET NX wins (checked below by
    # writing the claim first, then crediting — losers never reach it).
    candidates = sorted(bids)
    ckey = _claim_key(tid, rid, mid)
    credits: dict[str, float] = {}
    payload = claim_payload_factory(bids, winner, winner_bid, credits)
    stored = await r.set(ckey, json.dumps(payload), nx=True, ex=_CLAIM_TTL_S)
    if not stored:
        raw = await r.get(ckey)
        if raw is None:  # claim expired between SET NX and GET — rare
            return None, bids, False
        val = raw.decode() if isinstance(raw, bytes) else raw
        try:
            return json.loads(val), bids, False
        except (json.JSONDecodeError, TypeError):
            return None, bids, False

    # We won the race: apply fairness credits exactly once.
    credit_hash = _credit_key(tid)
    for participant in candidates:
        delta = -1.0 if participant == winner else 0.25
        # HINCRBYFLOAT returns the post-increment value: authoritative for
        # THIS claim. Re-reading with HGET both doubled the round-trips and
        # raced a concurrent claim in another room for the same tenant.
        updated = await r.hincrbyfloat(credit_hash, participant, delta)
        try:
            credits[participant] = float(updated)
        except (TypeError, ValueError):
            credits[participant] = await get_credit(r, tid, participant)
    payload["fairness_credit"] = credits
    await r.set(ckey, json.dumps(payload), ex=_CLAIM_TTL_S)
    return payload, bids, True


async def claim_exists(r: Any, *, tid: str, rid: str, mid: str) -> bool:
    """True once a claim has been written for this message."""
    try:
        return bool(await r.exists(_claim_key(tid, rid, mid)))
    except Exception:
        return False
