import asyncio
import hmac
import ipaddress
import json
import logging
import os
import re
import string
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, field_validator

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mcp_tunnel.relay")

RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
if not RELAY_SECRET:
    raise SystemExit(
        "RELAY_SECRET is not set. "
        "Set the RELAY_SECRET environment variable to a strong secret."
    )


@asynccontextmanager
async def lifespan(app):
    logger.info("Relay server starting up")
    await asyncio.to_thread(_load_from_file)
    if _expire_stale_messages():
        await _persist_state()
    yield
    logger.info("Relay server shutting down — saving state")
    await _persist_state()

app = FastAPI(
    title="Murmur Relay",
    description=(
        "Real-time messaging relay for AI agent coordination. "
        "Any agent that speaks HTTP can join rooms, send messages, "
        "and coordinate work through this API."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

# CORS — configurable via CORS_ORIGINS env var (comma-separated)
_cors_origins = os.environ.get("CORS_ORIGINS", "")
if _cors_origins:
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",")],
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )

# In-memory state
message_queues: dict[str, list[dict]] = defaultdict(list)
participants: set[str] = set()
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
persistence_lock = asyncio.Lock()
message_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
webhooks: dict[str, str] = {}  # instance_name -> callback_url
rooms: dict[str, dict] = {}  # room_id -> {name, created_by, members, created_at}
room_history: dict[str, list[dict]] = defaultdict(list)  # room_id -> last N messages
sse_queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
presence: dict[str, dict] = {}  # instance_name -> {last_heartbeat, status, room, uptime_start}

MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "1000"))
MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))
MESSAGE_TTL_SECONDS = int(os.environ.get("MESSAGE_TTL_SECONDS", str(24 * 60 * 60)))  # default 24h
MAX_ROOM_HISTORY = int(os.environ.get("MAX_ROOM_HISTORY", "200"))
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "90"))  # offline threshold

# Rate limiting: per-sender sliding window
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))  # seconds
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "60"))  # messages per window
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(sender: str) -> bool:
    """Return True if the sender is within the rate limit, False if exceeded."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    bucket = _rate_buckets[sender]
    # Prune old entries
    _rate_buckets[sender] = [t for t in bucket if t > cutoff]
    if len(_rate_buckets[sender]) >= RATE_LIMIT_MAX:
        return False
    _rate_buckets[sender].append(now)
    return True


# Analytics state
analytics: dict = {
    "total_sent": 0,
    "total_delivered": 0,
    "per_participant": {},
    "hourly_volume": {},
}
_start_time = time.time()


def _logical_message_units() -> list[dict]:
    """Group stored entries into logical messages so chunk groups stay intact."""
    units: dict[tuple[str, str, str], dict] = {}

    for recipient, msgs in message_queues.items():
        for msg in msgs:
            if "chunk_group" in msg:
                key = (recipient, "chunk_group", msg["chunk_group"])
            else:
                key = (recipient, "message", msg["id"])

            unit = units.setdefault(
                key,
                {
                    "recipient": recipient,
                    "timestamp": msg["timestamp"],
                    "ids": set(),
                    "size": 0,
                },
            )
            if msg["timestamp"] < unit["timestamp"]:
                unit["timestamp"] = msg["timestamp"]
            unit["ids"].add(msg["id"])
            unit["size"] += 1

    return sorted(units.values(), key=lambda unit: unit["timestamp"])


def _reset_state():
    """Reset state between tests."""
    global _start_time, persistence_lock
    message_queues.clear()
    participants.clear()
    locks.clear()
    persistence_lock = asyncio.Lock()
    message_events.clear()
    webhooks.clear()
    rooms.clear()
    room_history.clear()
    sse_queues.clear()
    presence.clear()
    _rate_buckets.clear()
    analytics["total_sent"] = 0
    analytics["total_delivered"] = 0
    analytics["per_participant"].clear()
    analytics["hourly_volume"].clear()
    _start_time = time.time()


def _track_send(sender: str):
    """Update analytics counters for a sent message."""
    analytics["total_sent"] += 1
    if sender not in analytics["per_participant"]:
        analytics["per_participant"][sender] = {"sent": 0, "received": 0}
    analytics["per_participant"][sender]["sent"] += 1
    hour_key = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
    analytics["hourly_volume"][hour_key] = analytics["hourly_volume"].get(hour_key, 0) + 1


def _track_delivery(recipient: str, count: int):
    """Update analytics counters for delivered messages."""
    if count == 0:
        return
    analytics["total_delivered"] += count
    if recipient not in analytics["per_participant"]:
        analytics["per_participant"][recipient] = {"sent": 0, "received": 0}
    analytics["per_participant"][recipient]["received"] += count


def _prune_hourly_volume():
    """Remove hourly volume entries older than 72 hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    analytics["hourly_volume"] = {
        k: v for k, v in analytics["hourly_volume"].items() if k >= cutoff
    }


