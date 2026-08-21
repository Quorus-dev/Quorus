"""Relay-side speaker triage and bid coordination."""

from __future__ import annotations

import json
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.routes import triage_redis as _tr
from quorus.routes.room_auth import require_room_member

router = APIRouter(prefix="/v1", tags=["triage"])
_LEGACY_TENANT = "_legacy"
_DEFAULT_BID_TTL_SECONDS = 5
# H4 fix — bound _bid_windows. Hand-rolled LRU + per-entry TTL because
# cachetools is not in the dependency set. _BID_WINDOW_MAX is a hard cap;
# _BID_WINDOW_TTL is a wall-clock timeout that protects us when an
# orphan window's expires_at field somehow gets stuck in the future.
_BID_WINDOW_MAX = 10_000
_BID_WINDOW_TTL_S = 600.0
_QUESTION_WORDS = re.compile(
    r"\b(who|what|when|where|why|how|can|could|should|would|will|does|do|did|is|are)\b",
    re.IGNORECASE,
)
_MENTION_RE = re.compile(r"@([A-Za-z0-9_-]+)")


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TriageRequest(BaseModel):
    room_id: str = Field(min_length=1, max_length=128)
    message_id: str | None = Field(default=None, max_length=128)
    from_name: str | None = Field(default=None, max_length=64)
    content: str = Field(max_length=20_000)
    message_type: str = Field(default="chat", max_length=32)


class TriageResponse(BaseModel):
    action: Literal["IGNORE", "NOTIFY", "RESPOND"]
    candidates: list[str]
    reason: str
    message_id: str


class BidRequest(BaseModel):
    room_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)
    participant: str = Field(min_length=1, max_length=64)
    bid: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=500)
    ttl_seconds: int = Field(default=_DEFAULT_BID_TTL_SECONDS, ge=1, le=30)

    @field_validator("participant")
    @classmethod
    def valid_participant(cls, value: str) -> str:
        if value.startswith("-"):
            raise ValueError("participant must not start with '-'")
        return value


class BidResponse(BaseModel):
    accepted: bool
    leader: str
    leader_bid: float
    window_expires_at: str
    fairness_credit: float


class ClaimRequest(BaseModel):
    room_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=128)


class ClaimResponse(BaseModel):
    claimed: bool
    winner: str
    bid: float
    claim_token: str
    expires_at: str
    candidates: list[str]
    fairness_credit: dict[str, float]


@dataclass
class _Bid:
    participant: str
    bid: float
    reason: str
    created_at: datetime


@dataclass
class _BidWindow:
    expires_at: datetime
    bids: dict[str, _Bid] = field(default_factory=dict)
    claim: ClaimResponse | None = None
    inserted_at: datetime = field(default_factory=_now)


# H4 fix — LRU-bounded with TTL evict on every read. OrderedDict gives O(1)
# move-to-end on access and O(1) popitem(last=False) on eviction. The
# _BID_WINDOW_TTL_S floor is a belt-and-braces timeout in case some window
# gets stuck.
# A bid at or above this is an explicit @-mention (reflexd scores mentions
# 1.0). Those are ADDRESSED, and sort above every credit-adjusted bid:
# after two wins an agent's credit went negative enough that a teammate's
# 0.3 question-bid beat the 1.0 mention and answered mail addressed to
# someone else (caught by the Stream T gate, 2026-08-21).
MENTION_BID = 1.0

# How long a closed window remains claimable. Long enough for every bidder
# to poll after the window shuts, short enough to stay bounded.
CLAIM_GRACE_S = 60

_bid_windows: "OrderedDict[tuple[str, str, str], _BidWindow]" = OrderedDict()
_fairness_credit: dict[tuple[str, str], float] = {}


def _window_key(tid: str, room_id: str, message_id: str) -> tuple[str, str, str]:
    return tid, room_id, message_id


def _cleanup_expired() -> None:
    """Drop windows whose expires_at is in the past AND have no claim,
    plus any window older than ``_BID_WINDOW_TTL_S`` regardless of claim
    status, plus enforce the LRU cap.
    """
    now = _now()
    ttl_floor = now - timedelta(seconds=_BID_WINDOW_TTL_S)
    # A window stays claimable for CLAIM_GRACE_S after it closes: bidding
    # ends, then the winner is awarded. Dropping it the instant it expired
    # made the "wait for the window" rule unsatisfiable — every claim 404'd.
    claim_cutoff = now - timedelta(seconds=CLAIM_GRACE_S)
    expired: list[tuple[str, str, str]] = []
    for key, window in _bid_windows.items():
        if window.expires_at < claim_cutoff and window.claim is None:
            expired.append(key)
        elif window.inserted_at < ttl_floor:
            # Hard TTL — even claimed windows get evicted eventually so
            # the dict cannot grow unbounded if claim never expires.
            expired.append(key)
    for key in expired:
        _bid_windows.pop(key, None)
    # H4 LRU cap — evict oldest (insertion-order) entries until under cap.
    while len(_bid_windows) > _BID_WINDOW_MAX:
        _bid_windows.popitem(last=False)


