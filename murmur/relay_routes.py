"""Relay route handlers — message, room, SSE, webhook, presence, health endpoints.

Extracted from relay.py to keep the app setup module small.
All in-memory state and helpers are imported from relay.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import ipaddress
import json
import os
import re
import socket
import string
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from murmur.auth.middleware import AuthContext, require_identity, verify_auth

logger = structlog.get_logger("murmur.relay_routes")

# ---------------------------------------------------------------------------
# In-memory state (shared with relay.py via imports)
# ---------------------------------------------------------------------------

message_queues: dict[str, list[dict]] = defaultdict(list)
participants: set[str] = set()
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
persistence_lock = asyncio.Lock()
rooms_lock = asyncio.Lock()
message_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
webhooks: dict[str, str] = {}
room_webhooks: dict[str, list[dict]] = defaultdict(list)
rooms: dict[str, dict] = {}
room_history: dict[str, list[dict]] = defaultdict(list)
SSE_QUEUE_MAX = int(os.environ.get("SSE_QUEUE_MAX", "1000"))
sse_queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
presence: dict[str, dict] = {}

MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "1000"))
MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))
MESSAGE_TTL_SECONDS = int(os.environ.get("MESSAGE_TTL_SECONDS", str(24 * 60 * 60)))
MAX_ROOM_HISTORY = int(os.environ.get("MAX_ROOM_HISTORY", "200"))
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "60"))
_rate_buckets: dict[str, list[float]] = defaultdict(list)
RELAY_SECRET = os.environ.get("RELAY_SECRET", "")

# Analytics state
analytics: dict = {
    "total_sent": 0,
    "total_delivered": 0,
    "per_participant": {},
    "hourly_volume": {},
}
_start_time = time.time()

SSE_TOKEN_TTL = int(os.environ.get("SSE_TOKEN_TTL", "300"))
_sse_tokens: dict[str, dict] = {}

INVITE_SECRET = os.environ.get("INVITE_SECRET", "") or RELAY_SECRET
INVITE_TTL = int(os.environ.get("INVITE_TTL", str(24 * 60 * 60)))

WEBHOOK_CONCURRENCY = int(os.environ.get("WEBHOOK_CONCURRENCY", "10"))
_webhook_semaphore = asyncio.Semaphore(WEBHOOK_CONCURRENCY)
_webhook_http_client: httpx.AsyncClient | None = None

MAX_ROOM_MEMBERS = int(os.environ.get("MAX_ROOM_MEMBERS", "50"))

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
MAX_NAME_LENGTH = 64
VALID_MESSAGE_TYPES = {"chat", "claim", "status", "request", "alert", "sync"}


def _validate_name(value: str) -> str:
    if not value or len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"Name must be 1-{MAX_NAME_LENGTH} characters")
    if not _NAME_RE.match(value):
        raise ValueError("Name must contain only alphanumeric characters, hyphens, and underscores")
    return value


def _check_rate_limit(sender: str) -> bool:
    """Return True if the sender is within the rate limit, False if exceeded."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    bucket = _rate_buckets[sender]
    _rate_buckets[sender] = [t for t in bucket if t > cutoff]
    if len(_rate_buckets[sender]) >= RATE_LIMIT_MAX:
        return False
    _rate_buckets[sender].append(now)
    return True


def _track_send(sender: str):
    analytics["total_sent"] += 1
    if sender not in analytics["per_participant"]:
        analytics["per_participant"][sender] = {"sent": 0, "received": 0}
    analytics["per_participant"][sender]["sent"] += 1
    hour_key = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
    analytics["hourly_volume"][hour_key] = analytics["hourly_volume"].get(hour_key, 0) + 1


def _track_delivery(recipient: str, count: int):
    if count == 0:
        return
    analytics["total_delivered"] += count
    if recipient not in analytics["per_participant"]:
        analytics["per_participant"][recipient] = {"sent": 0, "received": 0}
    analytics["per_participant"][recipient]["received"] += count


def _prune_hourly_volume():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    analytics["hourly_volume"] = {
        k: v for k, v in analytics["hourly_volume"].items() if k >= cutoff
    }


def _logical_message_units() -> list[dict]:
    units: dict[tuple[str, str, str], dict] = {}
    for recipient, msgs in message_queues.items():
        for msg in msgs:
            if "chunk_group" in msg:
                key = (recipient, "chunk_group", msg["chunk_group"])
            else:
                key = (recipient, "message", msg["id"])
            unit = units.setdefault(
                key,
                {"recipient": recipient, "timestamp": msg["timestamp"], "ids": set(), "size": 0},
            )
            if msg["timestamp"] < unit["timestamp"]:
                unit["timestamp"] = msg["timestamp"]
            unit["ids"].add(msg["id"])
            unit["size"] += 1
    return sorted(units.values(), key=lambda unit: unit["timestamp"])


def _count_pending_messages() -> int:
    return len(_logical_message_units())


def _expire_stale_messages() -> int:
    if MESSAGE_TTL_SECONDS <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TTL_SECONDS)).isoformat()
    expired = 0
    for recipient in message_queues:
        before = len(message_queues[recipient])
        message_queues[recipient] = [
            m for m in message_queues[recipient] if m.get("timestamp", "") >= cutoff
        ]
        expired += before - len(message_queues[recipient])
    if expired:
        logger.info("Expired %d stale messages (TTL=%ds)", expired, MESSAGE_TTL_SECONDS)
    return expired