def _save_to_file():
    """Save current state to JSON file."""
    data = {
        "messages": {k: list(v) for k, v in message_queues.items()},
        "participants": sorted(participants),
        "analytics": {
            "total_sent": analytics["total_sent"],
            "total_delivered": analytics["total_delivered"],
            "per_participant": analytics["per_participant"],
            "hourly_volume": analytics["hourly_volume"],
        },
        "rooms": {
            rid: {
                "name": r["name"],
                "created_by": r["created_by"],
                "members": sorted(r["members"]),
                "created_at": r["created_at"],
            }
            for rid, r in rooms.items()
        },
        "room_history": {rid: msgs for rid, msgs in room_history.items()},
        "presence": presence,
    }
    try:
        with open(MESSAGES_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        logger.error("Failed to save state to %s", MESSAGES_FILE)


async def _persist_state():
    """Serialize in-memory state to disk without overlapping file writes."""
    async with persistence_lock:
        await asyncio.to_thread(_save_to_file)


def _load_from_file():
    """Load state from JSON file if it exists."""
    if not os.path.exists(MESSAGES_FILE):
        return
    try:
        with open(MESSAGES_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Corrupt persistence file %s, starting fresh", MESSAGES_FILE)
        return
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
            "created_at": rdata["created_at"],
        }
    for rid, msgs in data.get("room_history", {}).items():
        room_history[rid] = msgs
    for name, pdata in data.get("presence", {}).items():
        presence[name] = pdata
    logger.info("Loaded state from %s", MESSAGES_FILE)


def _expire_stale_messages() -> int:
    """Remove messages older than MESSAGE_TTL_SECONDS and return the count expired."""
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
    """If total stored entries exceed MAX_MESSAGES, trim whole logical messages."""
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
        removed_units,
        removed_entries,
        MAX_MESSAGES,
    )

    for recipient in message_queues:
        message_queues[recipient] = [
            m for m in message_queues[recipient] if m["id"] not in remove_ids
        ]


def _chunk_content(content: str) -> list[str]:
    """Split text into chunks that each respect MAX_MESSAGE_SIZE in UTF-8 bytes."""
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


def _count_pending_messages() -> int:
    return len(_logical_message_units())


def _validate_webhook_callback_url(callback_url: str) -> str:
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
    except ValueError:
        if "." not in host:
            raise HTTPException(status_code=400, detail="callback_url must target a public host")
        return callback_url

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise HTTPException(status_code=400, detail="callback_url must target a public host")

    return callback_url


async def verify_auth(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth, f"Bearer {RELAY_SECRET}"):
        logger.warning("Auth failure from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")


_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
MAX_NAME_LENGTH = 64


def _validate_name(value: str) -> str:
    if not value or len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"Name must be 1-{MAX_NAME_LENGTH} characters")
    if not _NAME_RE.match(value):
        raise ValueError("Name must contain only alphanumeric characters, hyphens, and underscores")
    return value


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


VALID_MESSAGE_TYPES = {"chat", "claim", "status", "request", "alert", "sync"}
MAX_ROOM_MEMBERS = int(os.environ.get("MAX_ROOM_MEMBERS", "50"))


class RoomMessageRequest(BaseModel):
    from_name: str
    content: str
    message_type: str = "chat"

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


class JoinLeaveRequest(BaseModel):
    participant: str

    @field_validator("participant")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


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


def _find_room_by_name(name: str) -> Optional[str]:
    for rid, room in rooms.items():
        if room["name"] == name:
            return rid
    return None


def _resolve_room(room_id_or_name: str) -> tuple[str, dict]:
    """Resolve a room by ID or name. Raises HTTPException if not found."""
    room = rooms.get(room_id_or_name)
    if room:
        return room_id_or_name, room
    rid = _find_room_by_name(room_id_or_name)
    if rid:
        return rid, rooms[rid]
    raise HTTPException(status_code=404, detail="Room not found")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """Web dashboard — rooms, live messages, send box."""
    return _DASHBOARD_HTML


@app.get("/health")
async def health():
    """Check if the relay is running."""
    return {"status": "ok"}


@app.get("/health/detailed", dependencies=[Depends(verify_auth)])
async def health_detailed():
    """Detailed health: uptime, counts, active connections."""
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