def _record_window(key: tuple[str, str, str], window: _BidWindow) -> None:
    """Insert / refresh a window in the LRU."""
    _bid_windows[key] = window
    _bid_windows.move_to_end(key)
    while len(_bid_windows) > _BID_WINDOW_MAX:
        _bid_windows.popitem(last=False)


def _candidate_members(members: dict, sender: str | None) -> list[str]:
    return sorted(name for name in members if name != sender and not name.startswith("_"))


def _classify(
    content: str,
    message_type: str,
    members: dict,
    sender: str | None,
) -> tuple[str, list[str], str]:
    if message_type not in {"chat", "request", "question"}:
        return "IGNORE", [], f"message_type {message_type!r} is not conversational"

    mentioned = [name for name in _MENTION_RE.findall(content) if name in members]
    if mentioned:
        return "RESPOND", sorted(set(mentioned)), "explicit @mention"

    candidates = _candidate_members(members, sender)
    if "?" in content or _QUESTION_WORDS.search(content):
        return "RESPOND", candidates, "question heuristic"
    if candidates:
        return "NOTIFY", candidates, "room member notification"
    return "IGNORE", [], "no candidate participants"


def _broadcast_wake_intent(
    request: Request,
    tid: str,
    room_name: str,
    recipients: list[str],
    payload: dict,
) -> None:
    sse_svc = request.app.state.sse_service
    ts = _now().isoformat()
    for recipient in recipients:
        sse_svc.push(
            tid,
            recipient,
            {
                "id": str(uuid.uuid4()),
                "from_name": "_system",
                "to": recipient,
                "room": room_name,
                "content": json.dumps(payload, separators=(",", ":")),
                "message_type": "wake_intent",
                "timestamp": ts,
            },
        )


@router.post("/triage", response_model=TriageResponse)
async def triage_message(
    body: TriageRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, body.room_id)
    members = room_data.get("members", {})
    message_id = body.message_id or str(uuid.uuid4())
    action, candidates, reason = _classify(
        body.content,
        body.message_type,
        members,
        body.from_name,
    )

    if action != "IGNORE":
        _broadcast_wake_intent(
            request,
            tid,
            room_data.get("name", rid),
            candidates,
            {
                "event": "wake_intent",
                "room_id": rid,
                "message_id": message_id,
                "action": action,
                "candidates": candidates,
                "reason": reason,
            },
        )

    return TriageResponse(
        action=action,
        candidates=candidates,
        reason=reason,
        message_id=message_id,
    )