def _trim_messages():
    _expire_stale_messages()
    total_entries = sum(len(msgs) for msgs in message_queues.values())
    if total_entries <= MAX_MESSAGES:
        return
    to_remove = total_entries - MAX_MESSAGES
    remove_ids = set()
    removed_entries = 0
    removed_units = 0
    for unit in _logical_message_units():
        remove_ids.update(unit["ids"])
        removed_entries += unit["size"]
        removed_units += 1
        if removed_entries >= to_remove:
            break
    logger.info(
        "Trimming %d logical messages (%d stored entries) to stay within cap %d",
        removed_units, removed_entries, MAX_MESSAGES,
    )
    for recipient in message_queues:
        message_queues[recipient] = [
            m for m in message_queues[recipient] if m["id"] not in remove_ids
        ]


def _chunk_content(content: str) -> list[str]:
    chunks = []
    current = []
    current_size = 0
    for char in content:
        char_size = len(char.encode("utf-8"))
        if current and current_size + char_size > MAX_MESSAGE_SIZE:
            chunks.append("".join(current))
            current = [char]
            current_size = char_size
        else:
            current.append(char)
            current_size += char_size
    if current:
        chunks.append("".join(current))
    return chunks


def _sse_push(recipient: str, message: dict) -> None:
    for q in sse_queues.get(recipient, []):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("SSE queue full for %s, dropping message", recipient)


def _get_webhook_client() -> httpx.AsyncClient:
    global _webhook_http_client
    if _webhook_http_client is None or _webhook_http_client.is_closed:
        _webhook_http_client = httpx.AsyncClient(timeout=5)
    return _webhook_http_client


async def _deliver_webhook(recipient: str, callback_url: str, message: dict) -> None:
    async with _webhook_semaphore:
        try:
            client = _get_webhook_client()
            resp = await client.post(callback_url, json=message)
            resp.raise_for_status()
            logger.info("Webhook delivered to %s for %s", callback_url, recipient)
        except Exception:
            logger.warning(
                "Webhook delivery failed for %s at %s", recipient, callback_url, exc_info=True,
            )


def _notify_webhook(recipient: str, message: dict) -> None:
    callback_url = webhooks.get(recipient)
    if not callback_url:
        return
    asyncio.create_task(_deliver_webhook(recipient, callback_url, message))


def _notify_room_webhooks(room_id: str, message: dict) -> None:
    hooks = room_webhooks.get(room_id, [])
    for hook in hooks:
        asyncio.create_task(_deliver_webhook(f"room:{room_id}", hook["url"], message))


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _validate_webhook_url_sync(callback_url: str) -> str:
    parsed = urlparse(callback_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="callback_url must be an http(s) URL")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="callback_url must not include credentials")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise HTTPException(status_code=400, detail="callback_url must target a public host")
    try:
        ip = ipaddress.ip_address(host)
        if _is_private_ip(ip):
            raise HTTPException(status_code=400, detail="callback_url must target a public host")
        return callback_url
    except ValueError:
        pass
    if "." not in host:
        raise HTTPException(status_code=400, detail="callback_url must target a public host")
    return callback_url


async def _validate_webhook_callback_url(callback_url: str) -> str:
    url = _validate_webhook_url_sync(callback_url)
    parsed = urlparse(url)
    host = parsed.hostname.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        return url
    except ValueError:
        pass
    try:
        addrinfo = await asyncio.to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="callback_url hostname could not be resolved")
    if not addrinfo:
        raise HTTPException(status_code=400, detail="callback_url hostname could not be resolved")
    for family, _, _, _, sockaddr in addrinfo:
        resolved_ip = ipaddress.ip_address(sockaddr[0])
        if _is_private_ip(resolved_ip):
            raise HTTPException(
                status_code=400, detail="callback_url must not resolve to a private address",
            )
    return url


def _find_room_by_name(name: str) -> Optional[str]:
    for rid, room in rooms.items():
        if room["name"] == name:
            return rid
    return None


def _resolve_room(room_id_or_name: str) -> tuple[str, dict]:
    room = rooms.get(room_id_or_name)
    if room:
        return room_id_or_name, room
    rid = _find_room_by_name(room_id_or_name)
    if rid:
        return rid, rooms[rid]
    raise HTTPException(status_code=404, detail="Room not found")