async def _notify_webhook(recipient: str, message: dict):
    """Fire webhook notification for recipient, if registered. Failures are logged and ignored."""
    callback_url = webhooks.get(recipient)
    if not callback_url:
        return
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(callback_url, json=message, timeout=5)
            resp.raise_for_status()
            logger.info("Webhook delivered to %s for %s", callback_url, recipient)
    except Exception:
        logger.warning(
            "Webhook delivery failed for %s at %s",
            recipient,
            callback_url,
            exc_info=True,
        )


@app.post("/messages", dependencies=[Depends(verify_auth)])
async def send_message(msg: SendMessageRequest):
    """Send a direct message to another participant."""
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
            await _persist_state()
        message_events[msg.to].set()
        reassembled = {
            "id": messages[0]["id"],
            "from_name": msg.from_name,
            "to": msg.to,
            "content": content,
            "timestamp": timestamp,
        }
        for q in sse_queues.get(msg.to, []):
            await q.put(reassembled)
        logger.info(
            "Message chunked into %d parts (%s -> %s, group %s)",
            chunk_total, msg.from_name, msg.to, chunk_group,
        )
        await _notify_webhook(msg.to, reassembled)
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
            await _persist_state()
        message_events[msg.to].set()
        for q in sse_queues.get(msg.to, []):
            await q.put(message)
        logger.info("Message %s: %s -> %s", message["id"], msg.from_name, msg.to)
        await _notify_webhook(msg.to, message)
        return {"id": message["id"], "timestamp": message["timestamp"]}


@app.get("/stream/{recipient}")
async def stream_messages(recipient: str, token: str = ""):
    """SSE endpoint for real-time message delivery."""
    if not hmac.compare_digest(token, RELAY_SECRET):
        raise HTTPException(status_code=401, detail="Invalid token")

    q: asyncio.Queue = asyncio.Queue()
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