@router.post("/bid", response_model=BidResponse)
async def submit_bid(
    body: BidRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    if not auth.is_legacy and body.participant != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot bid as another participant")

    tid = _tid(auth)
    rid, _ = await require_room_member(request, auth, tid, body.room_id)

    # R3: shared auction when Redis is configured — multi-worker /
    # multi-replica deploys elect exactly one winner. Falls through to the
    # process-local path otherwise (file-mode relays, unit tests).
    r = _tr.redis_or_none()
    if r is not None:
        snap = await _tr.record_bid(
            r, tid=tid, rid=rid, mid=body.message_id,
            participant=body.participant, bid=body.bid,
            reason=body.reason, ttl_seconds=body.ttl_seconds,
        )
        return BidResponse(
            accepted=True,
            leader=snap["leader"],
            leader_bid=snap["leader_bid"],
            window_expires_at=snap["window_expires_at"],
            fairness_credit=snap["fairness_credit"],
        )

    _cleanup_expired()
    key = _window_key(tid, rid, body.message_id)
    window = _bid_windows.get(key)
    if window is None or window.expires_at < _now():
        window = _BidWindow(
            expires_at=_now() + timedelta(seconds=body.ttl_seconds),
        )
        _record_window(key, window)
    else:
        # Promote LRU recency on each touch.
        _bid_windows.move_to_end(key)

    window.bids[body.participant] = _Bid(
        participant=body.participant,
        bid=body.bid,
        reason=body.reason,
        created_at=_now(),
    )
    leader = max(
        window.bids.values(),
        key=lambda item: (
            item.bid >= MENTION_BID,
            item.bid + _fairness_credit.get((tid, item.participant), 0.0),
            item.created_at,
        ),
    )
    return BidResponse(
        accepted=True,
        leader=leader.participant,
        leader_bid=leader.bid,
        window_expires_at=window.expires_at.isoformat(),
        fairness_credit=_fairness_credit.get((tid, body.participant), 0.0),
    )


@router.post("/claim", response_model=ClaimResponse)
async def claim_winner(
    body: ClaimRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    tid = _tid(auth)
    rid, room_data = await require_room_member(request, auth, tid, body.room_id)

    r = _tr.redis_or_none()
    if r is not None:
        return await _claim_via_redis(r, request, auth, tid, rid, room_data, body)

    _cleanup_expired()
    key = _window_key(tid, rid, body.message_id)
    window = _bid_windows.get(key)
    if window is None or not window.bids:
        raise HTTPException(status_code=404, detail="No bids for message")

    if window.claim is not None:
        return window.claim

    # Wave-5 Fix 8 — only an actual bidder may claim. Without this gate
    # any room member could call /v1/claim against a window they never
    # bid on and walk away with the ``claim_token``, which is a real
    # privilege-escalation vector for downstream tools that trust the
    # token. Admin / legacy auth retain operator-level claim ability.
    if not auth.is_legacy and auth.role != "admin":
        if auth.sub is None or auth.sub not in window.bids:
            raise HTTPException(
                status_code=403,
                detail="not a bidder for this window",
            )

    # Authorization first, then availability: hold the award until bidding
    # closes. Without this the first caller to POST /claim won regardless
    # of its bid — the mentioned agent lost to a teammate that reached the
    # relay a few ms earlier (Stream T gate, 2026-08-21). Callers retry.
    if window.claim is None and window.expires_at > _now():
        raise HTTPException(
            status_code=425,
            detail="bid window still open — retry after it closes",
        )

    winner = max(
        window.bids.values(),
        key=lambda item: (
            item.bid >= MENTION_BID,
            item.bid + _fairness_credit.get((tid, item.participant), 0.0),
            item.created_at,
        ),
    )
    candidates = sorted(window.bids)
    for participant in candidates:
        credit_key = (tid, participant)
        if participant == winner.participant:
            _fairness_credit[credit_key] = _fairness_credit.get(credit_key, 0.0) - 1.0
        else:
            _fairness_credit[credit_key] = _fairness_credit.get(credit_key, 0.0) + 0.25

    expires_at = _now() + timedelta(seconds=_DEFAULT_BID_TTL_SECONDS)
    claim = ClaimResponse(
        claimed=True,
        winner=winner.participant,
        bid=winner.bid,
        claim_token=str(uuid.uuid4()),
        expires_at=expires_at.isoformat(),
        candidates=candidates,
        fairness_credit={
            participant: _fairness_credit[(tid, participant)]
            for participant in candidates
        },
    )
    window.claim = claim

    _broadcast_wake_intent(
        request,
        tid,
        room_data.get("name", rid),
        candidates,
        {
            "event": "claim",
            "room_id": rid,
            "message_id": body.message_id,
            "winner": winner.participant,
            "candidates": candidates,
            "claim_token": claim.claim_token,
        },
    )
    return claim


async def _claim_via_redis(r, request, auth, tid, rid, room_data, body) -> ClaimResponse:
    """R3 claim path — shared single-winner election through Redis."""
    bids, _exp = await _tr._load_bids(r, tid, rid, body.message_id)
    if not bids:
        raise HTTPException(status_code=404, detail="No bids for message")
    # Wave-5 Fix 8 parity: only an actual bidder (or operator) may claim.
    if not auth.is_legacy and auth.role != "admin":
        if auth.sub is None or auth.sub not in bids:
            raise HTTPException(status_code=403, detail="not a bidder for this window")
    if _exp and _exp > _now().isoformat() and not await _tr.claim_exists(
        r, tid=tid, rid=rid, mid=body.message_id,
    ):
        raise HTTPException(
            status_code=425,
            detail="bid window still open — retry after it closes",
        )

    def _payload(bids_now, winner, winner_bid, credits):
        expires_at = _now() + timedelta(seconds=_DEFAULT_BID_TTL_SECONDS)
        return {
            "claimed": True,
            "winner": winner,
            "bid": winner_bid,
            "claim_token": str(uuid.uuid4()),
            "expires_at": expires_at.isoformat(),
            "candidates": sorted(bids_now),
            "fairness_credit": credits,
        }

    payload, bids, is_fresh = await _tr.try_claim(
        r, tid=tid, rid=rid, mid=body.message_id,
        claim_payload_factory=_payload,
    )
    if payload is None:
        # Bids exist (checked above) but the claim record vanished or was
        # unreadable between SET NX and GET. That is transient contention,
        # not "no bids" — say so, so a caller can retry instead of giving up.
        raise HTTPException(
            status_code=409, detail="claim contended — retry",
        )
    claim = ClaimResponse(**payload)
    # Only the replica that actually WON the race broadcasts. The in-memory
    # path returns early on a re-claim without re-broadcasting; without this
    # guard every duplicate /v1/claim re-woke all candidates — the exact
    # duplicate-wake class R3 exists to eliminate.
    if is_fresh:
        _broadcast_wake_intent(
            request, tid, room_data.get("name", rid), claim.candidates,
            {
                "event": "claim",
                "room_id": rid,
                "message_id": body.message_id,
                "winner": claim.winner,
                "candidates": claim.candidates,
                "claim_token": claim.claim_token,
            },
        )
    return claim


def reset_triage_state() -> None:
    """Reset process-local triage state for tests."""
    _bid_windows.clear()
    _fairness_credit.clear()