def _reassemble_chunks(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    non_chunked = []
    chunk_groups: dict[str, list[dict]] = defaultdict(list)
    for msg in messages:
        if "chunk_group" in msg:
            chunk_groups[msg["chunk_group"]].append(msg)
        else:
            non_chunked.append(msg)
    ready = list(non_chunked)
    held_back = []
    for group_id, chunks in chunk_groups.items():
        expected = chunks[0]["chunk_total"]
        if len(chunks) == expected:
            chunks.sort(key=lambda c: c["chunk_index"])
            reassembled = {
                "id": chunks[0]["id"],
                "from_name": chunks[0]["from_name"],
                "to": chunks[0]["to"],
                "content": "".join(c["content"] for c in chunks),
                "timestamp": chunks[0]["timestamp"],
            }
            ready.append(reassembled)
        else:
            held_back.extend(chunks)
    ready.sort(key=lambda m: m["timestamp"])
    return ready, held_back


# Persistence helpers — imported from relay.py at runtime to avoid circular imports
_persist_state = None
_snapshot_state = None


def set_persistence_hooks(persist_fn, snapshot_fn):
    """Called by relay.py to inject persistence functions."""
    global _persist_state, _snapshot_state
    _persist_state = persist_fn
    _snapshot_state = snapshot_fn


async def _persist():
    """Persist state if a hook was set."""
    if _persist_state:
        await _persist_state()


# Invite token helpers
def _make_invite_token(room_name: str) -> str:
    expires = int(time.time()) + INVITE_TTL
    payload = f"{room_name}:{expires}"
    sig = hmac_mod.new(INVITE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify_invite_token(token: str, room_name: str) -> bool:
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        return False
    claimed_room, expires_str, sig = parts
    if claimed_room != room_name:
        return False
    try:
        expires = int(expires_str)
    except ValueError:
        return False
    if time.time() > expires:
        return False
    expected_payload = f"{claimed_room}:{expires_str}"
    expected_sig = hmac_mod.new(
        INVITE_SECRET.encode(), expected_payload.encode(), hashlib.sha256
    ).hexdigest()
    return hmac_mod.compare_digest(sig, expected_sig)


def reset_state():
    """Reset in-memory state between tests."""
    global _start_time, persistence_lock, rooms_lock, _webhook_semaphore, _webhook_http_client
    message_queues.clear()
    participants.clear()
    locks.clear()
    persistence_lock = asyncio.Lock()
    rooms_lock = asyncio.Lock()
    _webhook_semaphore = asyncio.Semaphore(WEBHOOK_CONCURRENCY)
    _webhook_http_client = None
    message_events.clear()
    webhooks.clear()
    room_webhooks.clear()
    rooms.clear()
    room_history.clear()
    sse_queues.clear()
    presence.clear()
    _rate_buckets.clear()
    _sse_tokens.clear()
    analytics["total_sent"] = 0
    analytics["total_delivered"] = 0
    analytics["per_participant"].clear()
    analytics["hourly_volume"].clear()
    _start_time = time.time()


def snapshot_state() -> dict:
    return {
        "messages": {k: list(v) for k, v in message_queues.items()},
        "participants": sorted(participants),
        "analytics": {
            "total_sent": analytics["total_sent"],
            "total_delivered": analytics["total_delivered"],
            "per_participant": dict(analytics["per_participant"]),
            "hourly_volume": dict(analytics["hourly_volume"]),
        },
        "rooms": {
            rid: {
                "name": r["name"],
                "created_by": r["created_by"],
                "members": sorted(r["members"]),
                "member_roles": r.get("member_roles", {}),
                "created_at": r["created_at"],
            }
            for rid, r in rooms.items()
        },
        "room_history": {rid: list(msgs) for rid, msgs in room_history.items()},
        "room_webhooks": {rid: list(hooks) for rid, hooks in room_webhooks.items()},
        "presence": dict(presence),
        "webhooks": dict(webhooks),
    }


def apply_loaded_state(data: dict) -> None:
    for recipient, msgs in data.get("messages", {}).items():
        message_queues[recipient] = msgs
    for p in data.get("participants", []):
        participants.add(p)
    saved_analytics = data.get("analytics", {})
    analytics["total_sent"] = saved_analytics.get("total_sent", 0)
    analytics["total_delivered"] = saved_analytics.get("total_delivered", 0)
    analytics["per_participant"] = saved_analytics.get("per_participant", {})
    analytics["hourly_volume"] = saved_analytics.get("hourly_volume", {})
    for rid, rdata in data.get("rooms", {}).items():
        rooms[rid] = {
            "name": rdata["name"],
            "created_by": rdata["created_by"],
            "members": set(rdata.get("members", [])),
            "member_roles": rdata.get("member_roles", {}),
            "created_at": rdata["created_at"],
        }
    for rid, msgs in data.get("room_history", {}).items():
        room_history[rid] = msgs
    for rid, hooks in data.get("room_webhooks", {}).items():
        room_webhooks[rid] = hooks
    for name, pdata in data.get("presence", {}).items():
        presence[name] = pdata
    for name, url in data.get("webhooks", {}).items():
        webhooks[name] = url


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    from_name: str
    to: str
    content: str

    @field_validator("from_name", "to")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class RegisterWebhookRequest(BaseModel):
    instance_name: str
    callback_url: str

    @field_validator("instance_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class CreateRoomRequest(BaseModel):
    name: str
    created_by: str

    @field_validator("name", "created_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class RoomMessageRequest(BaseModel):
    from_name: str
    content: str
    message_type: str = "chat"
    reply_to: str | None = None

    @field_validator("from_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("message_type")
    @classmethod
    def check_message_type(cls, v: str) -> str:
        if v not in VALID_MESSAGE_TYPES:
            allowed = ", ".join(sorted(VALID_MESSAGE_TYPES))
            raise ValueError(f"message_type must be one of: {allowed}")
        return v


VALID_ROLES = {"builder", "reviewer", "researcher", "pm", "qa", "member"}


class JoinLeaveRequest(BaseModel):
    participant: str
    role: str = "member"

    @field_validator("participant")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            allowed = ", ".join(sorted(VALID_ROLES))
            raise ValueError(f"role must be one of: {allowed}")
        return v


class KickRequest(BaseModel):
    participant: str
    requested_by: str

    @field_validator("participant", "requested_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class RenameRoomRequest(BaseModel):
    new_name: str
    requested_by: str

    @field_validator("new_name", "requested_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class DestroyRoomRequest(BaseModel):
    requested_by: str

    @field_validator("requested_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class RoomWebhookRequest(BaseModel):
    callback_url: str
    registered_by: str

    @field_validator("registered_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class HeartbeatRequest(BaseModel):
    instance_name: str
    status: str = "active"
    room: str = ""

    @field_validator("instance_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("status")
    @classmethod
    def check_status(cls, v: str) -> str:
        if v not in {"active", "idle", "busy"}:
            raise ValueError("status must be one of: active, idle, busy")
        return v


class InviteJoinRequest(BaseModel):
    participant: str
    token: str

    @field_validator("participant")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get("/health")
async def health():
    """Check relay health including Postgres connectivity."""
    from murmur.storage.postgres import check_connection

    checks: dict = {"status": "ok"}

    # Check Postgres if configured
    pg_ok = await check_connection()
    if pg_ok:
        checks["postgres"] = "connected"
    else:
        # Fall back to persistence file check
        persist_dir = os.path.dirname(os.path.abspath(MESSAGES_FILE)) or "."
        if not os.access(persist_dir, os.W_OK):
            checks["status"] = "degraded"
            checks["persistence"] = "directory not writable"
        else:
            checks["persistence"] = "ok"

    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(checks, status_code=status_code)


@router.get("/health/detailed")
async def health_detailed(auth: AuthContext = Depends(verify_auth)):
    total_msgs = sum(len(q) for q in message_queues.values())
    total_sse = sum(len(q) for q in sse_queues.values())
    online_agents = sum(
        1 for p in presence.values()
        if (datetime.now(timezone.utc)
            - datetime.fromisoformat(p["last_heartbeat"])
            ).total_seconds() < HEARTBEAT_TIMEOUT
    )
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _start_time),
        "rooms": len(rooms),
        "participants": len(participants),
        "pending_messages": total_msgs,
        "active_sse_connections": total_sse,
        "online_agents": online_agents,
        "total_sent": analytics["total_sent"],
        "total_delivered": analytics["total_delivered"],
    }


@router.post("/messages")
async def send_message(msg: SendMessageRequest, auth: AuthContext = Depends(verify_auth)):
    # Server-side identity: use JWT sub, fall back to client-supplied for legacy
    sender = auth.sub or msg.from_name
    if auth.sub and msg.from_name != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot send as another user")
    msg.from_name = sender
    if not _check_rate_limit(msg.from_name):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    timestamp = datetime.now(timezone.utc).isoformat()
    content = msg.content

    if len(content.encode("utf-8")) > MAX_MESSAGE_SIZE:
        chunk_group = str(uuid.uuid4())
        chunks = _chunk_content(content)
        chunk_total = len(chunks)
        if chunk_total > MAX_MESSAGES:
            raise HTTPException(
                status_code=413,
                detail="Message is too large for the configured storage limit",
            )
        messages = []
        for idx, chunk_content in enumerate(chunks):
            messages.append({
                "id": str(uuid.uuid4()),
                "from_name": msg.from_name,
                "to": msg.to,
                "room": None,
                "content": chunk_content,
                "timestamp": timestamp,
                "chunk_group": chunk_group,
                "chunk_index": idx,
                "chunk_total": chunk_total,
            })
        async with locks[msg.to]:
            message_queues[msg.to].extend(messages)
            participants.update({msg.from_name, msg.to})
            _track_send(msg.from_name)
            _trim_messages()
            await _persist()
        message_events[msg.to].set()
        reassembled = {
            "id": messages[0]["id"],
            "from_name": msg.from_name,
            "to": msg.to,
            "content": content,
            "timestamp": timestamp,
        }
        _sse_push(msg.to, reassembled)
        logger.info(
            "Message chunked into %d parts (%s -> %s, group %s)",
            chunk_total, msg.from_name, msg.to, chunk_group,
        )
        _notify_webhook(msg.to, reassembled)
        return {"id": messages[0]["id"], "timestamp": timestamp}
    else:
        message = {
            "id": str(uuid.uuid4()),
            "from_name": msg.from_name,
            "to": msg.to,
            "room": None,
            "content": content,
            "timestamp": timestamp,
        }
        async with locks[msg.to]:
            message_queues[msg.to].append(message)
            participants.update({msg.from_name, msg.to})
            _track_send(msg.from_name)
            _trim_messages()
            await _persist()
        message_events[msg.to].set()
        _sse_push(msg.to, message)
        logger.info("Message %s: %s -> %s", message["id"], msg.from_name, msg.to)
        _notify_webhook(msg.to, message)
        return {"id": message["id"], "timestamp": message["timestamp"]}


@router.post("/stream/token")
async def create_sse_token(request: Request, auth: AuthContext = Depends(verify_auth)):
    body = await request.json()
    recipient = body.get("recipient", "")
    if not recipient:
        raise HTTPException(status_code=400, detail="recipient is required")
    # JWT users can only create tokens for themselves
    if auth.sub and recipient != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot create SSE token for another user",
        )
    token = str(uuid.uuid4())
    _sse_tokens[token] = {
        "recipient": recipient,
        "expires": time.time() + SSE_TOKEN_TTL,
    }
    return {"token": token, "expires_in": SSE_TOKEN_TTL}


def _verify_sse_token(token: str, recipient: str) -> bool:
    entry = _sse_tokens.get(token)
    if not entry:
        return False
    if time.time() > entry["expires"]:
        _sse_tokens.pop(token, None)
        return False
    return entry["recipient"] == recipient


@router.get("/stream/{recipient}")
async def stream_messages(recipient: str, token: str = ""):
    if not (_verify_sse_token(token, recipient) or hmac_mod.compare_digest(token, RELAY_SECRET)):
        raise HTTPException(status_code=401, detail="Invalid token")

    q: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAX)
    sse_queues[recipient].append(q)
    logger.info("SSE client connected for %s", recipient)

    async def event_generator():
        try:
            connected = json.dumps({
                "participant": recipient,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            yield f"event: connected\ndata: {connected}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"event: message\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in sse_queues[recipient]:
                sse_queues[recipient].remove(q)
            logger.info("SSE client disconnected for %s", recipient)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/messages/{recipient}")
async def get_messages(recipient: str, auth: AuthContext = Depends(verify_auth), wait: int = 0):
    require_identity(auth, recipient)
    wait = min(max(wait, 0), 60)
    async with locks[recipient]:
        expired = _expire_stale_messages()
        all_msgs = list(message_queues[recipient])
        ready, held_back = _reassemble_chunks(all_msgs)
        message_queues[recipient] = held_back
        message_events[recipient].clear()
        if ready:
            _track_delivery(recipient, len(ready))
        if ready or expired:
            await _persist()
    if not ready and wait > 0:
        try:
            await asyncio.wait_for(message_events[recipient].wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        async with locks[recipient]:
            expired = _expire_stale_messages()
            all_msgs = list(message_queues[recipient])
            ready, held_back = _reassemble_chunks(all_msgs)
            message_queues[recipient] = held_back
            message_events[recipient].clear()
            if ready:
                _track_delivery(recipient, len(ready))
            if ready or expired:
                await _persist()
    logger.info(
        "Fetched %d messages for %s (%d chunks held back)", len(ready), recipient, len(held_back),
    )
    return ready


@router.get("/messages/{recipient}/peek")
async def peek_messages(recipient: str, auth: AuthContext = Depends(verify_auth)):
    require_identity(auth, recipient)
    count = len(message_queues.get(recipient, []))
    return {"count": count, "recipient": recipient}


@router.get("/participants")
async def list_participants_endpoint(auth: AuthContext = Depends(verify_auth)):
    return sorted(participants)


@router.post("/webhooks")
async def register_webhook(req: RegisterWebhookRequest, auth: AuthContext = Depends(verify_auth)):
    # JWT users can only register webhooks for themselves
    if auth.sub and req.instance_name != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot register webhook for another user",
        )
    callback_url = await _validate_webhook_callback_url(req.callback_url)
    webhooks[req.instance_name] = callback_url
    participants.add(req.instance_name)
    await _persist()
    logger.info("Webhook registered: %s -> %s", req.instance_name, callback_url)
    return {"status": "registered"}


@router.delete("/webhooks/{instance_name}")
async def delete_webhook(instance_name: str, auth: AuthContext = Depends(verify_auth)):
    # JWT users can only delete their own webhooks
    if auth.sub and instance_name != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot delete webhook for another user",
        )
    webhooks.pop(instance_name, None)
    await _persist()
    logger.info("Webhook removed: %s", instance_name)
    return {"status": "removed"}


@router.post("/rooms")
async def create_room(req: CreateRoomRequest, auth: AuthContext = Depends(verify_auth)):
    # Server-side identity: derive created_by from JWT, fall back to client-supplied
    creator = auth.sub or req.created_by
    if auth.sub and req.created_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot create room as another user")
    req.created_by = creator
    async with rooms_lock:
        if _find_room_by_name(req.name):
            raise HTTPException(status_code=409, detail="Room name already exists")
        room_id = str(uuid.uuid4())
        room = {
            "name": req.name,
            "created_by": req.created_by,
            "members": {req.created_by},
            "member_roles": {req.created_by: "builder"},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        rooms[room_id] = room
        participants.add(req.created_by)
    await _persist()
    logger.info("Room created: %s (%s) by %s", req.name, room_id, req.created_by)
    return {
        "id": room_id,
        "name": room["name"],
        "members": sorted(room["members"]),
        "member_roles": room.get("member_roles", {}),
        "created_at": room["created_at"],
    }


@router.get("/rooms")
async def list_rooms(auth: AuthContext = Depends(verify_auth)):
    return [
        {
            "id": rid,
            "name": room["name"],
            "members": sorted(room["members"]),
            "member_roles": room.get("member_roles", {}),
            "created_at": room["created_at"],
        }
        for rid, room in rooms.items()
    ]


@router.get("/rooms/{room_id}")
async def get_room(room_id: str, auth: AuthContext = Depends(verify_auth)):
    rid, room = _resolve_room(room_id)
    return {
        "id": rid,
        "name": room["name"],
        "members": sorted(room["members"]),
        "member_roles": room.get("member_roles", {}),
        "created_at": room["created_at"],
    }


@router.post("/rooms/{room_id}/join")
async def join_room(room_id: str, req: JoinLeaveRequest, auth: AuthContext = Depends(verify_auth)):
    """Add a participant to a room with an optional role."""
    # JWT users can only join as themselves
    participant = auth.sub or req.participant
    if auth.sub and req.participant != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot join room as another user")
    req.participant = participant
    async with rooms_lock:
        room_id, room = _resolve_room(room_id)
        if len(room["members"]) >= MAX_ROOM_MEMBERS:
            raise HTTPException(
                status_code=400,
                detail=f"Room is at maximum capacity ({MAX_ROOM_MEMBERS} members)",
            )
        room["members"].add(req.participant)
        if "member_roles" not in room:
            room["member_roles"] = {}
        room["member_roles"][req.participant] = req.role
        participants.add(req.participant)
    await _persist()
    return {"status": "joined", "role": req.role}


@router.post("/rooms/{room_id}/leave")
async def leave_room(room_id: str, req: JoinLeaveRequest, auth: AuthContext = Depends(verify_auth)):
    # JWT users can only leave as themselves
    participant = auth.sub or req.participant
    if auth.sub and req.participant != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot leave room as another user")
    req.participant = participant
    async with rooms_lock:
        room_id, room = _resolve_room(room_id)
        room["members"].discard(req.participant)
        room.get("member_roles", {}).pop(req.participant, None)
    await _persist()
    return {"status": "left"}


@router.post("/rooms/{room_id}/kick")
async def kick_from_room(room_id: str, req: KickRequest, auth: AuthContext = Depends(verify_auth)):
    # Derive requested_by from JWT
    requested_by = auth.sub or req.requested_by
    if auth.sub and req.requested_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot kick as another user")
    req.requested_by = requested_by
    room_id, room = _resolve_room(room_id)
    # JWT admins bypass creator check; legacy and JWT users must be creator
    is_admin = not auth.is_legacy and auth.role == "admin"
    if not is_admin and req.requested_by != room["created_by"]:
        raise HTTPException(
            status_code=403, detail="Only the room creator or admin can kick members",
        )
    if req.participant == room["created_by"]:
        raise HTTPException(status_code=400, detail="Cannot kick the room creator")
    if req.participant not in room["members"]:
        raise HTTPException(status_code=404, detail="Participant not in room")
    room["members"].discard(req.participant)
    room.get("member_roles", {}).pop(req.participant, None)
    await _persist()
    logger.info("Kicked %s from room %s by %s", req.participant, room["name"], req.requested_by)
    return {"status": "kicked", "participant": req.participant}


@router.delete("/rooms/{room_id}")
async def destroy_room(
    room_id: str, req: DestroyRoomRequest, auth: AuthContext = Depends(verify_auth),
):
    # Derive requested_by from JWT
    requested_by = auth.sub or req.requested_by
    if auth.sub and req.requested_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot destroy room as another user")
    req.requested_by = requested_by
    room_id, room = _resolve_room(room_id)
    is_admin = not auth.is_legacy and auth.role == "admin"
    if not is_admin and req.requested_by != room["created_by"]:
        raise HTTPException(
            status_code=403,
            detail="Only the room creator or admin can destroy the room",
        )
    room_name = room["name"]
    del rooms[room_id]
    room_history.pop(room_id, None)
    room_webhooks.pop(room_id, None)
    await _persist()
    logger.info("Room %s (%s) destroyed by %s", room_name, room_id, req.requested_by)
    return {"status": "destroyed", "room": room_name}


@router.patch("/rooms/{room_id}")
async def rename_room(
    room_id: str, req: RenameRoomRequest, auth: AuthContext = Depends(verify_auth),
):
    # Derive requested_by from JWT
    requested_by = auth.sub or req.requested_by
    if auth.sub and req.requested_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot rename room as another user")
    req.requested_by = requested_by
    room_id, room = _resolve_room(room_id)
    is_admin = not auth.is_legacy and auth.role == "admin"
    if not is_admin and req.requested_by != room["created_by"]:
        raise HTTPException(
            status_code=403,
            detail="Only the room creator or admin can rename the room",
        )
    if _find_room_by_name(req.new_name):
        raise HTTPException(status_code=409, detail="Room name already exists")
    old_name = room["name"]
    room["name"] = req.new_name
    for msg in room_history.get(room_id, []):
        msg["room"] = req.new_name
    await _persist()
    logger.info("Room renamed: %s -> %s by %s", old_name, req.new_name, req.requested_by)
    return {"status": "renamed", "old_name": old_name, "new_name": req.new_name}


@router.post("/rooms/{room_id}/webhooks")
async def register_room_webhook(
    room_id: str, req: RoomWebhookRequest, auth: AuthContext = Depends(verify_auth),
):
    # Derive registered_by from JWT
    registered_by = auth.sub or req.registered_by
    if auth.sub and req.registered_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot register webhook as another user")
    req.registered_by = registered_by
    room_id, room = _resolve_room(room_id)
    callback_url = await _validate_webhook_callback_url(req.callback_url)
    existing_urls = {h["url"] for h in room_webhooks[room_id]}
    if callback_url in existing_urls:
        raise HTTPException(status_code=409, detail="Webhook URL already registered for this room")
    room_webhooks[room_id].append({"url": callback_url, "registered_by": req.registered_by})
    await _persist()
    logger.info(
        "Room webhook registered: %s -> %s by %s", room["name"], callback_url, req.registered_by,
    )
    return {"status": "registered", "room": room["name"], "callback_url": callback_url}


@router.get("/rooms/{room_id}/webhooks")
async def list_room_webhooks(room_id: str, auth: AuthContext = Depends(verify_auth)):
    room_id, room = _resolve_room(room_id)
    return room_webhooks.get(room_id, [])


@router.delete("/rooms/{room_id}/webhooks")
async def delete_room_webhook(
    room_id: str, req: RoomWebhookRequest, auth: AuthContext = Depends(verify_auth),
):
    # Derive registered_by from JWT
    registered_by = auth.sub or req.registered_by
    if auth.sub and req.registered_by != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot delete webhook as another user")
    req.registered_by = registered_by
    room_id, room = _resolve_room(room_id)
    hooks = room_webhooks.get(room_id, [])
    before = len(hooks)
    room_webhooks[room_id] = [h for h in hooks if h["url"] != req.callback_url]
    if len(room_webhooks[room_id]) == before:
        raise HTTPException(status_code=404, detail="Webhook URL not found for this room")
    await _persist()
    logger.info("Room webhook removed: %s -> %s", room["name"], req.callback_url)
    return {"status": "removed", "room": room["name"], "callback_url": req.callback_url}


@router.post("/rooms/{room_id}/messages")
async def send_room_message(
    room_id: str, msg: RoomMessageRequest, auth: AuthContext = Depends(verify_auth),
):
    # Server-side identity: derive from_name from JWT
    sender = auth.sub or msg.from_name
    if auth.sub and msg.from_name != auth.sub:
        raise HTTPException(status_code=403, detail="Cannot send as another user")
    msg.from_name = sender
    if not _check_rate_limit(msg.from_name):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    room_id, room = _resolve_room(room_id)
    if msg.from_name not in room["members"]:
        raise HTTPException(status_code=403, detail="Not a member of this room")
    if len(msg.content.encode("utf-8")) > MAX_MESSAGE_SIZE:
        raise HTTPException(status_code=413, detail="Message content exceeds maximum size")

    timestamp = datetime.now(timezone.utc).isoformat()
    message_id = str(uuid.uuid4())
    recipients = set(room["members"])

    for recipient in recipients:
        fan_out_msg = {
            "id": str(uuid.uuid4()),
            "from_name": msg.from_name,
            "to": recipient,
            "room": room["name"],
            "content": msg.content,
            "message_type": msg.message_type,
            "timestamp": timestamp,
            "reply_to": msg.reply_to,
        }
        async with locks[recipient]:
            message_queues[recipient].append(fan_out_msg)
        message_events[recipient].set()
        _sse_push(recipient, fan_out_msg)
        _notify_webhook(recipient, fan_out_msg)

    history_msg = {
        "id": message_id,
        "from_name": msg.from_name,
        "room": room["name"],
        "content": msg.content,
        "message_type": msg.message_type,
        "timestamp": timestamp,
        "reply_to": msg.reply_to,
    }
    room_history[room_id].append(history_msg)
    if len(room_history[room_id]) > MAX_ROOM_HISTORY:
        room_history[room_id] = room_history[room_id][-MAX_ROOM_HISTORY:]

    participants.add(msg.from_name)
    _track_send(msg.from_name)
    _trim_messages()
    await _persist()
    _notify_room_webhooks(room_id, history_msg)

    logger.info(
        "Room message %s in %s: %s -> %d recipients",
        message_id, room["name"], msg.from_name, len(recipients),
    )
    return {"id": message_id, "timestamp": timestamp}


@router.get("/rooms/{room_id}/history")
async def get_room_history(room_id: str, auth: AuthContext = Depends(verify_auth), limit: int = 50):
    rid, room = _resolve_room(room_id)
    limit = min(max(limit, 1), MAX_ROOM_HISTORY)
    msgs = room_history.get(rid, [])
    return msgs[-limit:]


@router.get("/rooms/{room_id}/search")
async def search_room_history(
    room_id: str, auth: AuthContext = Depends(verify_auth),
    q: str = "", sender: str = "", message_type: str = "", limit: int = 50,
):
    rid, room = _resolve_room(room_id)
    msgs = room_history.get(rid, [])
    results = []
    q_lower = q.lower()
    for msg in msgs:
        if q and q_lower not in msg.get("content", "").lower():
            continue
        if sender and msg.get("from_name", "") != sender:
            continue
        if message_type and msg.get("message_type", "") != message_type:
            continue
        results.append(msg)
    limit = min(max(limit, 1), MAX_ROOM_HISTORY)
    return results[-limit:]


@router.post("/heartbeat")
async def heartbeat(req: HeartbeatRequest, auth: AuthContext = Depends(verify_auth)):
    require_identity(auth, req.instance_name)
    now = datetime.now(timezone.utc).isoformat()
    name = req.instance_name
    existing = presence.get(name)
    uptime_start = existing["uptime_start"] if existing else now
    presence[name] = {
        "last_heartbeat": now,
        "status": req.status,
        "room": req.room,
        "uptime_start": uptime_start,
    }
    participants.add(name)
    await _persist()
    logger.info("Heartbeat from %s (status=%s)", name, req.status)
    return {"status": "ok", "timestamp": now}


@router.get("/presence")
async def get_presence(auth: AuthContext = Depends(verify_auth)):
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=HEARTBEAT_TIMEOUT)).isoformat()
    result = []
    for name, p in presence.items():
        online = p["last_heartbeat"] >= cutoff
        result.append({
            "name": name,
            "online": online,
            "status": p["status"] if online else "offline",
            "room": p.get("room", ""),
            "last_heartbeat": p["last_heartbeat"],
            "uptime_start": p["uptime_start"],
        })
    result.sort(key=lambda x: (not x["online"], x["name"]))
    return result


@router.get("/analytics")
async def get_analytics(auth: AuthContext = Depends(verify_auth)):
    if _expire_stale_messages():
        await _persist()
    _prune_hourly_volume()
    pending = _count_pending_messages()
    hourly = [
        {"hour": k, "count": v} for k, v in sorted(analytics["hourly_volume"].items())
    ]
    return {
        "total_messages_sent": analytics["total_sent"],
        "total_messages_delivered": analytics["total_delivered"],
        "messages_pending": pending,
        "participants": analytics["per_participant"],
        "hourly_volume": hourly,
        "uptime_seconds": round(time.time() - _start_time),
    }


# ---------------------------------------------------------------------------
# Invite endpoints (public — no auth required)
# ---------------------------------------------------------------------------

_INVITE_TMPL = string.Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Join $room_name — Murmur</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;
display:flex;justify-content:center;align-items:center;min-height:100vh}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;
padding:2.5rem;max-width:420px;width:90%;text-align:center}
h1{font-size:1.5rem;margin-bottom:.5rem}
.room{color:#58a6ff;font-size:1.2rem;margin-bottom:1.5rem}
input{width:100%;padding:.75rem 1rem;border:1px solid #30363d;
border-radius:8px;background:#0d1117;color:#e6edf3;font-size:1rem;
margin-bottom:1rem}
input:focus{outline:none;border-color:#58a6ff}
button{width:100%;padding:.75rem;border:none;border-radius:8px;
background:#238636;color:#fff;font-size:1rem;cursor:pointer;
font-weight:600}
button:hover{background:#2ea043}
button:disabled{opacity:.5;cursor:not-allowed}
.msg{margin-top:1rem;padding:.75rem;border-radius:8px;font-size:.9rem}
.ok{background:#0d2818;border:1px solid #238636;color:#3fb950}
.err{background:#2d1117;border:1px solid #f85149;color:#f85149}
.cli{margin-top:1.5rem;text-align:left;font-size:.8rem;color:#8b949e}
code{background:#0d1117;padding:.2rem .4rem;border-radius:4px;
font-size:.85rem;color:#e6edf3}
</style>
</head>
<body>
<div class="card">
<h1>You've been invited to</h1>
<div class="room">$room_name</div>
<form id="f">
<input id="name" placeholder="Your name (e.g. aarya-agent-1)"
 required autocomplete="off">
<button type="submit">Join Room</button>
</form>
<div id="result"></div>
<div class="cli">
<p>Or join via CLI:</p>
<p><code>murmur join --name YOUR_NAME --relay $relay_url
 --secret YOUR_SECRET --room $room_name</code></p>
</div>
</div>
<script>
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
const f=document.getElementById('f'),r=document.getElementById('result');
f.onsubmit=async e=>{
e.preventDefault();
const name=document.getElementById('name').value.trim();
if(!name)return;
const btn=f.querySelector('button');
btn.disabled=true;btn.textContent='Joining...';
try{
const res=await fetch('/invite/$room_name/join',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify({participant:name,token:'$token'}
});
if(res.ok){
r.innerHTML='<div class="msg ok">Joined <b>'+esc('$room_name')+'</b> as <b>'
+esc(name)+'</b>!</div>';
}else{
const d=await res.json();
r.innerHTML='<div class="msg err">'+esc(d.detail||'Failed')+'</div>';
btn.disabled=false;btn.textContent='Join Room';
}
}catch(err){
r.innerHTML='<div class="msg err">'+esc(err.message)+'</div>';
btn.disabled=false;btn.textContent='Join Room';
}
};
</script>
</body></html>""")


@router.get("/invite/{room_name}", response_class=HTMLResponse)
async def invite_page(room_name: str, request: Request, auth: AuthContext = Depends(verify_auth)):
    rid = _find_room_by_name(room_name)
    if not rid and room_name not in rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    relay_url = str(request.base_url).rstrip("/")
    invite_token = _make_invite_token(room_name)
    html = _INVITE_TMPL.substitute(
        room_name=room_name, relay_url=relay_url, token=invite_token,
    )
    return HTMLResponse(content=html)


@router.post("/invite/{room_name}/join")
async def invite_join(room_name: str, req: InviteJoinRequest):
    if not _verify_invite_token(req.token, room_name):
        raise HTTPException(status_code=403, detail="Invalid or expired invite token")
    async with rooms_lock:
        rid = _find_room_by_name(room_name)
        if not rid:
            raise HTTPException(status_code=404, detail="Room not found")
        room = rooms[rid]
        if len(room["members"]) >= MAX_ROOM_MEMBERS:
            raise HTTPException(
                status_code=400,
                detail=f"Room is at maximum capacity ({MAX_ROOM_MEMBERS} members)",
            )
        room["members"].add(req.participant)
        participants.add(req.participant)
    await _persist()
    return {"status": "joined"}