def _reassemble_chunks(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate messages into ready (reassembled + non-chunked) and held-back (incomplete chunks).

    Returns (ready_messages, held_back_messages).
    """
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


@app.get("/messages/{recipient}", dependencies=[Depends(verify_auth)])
async def get_messages(recipient: str, wait: int = 0):
    """Fetch pending messages for a recipient. Use wait=N for long-polling (up to 60s)."""
    wait = min(max(wait, 0), 60)  # Clamp to 0-60

    async with locks[recipient]:
        expired = _expire_stale_messages()
        all_msgs = list(message_queues[recipient])
        ready, held_back = _reassemble_chunks(all_msgs)
        message_queues[recipient] = held_back
        # Re-arm after observing queue state while holding the same lock senders use
        # before enqueueing, so a subsequent set() always represents newer data.
        message_events[recipient].clear()
        if ready:
            _track_delivery(recipient, len(ready))
        if ready or expired:
            await _persist_state()

    if not ready and wait > 0:
        try:
            await asyncio.wait_for(message_events[recipient].wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        # Re-check after waking
        async with locks[recipient]:
            expired = _expire_stale_messages()
            all_msgs = list(message_queues[recipient])
            ready, held_back = _reassemble_chunks(all_msgs)
            message_queues[recipient] = held_back
            message_events[recipient].clear()
            if ready:
                _track_delivery(recipient, len(ready))
            if ready or expired:
                await _persist_state()
    logger.info(
        "Fetched %d messages for %s (%d chunks held back)",
        len(ready), recipient, len(held_back),
    )
    return ready


@app.get("/messages/{recipient}/peek", dependencies=[Depends(verify_auth)])
async def peek_messages(recipient: str):
    """Return count of pending messages without consuming them."""
    count = len(message_queues.get(recipient, []))
    return {"count": count, "recipient": recipient}


@app.get("/participants", dependencies=[Depends(verify_auth)])
async def list_participants_endpoint():
    """List all known participant names."""
    return sorted(participants)


@app.post("/webhooks", dependencies=[Depends(verify_auth)])
async def register_webhook(req: RegisterWebhookRequest):
    """Register a webhook URL for push notifications when messages arrive."""
    callback_url = _validate_webhook_callback_url(req.callback_url)
    webhooks[req.instance_name] = callback_url
    participants.add(req.instance_name)
    await _persist_state()
    logger.info("Webhook registered: %s -> %s", req.instance_name, callback_url)
    return {"status": "registered"}


@app.delete("/webhooks/{instance_name}", dependencies=[Depends(verify_auth)])
async def delete_webhook(instance_name: str):
    """Remove a previously registered webhook."""
    webhooks.pop(instance_name, None)
    logger.info("Webhook removed: %s", instance_name)
    return {"status": "removed"}


@app.post("/rooms", dependencies=[Depends(verify_auth)])
async def create_room(req: CreateRoomRequest):
    """Create a new room. The creator is auto-added as a member."""
    if _find_room_by_name(req.name):
        raise HTTPException(status_code=409, detail="Room name already exists")
    room_id = str(uuid.uuid4())
    room = {
        "name": req.name,
        "created_by": req.created_by,
        "members": {req.created_by},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    rooms[room_id] = room
    participants.add(req.created_by)
    await _persist_state()
    logger.info("Room created: %s (%s) by %s", req.name, room_id, req.created_by)
    return {
        "id": room_id,
        "name": room["name"],
        "members": sorted(room["members"]),
        "created_at": room["created_at"],
    }


@app.get("/rooms", dependencies=[Depends(verify_auth)])
async def list_rooms():
    """List all rooms with their members."""
    return [
        {
            "id": rid,
            "name": room["name"],
            "members": sorted(room["members"]),
            "created_at": room["created_at"],
        }
        for rid, room in rooms.items()
    ]


@app.get("/rooms/{room_id}", dependencies=[Depends(verify_auth)])
async def get_room(room_id: str):
    """Get a room by ID or name."""
    rid, room = _resolve_room(room_id)
    return {
        "id": rid,
        "name": room["name"],
        "members": sorted(room["members"]),
        "created_at": room["created_at"],
    }


@app.post("/rooms/{room_id}/join", dependencies=[Depends(verify_auth)])
async def join_room(room_id: str, req: JoinLeaveRequest):
    """Add a participant to a room."""
    room_id, room = _resolve_room(room_id)
    if len(room["members"]) >= MAX_ROOM_MEMBERS:
        raise HTTPException(
            status_code=400,
            detail=f"Room is at maximum capacity ({MAX_ROOM_MEMBERS} members)",
        )
    room["members"].add(req.participant)
    participants.add(req.participant)
    await _persist_state()
    return {"status": "joined"}


@app.post("/rooms/{room_id}/leave", dependencies=[Depends(verify_auth)])
async def leave_room(room_id: str, req: JoinLeaveRequest):
    """Remove a participant from a room."""
    room_id, room = _resolve_room(room_id)
    room["members"].discard(req.participant)
    await _persist_state()
    return {"status": "left"}


@app.post("/rooms/{room_id}/kick", dependencies=[Depends(verify_auth)])
async def kick_from_room(room_id: str, req: KickRequest):
    """Remove a participant from a room (admin only — room creator)."""
    room_id, room = _resolve_room(room_id)
    if req.requested_by != room["created_by"]:
        raise HTTPException(status_code=403, detail="Only the room creator can kick members")
    if req.participant == room["created_by"]:
        raise HTTPException(status_code=400, detail="Cannot kick the room creator")
    if req.participant not in room["members"]:
        raise HTTPException(status_code=404, detail="Participant not in room")
    room["members"].discard(req.participant)
    await _persist_state()
    logger.info("Kicked %s from room %s by %s", req.participant, room["name"], req.requested_by)
    return {"status": "kicked", "participant": req.participant}


@app.delete("/rooms/{room_id}", dependencies=[Depends(verify_auth)])
async def destroy_room(room_id: str, req: DestroyRoomRequest):
    """Delete a room and its history (admin only — room creator)."""
    room_id, room = _resolve_room(room_id)
    if req.requested_by != room["created_by"]:
        raise HTTPException(status_code=403, detail="Only the room creator can destroy the room")
    room_name = room["name"]
    del rooms[room_id]
    room_history.pop(room_id, None)
    await _persist_state()
    logger.info("Room %s (%s) destroyed by %s", room_name, room_id, req.requested_by)
    return {"status": "destroyed", "room": room_name}


@app.patch("/rooms/{room_id}", dependencies=[Depends(verify_auth)])
async def rename_room(room_id: str, req: RenameRoomRequest):
    """Rename a room (admin only — room creator)."""
    room_id, room = _resolve_room(room_id)
    if req.requested_by != room["created_by"]:
        raise HTTPException(status_code=403, detail="Only the room creator can rename the room")
    if _find_room_by_name(req.new_name):
        raise HTTPException(status_code=409, detail="Room name already exists")
    old_name = room["name"]
    room["name"] = req.new_name
    # Update room name in history messages
    for msg in room_history.get(room_id, []):
        msg["room"] = req.new_name
    await _persist_state()
    logger.info("Room renamed: %s -> %s by %s", old_name, req.new_name, req.requested_by)
    return {"status": "renamed", "old_name": old_name, "new_name": req.new_name}


@app.post("/rooms/{room_id}/messages", dependencies=[Depends(verify_auth)])
async def send_room_message(room_id: str, msg: RoomMessageRequest):
    """Send a message to all members of a room."""
    if not _check_rate_limit(msg.from_name):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    room_id, room = _resolve_room(room_id)
    if msg.from_name not in room["members"]:
        raise HTTPException(status_code=403, detail="Not a member of this room")

    if len(msg.content.encode("utf-8")) > MAX_MESSAGE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Message content exceeds maximum size",
        )

    timestamp = datetime.now(timezone.utc).isoformat()
    message_id = str(uuid.uuid4())
    recipients = room["members"]

    for recipient in recipients:
        fan_out_msg = {
            "id": str(uuid.uuid4()),
            "from_name": msg.from_name,
            "to": recipient,
            "room": room["name"],
            "content": msg.content,
            "message_type": msg.message_type,
            "timestamp": timestamp,
        }
        async with locks[recipient]:
            message_queues[recipient].append(fan_out_msg)
        message_events[recipient].set()
        for q in sse_queues.get(recipient, []):
            await q.put(fan_out_msg)
        await _notify_webhook(recipient, fan_out_msg)

    # Store in room history (not cleared on read)
    history_msg = {
        "id": message_id,
        "from_name": msg.from_name,
        "room": room["name"],
        "content": msg.content,
        "message_type": msg.message_type,
        "timestamp": timestamp,
    }
    room_history[room_id].append(history_msg)
    if len(room_history[room_id]) > MAX_ROOM_HISTORY:
        room_history[room_id] = room_history[room_id][-MAX_ROOM_HISTORY:]

    participants.add(msg.from_name)
    _track_send(msg.from_name)
    _trim_messages()
    await _persist_state()

    logger.info(
        "Room message %s in %s: %s -> %d recipients",
        message_id, room["name"], msg.from_name, len(recipients),
    )
    return {"id": message_id, "timestamp": timestamp}


@app.get("/rooms/{room_id}/history", dependencies=[Depends(verify_auth)])
async def get_room_history(room_id: str, limit: int = 50):
    """Return the last N messages sent to a room (not cleared on read)."""
    rid, room = _resolve_room(room_id)
    limit = min(max(limit, 1), MAX_ROOM_HISTORY)
    msgs = room_history.get(rid, [])
    return msgs[-limit:]


@app.post("/heartbeat", dependencies=[Depends(verify_auth)])
async def heartbeat(req: HeartbeatRequest):
    """Record an agent heartbeat to track online/offline presence."""
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
    await _persist_state()
    logger.info("Heartbeat from %s (status=%s)", name, req.status)
    return {"status": "ok", "timestamp": now}


@app.get("/presence", dependencies=[Depends(verify_auth)])
async def get_presence():
    """Return all known agents with online/offline status based on heartbeat TTL."""
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


@app.get("/analytics", dependencies=[Depends(verify_auth)])
async def get_analytics():
    """Relay statistics: message counts, per-participant stats, hourly volume."""
    if _expire_stale_messages():
        await _persist_state()
    _prune_hourly_volume()
    pending = _count_pending_messages()
    hourly = [
        {"hour": k, "count": v}
        for k, v in sorted(analytics["hourly_volume"].items())
    ]
    return {
        "total_messages_sent": analytics["total_sent"],
        "total_messages_delivered": analytics["total_delivered"],
        "messages_pending": pending,
        "participants": analytics["per_participant"],
        "hourly_volume": hourly,
        "uptime_seconds": round(time.time() - _start_time),
    }


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
const f=document.getElementById('f'),r=document.getElementById('result');
f.onsubmit=async e=>{
e.preventDefault();
const name=document.getElementById('name').value.trim();
if(!name)return;
const btn=f.querySelector('button');
btn.disabled=true;btn.textContent='Joining...';
try{
const res=await fetch('/rooms/$room_name/join',{
method:'POST',
headers:{'Content-Type':'application/json',
'Authorization':'Bearer $token'},
body:JSON.stringify({participant:name})
});
if(res.ok){
r.innerHTML='<div class="msg ok">Joined <b>$room_name</b> as <b>'
+name+'</b>!</div>';
}else{
const d=await res.json();
r.innerHTML='<div class="msg err">'+(d.detail||'Failed')+'</div>';
btn.disabled=false;btn.textContent='Join Room';
}
}catch(err){
r.innerHTML='<div class="msg err">'+err.message+'</div>';
btn.disabled=false;btn.textContent='Join Room';
}
};
</script>
</body></html>""")

INVITE_TOKEN = os.environ.get("INVITE_TOKEN", RELAY_SECRET)


@app.get("/invite/{room_name}", response_class=HTMLResponse)
async def invite_page(room_name: str, request: Request):
    """Discord-style invite page. Anyone with the link can join."""
    # Verify room exists
    rid = _find_room_by_name(room_name)
    if not rid and room_name not in rooms:
        raise HTTPException(status_code=404, detail="Room not found")

    relay_url = str(request.base_url).rstrip("/")
    html = _INVITE_TMPL.substitute(
        room_name=room_name,
        relay_url=relay_url,
        token=INVITE_TOKEN,
    )
    return HTMLResponse(content=html)


_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Murmur</title>
<style>
:root{
  --bg-0:#09090b;--bg-1:#111113;--bg-2:#18181b;
  --bg-hover:#1e1e22;--border:#27272a;
  --text:#fafafa;--text-2:#a1a1aa;--text-3:#52525b;
  --accent:#3b82f6;--accent-h:#2563eb;
  --accent-s:rgba(59,130,246,.1);
  --green:#22c55e;--red:#ef4444;--orange:#f59e0b;
  --purple:#a855f7;--emerald:#10b981;
  --r:8px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',
  'Inter',system-ui,sans-serif;
  background:var(--bg-0);color:var(--text);
  -webkit-font-smoothing:antialiased;
}
header{
  background:var(--bg-1);
  border-bottom:1px solid var(--border);
  padding:0 20px;height:52px;
  display:flex;align-items:center;gap:12px;
}
.logo{
  font-size:15px;font-weight:600;
  letter-spacing:-.02em;
  display:flex;align-items:center;gap:8px;
}
.logo svg{width:20px;height:20px;opacity:.9}
.conn{
  margin-left:auto;display:flex;
  align-items:center;gap:6px;
  font-size:12px;color:var(--text-2);
}
.conn-dot{
  width:7px;height:7px;border-radius:50%;
  transition:background .3s;
}
.conn-ok{background:var(--green)}
.conn-err{background:var(--red);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.container{display:flex;height:calc(100vh - 52px)}
.sidebar{
  width:260px;background:var(--bg-1);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;flex-shrink:0;
}
.sidebar-hdr{
  padding:16px 16px 12px;font-size:11px;
  font-weight:600;color:var(--text-3);
  text-transform:uppercase;letter-spacing:.05em;
}
.rooms-list{flex:1;overflow-y:auto;padding:0 8px}
.room-item{
  padding:10px 12px;cursor:pointer;
  border-radius:var(--r);font-size:13px;
  font-weight:500;color:var(--text-2);
  display:flex;align-items:center;gap:8px;
  transition:all .15s;margin-bottom:2px;
}
.room-item:hover{
  background:var(--bg-hover);color:var(--text);
}
.room-item.active{
  background:var(--accent-s);color:var(--accent);
}
.room-item .ri{opacity:.5;flex-shrink:0}
.room-item .rn{
  flex:1;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;
}
.room-item .cnt{
  font-size:11px;color:var(--text-3);
  background:var(--bg-2);
  padding:2px 6px;border-radius:10px;
}
.room-item .unread{
  background:var(--accent);color:#fff;
  font-size:10px;font-weight:600;
  padding:2px 7px;border-radius:10px;
  min-width:18px;text-align:center;
}
.main{
  flex:1;display:flex;flex-direction:column;
  background:var(--bg-0);
}
.room-hdr{
  padding:14px 20px;
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:12px;
}
.room-hdr .rt{font-size:14px;font-weight:600}
.room-hdr .rm{
  font-size:12px;color:var(--text-3);
  margin-left:auto;
}
.messages{
  flex:1;overflow-y:auto;padding:16px 20px;
  display:flex;flex-direction:column;gap:2px;
}
.messages::-webkit-scrollbar{width:6px}
.messages::-webkit-scrollbar-track{background:transparent}
.messages::-webkit-scrollbar-thumb{
  background:var(--border);border-radius:3px;
}
.msg{
  font-size:13px;line-height:1.6;
  padding:4px 8px;border-radius:var(--r);
  transition:background .1s;
}
.msg:hover{background:var(--bg-hover)}
.msg .ts{
  color:var(--text-3);font-size:11px;
  margin-right:8px;
  font-variant-numeric:tabular-nums;
}
.msg .sender{
  font-weight:600;color:var(--accent);
  margin-right:6px;
}
.msg .tag{
  font-size:10px;font-weight:600;
  padding:2px 6px;border-radius:4px;
  margin-right:6px;text-transform:uppercase;
  letter-spacing:.03em;vertical-align:1px;
}
.tag-claim{
  background:rgba(245,158,11,.15);color:var(--orange);
}
.tag-status{
  background:rgba(59,130,246,.15);color:var(--accent);
}
.tag-request{
  background:rgba(168,85,247,.15);color:var(--purple);
}
.tag-alert{
  background:rgba(239,68,68,.15);color:var(--red);
}
.tag-sync{
  background:rgba(16,185,129,.15);color:var(--emerald);
}
.members-bar{
  padding:10px 20px;
  border-top:1px solid var(--border);
  background:var(--bg-1);
  display:flex;align-items:center;gap:4px;
  flex-wrap:wrap;
}
.members-bar .lbl{
  font-size:11px;color:var(--text-3);
  margin-right:8px;font-weight:500;
}
.member{
  display:inline-flex;align-items:center;gap:4px;
  font-size:12px;color:var(--text-2);
  padding:3px 8px;border-radius:var(--r);
  background:var(--bg-2);margin:2px;
}
.dot{width:6px;height:6px;border-radius:50%}
.dot-online{background:var(--green)}
.dot-offline{background:var(--text-3)}
.input-bar{
  padding:12px 20px 16px;display:flex;gap:8px;
}
.input-bar input{
  flex:1;background:var(--bg-1);
  border:1px solid var(--border);
  border-radius:var(--r);padding:10px 14px;
  color:var(--text);font-size:13px;outline:none;
  transition:border-color .2s,box-shadow .2s;
}
.input-bar input:focus{
  border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-s);
}
.input-bar input::placeholder{color:var(--text-3)}
.input-bar button{
  background:var(--accent);color:#fff;
  border:none;border-radius:var(--r);
  padding:10px 20px;cursor:pointer;
  font-size:13px;font-weight:500;
  transition:background .15s,transform .1s;
}
.input-bar button:hover{background:var(--accent-h)}
.input-bar button:active{transform:scale(.97)}
.input-bar button:disabled{
  opacity:.4;cursor:not-allowed;transform:none;
}
.empty{
  color:var(--text-3);text-align:center;
  padding:60px 20px;font-size:13px;line-height:1.6;
}
.empty-icon{font-size:32px;margin-bottom:8px;opacity:.5}
@media(max-width:700px){
  .sidebar{
    position:fixed;left:-280px;top:52px;
    bottom:0;z-index:10;width:280px;
    transition:left .2s ease;
  }
  .sidebar.open{left:0}
  .menu-btn{display:block !important}
  .msg{font-size:12px}
  .input-bar input{font-size:16px}
}
.menu-btn{
  display:none;background:none;border:none;
  color:var(--text-2);cursor:pointer;
  padding:4px;border-radius:4px;
}
.menu-btn:hover{background:var(--bg-hover)}
</style>
</head>
<body>
<header>
  <button class="menu-btn" onclick="toggleSidebar()">
    <svg width="18" height="18" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" stroke-width="2">
      <path d="M3 12h18M3 6h18M3 18h18"/>
    </svg>
  </button>
  <div class="logo">
    <svg viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2">
      <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2
        2 0 012-2h14a2 2 0 012 2z"/>
    </svg>
    murmur
  </div>
  <div class="conn" id="status">
    <span class="conn-dot conn-err" id="connDot"></span>
    <span id="connText">connecting</span>
  </div>
</header>
<div class="container">
  <div class="sidebar" id="sidebar">
    <div class="sidebar-hdr">Rooms</div>
    <div class="rooms-list" id="rooms">
      <div class="empty">Loading...</div>
    </div>
  </div>
  <div class="main">
    <div class="room-hdr" id="roomHdr" style="display:none">
      <span class="rt" id="roomTitle"></span>
      <span class="rm" id="roomMeta"></span>
    </div>
    <div class="messages" id="messages">
      <div class="empty">
        <div class="empty-icon">&#x1f4ac;</div>
        Select a room to start
      </div>
    </div>
    <div class="members-bar" id="members"
      style="display:none"></div>
    <div class="input-bar">
      <input id="msgInput"
        placeholder="Type a message..." disabled>
      <button id="sendBtn" onclick="sendMsg()"
        disabled>Send</button>
    </div>
  </div>
</div>
<script>
const API=location.origin;
const P=new URLSearchParams(location.search);
const TOKEN=P.get('token')||'';
const NAME=P.get('name')||'web-user';
const H={
  'Authorization':'Bearer '+TOKEN,
  'Content-Type':'application/json'
};
let currentRoom=null,sse=null;
const unread={};

function toggleSidebar(){
  document.getElementById('sidebar').classList.toggle('open');
}

async function loadRooms(){
  try{
    const r=await fetch(API+'/rooms',{headers:H});
    if(!r.ok){setConn(false);return}
    const rooms=await r.json();
    setConn(true);
    const el=document.getElementById('rooms');
    if(!rooms.length){
      el.innerHTML='<div class="empty">No rooms yet</div>';
      return;
    }
    el.innerHTML=rooms.map(rm=>{
      const u=unread[rm.name]||0;
      const badge=u
        ?'<span class="unread">'+u+'</span>'
        :'<span class="cnt">'+rm.members.length+'</span>';
      const act=rm.name===currentRoom?' active':'';
      return '<div class="room-item'+act
        +'" onclick="selectRoom(\''+rm.name+'\')">'+
        '<span class="ri">#</span>'+
        '<span class="rn">'+rm.name+'</span>'+
        badge+'</div>';
    }).join('');
  }catch(e){setConn(false)}
}

async function selectRoom(name){
  currentRoom=name;
  unread[name]=0;
  document.getElementById('sidebar')
    .classList.remove('open');
  document.getElementById('msgInput').disabled=false;
  document.getElementById('sendBtn').disabled=false;
  loadRooms();
  try{
    const r=await fetch(
      API+'/rooms/'+name+'/history?limit=100',
      {headers:H}
    );
    const msgs=await r.json();
    const el=document.getElementById('messages');
    el.innerHTML=msgs.map(formatMsg).join('');
    scrollToBottom();
  }catch(e){}
  try{
    const r=await fetch(
      API+'/rooms/'+name,{headers:H}
    );
    const room=await r.json();
    const hdr=document.getElementById('roomHdr');
    hdr.style.display='flex';
    document.getElementById('roomTitle')
      .textContent='# '+name;
    document.getElementById('roomMeta')
      .textContent=room.members.length+' members';
    const pr=await fetch(API+'/presence',{headers:H});
    const presence=await pr.json();
    const online=new Set(
      presence.filter(p=>p.online).map(p=>p.name)
    );
    const mb=document.getElementById('members');
    mb.style.display='flex';
    mb.innerHTML='<span class="lbl">Members</span>'+
      room.members.map(m=>{
        const d=online.has(m)?'dot-online':'dot-offline';
        return '<span class="member">'+
          '<span class="dot '+d+'"></span>'+
          m+'</span>';
      }).join('');
  }catch(e){}
  connectSSE();
}

function setConn(ok){
  document.getElementById('connDot').className=
    'conn-dot '+(ok?'conn-ok':'conn-err');
  document.getElementById('connText').textContent=
    ok?'connected':'reconnecting';
}

function scrollToBottom(){
  const el=document.getElementById('messages');
  requestAnimationFrame(
    ()=>{el.scrollTop=el.scrollHeight}
  );
}

function connectSSE(){
  if(sse)sse.close();
  sse=new EventSource(
    API+'/stream/'+NAME+'?token='+TOKEN
  );
  sse.onopen=()=>setConn(true);
  sse.onerror=()=>setConn(false);
  sse.addEventListener('message',e=>{
    try{
      const msg=JSON.parse(e.data);
      if(msg.room===currentRoom){
        const el=document.getElementById('messages');
        el.innerHTML+=formatMsg(msg);
        scrollToBottom();
      }else if(msg.room){
        unread[msg.room]=(unread[msg.room]||0)+1;
        loadRooms();
      }
    }catch(e){}
  });
}

function formatMsg(msg){
  const ts=(msg.timestamp||'').substring(11,19);
  const type=msg.message_type||'chat';
  let tag='';
  if(type!=='chat'){
    tag='<span class="tag tag-'+type+'">'+
      type+'</span>';
  }
  return '<div class="msg"><span class="ts">'+ts+
    '</span><span class="sender">'+
    (msg.from_name||'?')+'</span>'+
    tag+(msg.content||'')+'</div>';
}

async function sendMsg(){
  const input=document.getElementById('msgInput');
  const text=input.value.trim();
  if(!text||!currentRoom)return;
  input.value='';
  try{await fetch(
    API+'/rooms/'+currentRoom+'/messages',{
      method:'POST',headers:H,
      body:JSON.stringify({from_name:NAME,content:text})
    }
  )}catch(e){}
}

document.getElementById('msgInput').addEventListener(
  'keydown',e=>{if(e.key==='Enter')sendMsg()}
);

async function refreshPresence(){
  if(!currentRoom)return;
  try{
    const r=await fetch(
      API+'/rooms/'+currentRoom,{headers:H}
    );
    const room=await r.json();
    const pr=await fetch(API+'/presence',{headers:H});
    const presence=await pr.json();
    const online=new Set(
      presence.filter(p=>p.online).map(p=>p.name)
    );
    const mb=document.getElementById('members');
    mb.innerHTML='<span class="lbl">Members</span>'+
      room.members.map(m=>{
        const d=online.has(m)?'dot-online':'dot-offline';
        return '<span class="member">'+
          '<span class="dot '+d+'"></span>'+
          m+'</span>';
      }).join('');
  }catch(e){}
}
loadRooms();
setInterval(()=>{loadRooms();refreshPresence()},30000);
</script>
</body>
</html>
"""

def main() -> None:
    """Run the relay server as a CLI entrypoint."""
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
