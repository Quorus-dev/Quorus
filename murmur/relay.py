import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import string
import tempfile
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Configure structlog for structured JSON logging in production
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if LOG_LEVEL == "DEBUG" else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, LOG_LEVEL, logging.INFO)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("murmur.relay")

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
    if _webhook_http_client and not _webhook_http_client.is_closed:
        await _webhook_http_client.aclose()

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

class RequestContextMiddleware(BaseHTTPMiddleware):
    """Inject request_id, method, and path into structlog context for every request."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


app.add_middleware(RequestContextMiddleware)

# CORS — configurable via CORS_ORIGINS env var (comma-separated)
_cors_origins = os.environ.get("CORS_ORIGINS", "")
if _cors_origins:
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",")],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

# In-memory state
message_queues: dict[str, list[dict]] = defaultdict(list)
participants: set[str] = set()
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
persistence_lock = asyncio.Lock()
rooms_lock = asyncio.Lock()  # global lock for room create/join/leave
message_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
webhooks: dict[str, str] = {}  # instance_name -> callback_url
rooms: dict[str, dict] = {}  # room_id -> {name, created_by, members, created_at}
room_history: dict[str, list[dict]] = defaultdict(list)  # room_id -> last N messages
SSE_QUEUE_MAX = int(os.environ.get("SSE_QUEUE_MAX", "1000"))
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


def _check_rate_limit(key: str) -> bool:
    """Return True if the key is within the rate limit, False if exceeded."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    bucket = _rate_buckets[key]
    # Prune old entries
    _rate_buckets[key] = [t for t in bucket if t > cutoff]
    if len(_rate_buckets[key]) >= RATE_LIMIT_MAX:
        return False
    _rate_buckets[key].append(now)
    return True


def _get_client_ip(request: Request) -> str:
    """Extract client IP for rate limiting, respecting X-Forwarded-For behind proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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


def _snapshot_state() -> dict:
    """Capture a consistent snapshot of in-memory state for serialization."""
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
                "created_at": r["created_at"],
            }
            for rid, r in rooms.items()
        },
        "room_history": {rid: list(msgs) for rid, msgs in room_history.items()},
        "presence": dict(presence),
        "webhooks": dict(webhooks),
    }


def _write_atomic(path: str, data: bytes) -> None:
    """Write data to path atomically via temp-file + fsync + rename."""
    dir_name = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, path)
    except OSError:
        logger.error("Failed to save state to %s", path)
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _save_to_file():
    """Save current state to JSON file atomically."""
    data = _snapshot_state()
    encoded = json.dumps(data, indent=2).encode("utf-8")
    _write_atomic(MESSAGES_FILE, encoded)


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
    except (json.JSONDecodeError, ValueError, OSError):
        logger.warning("Corrupt persistence file %s, starting fresh", MESSAGES_FILE)
        return
    _apply_loaded_state(data)
    logger.info("Loaded state from %s", MESSAGES_FILE)


def _apply_loaded_state(data: dict) -> None:
    """Apply a loaded state dict to in-memory globals."""
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
    for name, url in data.get("webhooks", {}).items():
        webhooks[name] = url


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


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_webhook_url_sync(callback_url: str) -> str:
    """Validate structure of a webhook URL (no I/O). Raises HTTPException."""
    parsed = urlparse(callback_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="callback_url must be an http(s) URL")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="callback_url must not include credentials")

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise HTTPException(status_code=400, detail="callback_url must target a public host")

    # Check literal IP addresses
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
    """Validate webhook URL structure and resolve hostname to check for SSRF."""
    url = _validate_webhook_url_sync(callback_url)

    parsed = urlparse(url)
    host = parsed.hostname.rstrip(".").lower()

    # Skip DNS check for literal IPs (already validated above)
    try:
        ipaddress.ip_address(host)
        return url
    except ValueError:
        pass

    # Resolve hostname and check all A/AAAA records against private ranges
    try:
        addrinfo = await asyncio.to_thread(
            socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM
        )
    except socket.gaierror:
        raise HTTPException(
            status_code=400, detail="callback_url hostname could not be resolved"
        )

    if not addrinfo:
        raise HTTPException(
            status_code=400, detail="callback_url hostname could not be resolved"
        )

    for family, _, _, _, sockaddr in addrinfo:
        resolved_ip = ipaddress.ip_address(sockaddr[0])
        if _is_private_ip(resolved_ip):
            raise HTTPException(
                status_code=400,
                detail="callback_url must not resolve to a private address",
            )

    return url


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


def _sse_push(recipient: str, message: dict) -> None:
    """Push a message to all SSE queues for a recipient, dropping if full."""
    for q in sse_queues.get(recipient, []):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("SSE queue full for %s, dropping message", recipient)


WEBHOOK_CONCURRENCY = int(os.environ.get("WEBHOOK_CONCURRENCY", "10"))
_webhook_semaphore = asyncio.Semaphore(WEBHOOK_CONCURRENCY)
_webhook_http_client: httpx.AsyncClient | None = None


def _get_webhook_client() -> httpx.AsyncClient:
    """Lazy-init a shared httpx client for webhook delivery."""
    global _webhook_http_client
    if _webhook_http_client is None or _webhook_http_client.is_closed:
        _webhook_http_client = httpx.AsyncClient(timeout=5)
    return _webhook_http_client


async def _deliver_webhook(recipient: str, callback_url: str, message: dict) -> None:
    """Deliver a single webhook with concurrency limiting."""
    async with _webhook_semaphore:
        try:
            client = _get_webhook_client()
            resp = await client.post(callback_url, json=message)
            resp.raise_for_status()
            logger.info("Webhook delivered to %s for %s", callback_url, recipient)
        except Exception:
            logger.warning(
                "Webhook delivery failed for %s at %s",
                recipient,
                callback_url,
                exc_info=True,
            )


def _notify_webhook(recipient: str, message: dict) -> None:
    """Schedule webhook delivery in the background (non-blocking)."""
    callback_url = webhooks.get(recipient)
    if not callback_url:
        return
    asyncio.create_task(_deliver_webhook(recipient, callback_url, message))


@app.post("/messages", dependencies=[Depends(verify_auth)])
async def send_message(msg: SendMessageRequest, request: Request):
    """Send a direct message to another participant."""
    if not _check_rate_limit(_get_client_ip(request)):
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
            await _persist_state()
        message_events[msg.to].set()
        _sse_push(msg.to, message)
        logger.info("Message %s: %s -> %s", message["id"], msg.from_name, msg.to)
        _notify_webhook(msg.to, message)
        return {"id": message["id"], "timestamp": message["timestamp"]}


@app.post("/stream/token", dependencies=[Depends(verify_auth)])
async def create_sse_token(request: Request):
    """Exchange relay auth for a short-lived SSE token (avoids secret in URL)."""
    body = await request.json()
    recipient = body.get("recipient", "")
    if not recipient:
        raise HTTPException(status_code=400, detail="recipient is required")
    token = str(uuid.uuid4())
    _sse_tokens[token] = {
        "recipient": recipient,
        "expires": time.time() + SSE_TOKEN_TTL,
    }
    return {"token": token, "expires_in": SSE_TOKEN_TTL}


def _verify_sse_token(token: str, recipient: str) -> bool:
    """Validate an SSE token for the given recipient."""
    entry = _sse_tokens.get(token)
    if not entry:
        return False
    if time.time() > entry["expires"]:
        _sse_tokens.pop(token, None)
        return False
    return entry["recipient"] == recipient


@app.get("/stream/{recipient}")
async def stream_messages(recipient: str, token: str = ""):
    """SSE endpoint for real-time message delivery."""
    # Accept either a short-lived SSE token or the relay secret (backward compat)
    if not (_verify_sse_token(token, recipient) or hmac.compare_digest(token, RELAY_SECRET)):
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
    callback_url = await _validate_webhook_callback_url(req.callback_url)
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
    async with rooms_lock:
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
    async with rooms_lock:
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
    async with rooms_lock:
        room_id, room = _resolve_room(room_id)
        room["members"].discard(req.participant)
    await _persist_state()
    return {"status": "left"}


@app.post("/rooms/{room_id}/messages", dependencies=[Depends(verify_auth)])
async def send_room_message(room_id: str, msg: RoomMessageRequest, request: Request):
    """Send a message to all members of a room."""
    if not _check_rate_limit(_get_client_ip(request)):
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
    # Snapshot members to avoid RuntimeError from concurrent join/leave
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
        }
        async with locks[recipient]:
            message_queues[recipient].append(fan_out_msg)
        message_events[recipient].set()
        _sse_push(recipient, fan_out_msg)
        _notify_webhook(recipient, fan_out_msg)

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

SSE_TOKEN_TTL = int(os.environ.get("SSE_TOKEN_TTL", "300"))  # 5 minutes
_sse_tokens: dict[str, dict] = {}  # token -> {recipient, expires}

INVITE_SECRET = os.environ.get("INVITE_SECRET", RELAY_SECRET)
INVITE_TTL = int(os.environ.get("INVITE_TTL", str(24 * 60 * 60)))  # 24h default


def _make_invite_token(room_name: str) -> str:
    """Generate an HMAC-based, room-scoped, time-limited invite token."""
    expires = int(time.time()) + INVITE_TTL
    payload = f"{room_name}:{expires}"
    sig = hmac.new(INVITE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify_invite_token(token: str, room_name: str) -> bool:
    """Verify an invite token is valid for the given room and not expired."""
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
    expected_sig = hmac.new(
        INVITE_SECRET.encode(), expected_payload.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


@app.get("/invite/{room_name}", response_class=HTMLResponse)
async def invite_page(room_name: str, request: Request):
    """Discord-style invite page. Anyone with the link can join."""
    # Verify room exists
    rid = _find_room_by_name(room_name)
    if not rid and room_name not in rooms:
        raise HTTPException(status_code=404, detail="Room not found")

    relay_url = str(request.base_url).rstrip("/")
    invite_token = _make_invite_token(room_name)
    html = _INVITE_TMPL.substitute(
        room_name=room_name,
        relay_url=relay_url,
        token=invite_token,
    )
    return HTMLResponse(content=html)


class InviteJoinRequest(BaseModel):
    participant: str
    token: str

    @field_validator("participant")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


@app.post("/invite/{room_name}/join")
async def invite_join(room_name: str, req: InviteJoinRequest):
    """Join a room using a scoped invite token (no RELAY_SECRET needed)."""
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
    await _persist_state()
    return {"status": "joined"}


_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Murmur Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9}
header{background:#161b22;border-bottom:1px solid #30363d;padding:12px 24px;
  display:flex;align-items:center;gap:12px}
header h1{font-size:18px;color:#58a6ff}
header .status{font-size:12px;color:#3fb950;margin-left:auto}
.container{display:flex;height:calc(100vh - 49px)}
.sidebar{width:240px;background:#161b22;border-right:1px solid #30363d;
  overflow-y:auto;flex-shrink:0}
.sidebar h3{padding:12px 16px 8px;font-size:12px;color:#8b949e;
  text-transform:uppercase}
.room-item{padding:8px 16px;cursor:pointer;border-left:3px solid transparent;
  font-size:14px;color:#c9d1d9}
.room-item:hover{background:#1c2128}
.room-item.active{background:#1c2128;border-left-color:#58a6ff;color:#58a6ff}
.room-item .count{font-size:11px;color:#8b949e;float:right}
.main{flex:1;display:flex;flex-direction:column}
.messages{flex:1;overflow-y:auto;padding:16px;
  display:flex;flex-direction:column;gap:4px}
.msg{font-size:13px;line-height:1.5}
.msg .ts{color:#484f58;font-size:11px;margin-right:6px}
.msg .sender{font-weight:600;color:#58a6ff;margin-right:4px}
.msg .tag{font-size:11px;padding:1px 5px;border-radius:3px;margin-right:4px}
.tag-claim{background:#9e6a03;color:#fff}
.tag-status{background:#1f6feb;color:#fff}
.tag-request{background:#8957e5;color:#fff}
.tag-alert{background:#da3633;color:#fff}
.tag-sync{background:#238636;color:#fff}
.input-bar{border-top:1px solid #30363d;padding:12px 16px;display:flex;gap:8px}
.input-bar input{flex:1;background:#0d1117;border:1px solid #30363d;
  border-radius:6px;padding:8px 12px;color:#c9d1d9;font-size:14px;outline:none}
.input-bar input:focus{border-color:#58a6ff}
.input-bar button{background:#238636;color:#fff;border:none;border-radius:6px;
  padding:8px 16px;cursor:pointer;font-size:14px}
.input-bar button:hover{background:#2ea043}
.members{padding:8px 16px;border-top:1px solid #30363d;
  font-size:12px;color:#8b949e}
.members .member{display:inline-flex;align-items:center;gap:4px;margin-right:10px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.dot-online{background:#3fb950}
.dot-offline{background:#484f58}
.empty{color:#484f58;text-align:center;padding:40px;font-size:14px}
@media(max-width:640px){
  .sidebar{display:none}
  .container{flex-direction:column}
  .msg{font-size:12px}
  .input-bar input{font-size:16px}
}
</style>
</head>
<body>
<header>
  <h1>murmur</h1>
  <span class="status" id="status">connecting...</span>
</header>
<div class="container">
  <div class="sidebar">
    <h3>Rooms</h3>
    <div id="rooms"><div class="empty">Loading...</div></div>
  </div>
  <div class="main">
    <div class="messages" id="messages">
      <div class="empty">Select a room to view messages</div>
    </div>
    <div class="members" id="members"></div>
    <div class="input-bar">
      <input id="msgInput" placeholder="Type a message..." disabled>
      <button id="sendBtn" onclick="sendMsg()" disabled>Send</button>
    </div>
  </div>
</div>
<script>
const API=location.origin;
const P=new URLSearchParams(location.search);
const TOKEN=P.get('token')||'';
const NAME=P.get('name')||'web-user';
const H={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'};
let currentRoom=null,sse=null;
function esc(s){const d=document.createElement('div');d.textContent=String(s);return d.innerHTML}

async function loadRooms(){
  try{
    const r=await fetch(API+'/rooms',{headers:H});
    if(!r.ok){document.getElementById('status').textContent='auth failed';return}
    const rooms=await r.json();
    document.getElementById('status').textContent='connected';
    const el=document.getElementById('rooms');
    if(!rooms.length){el.innerHTML='<div class="empty">No rooms</div>';return}
    el.innerHTML=rooms.map(rm=>'<div class="room-item" onclick="selectRoom(\''+
      esc(rm.name)+'\')">'+esc(rm.name)+'<span class="count">'+
      esc(rm.members.length)+'</span></div>').join('');
    if(currentRoom)document.querySelectorAll('.room-item').forEach(e=>{
      if(e.textContent.startsWith(currentRoom))e.classList.add('active');
    });
  }catch(e){document.getElementById('status').textContent='offline'}
}

async function selectRoom(name){
  currentRoom=name;
  document.querySelectorAll('.room-item').forEach(e=>{
    e.classList.toggle('active',e.textContent.startsWith(name));
  });
  document.getElementById('msgInput').disabled=false;
  document.getElementById('sendBtn').disabled=false;
  try{
    const r=await fetch(API+'/rooms/'+name+'/history?limit=100',{headers:H});
    const msgs=await r.json();
    const el=document.getElementById('messages');
    el.innerHTML=msgs.map(formatMsg).join('');
    el.scrollTop=el.scrollHeight;
  }catch(e){}
  try{
    const r=await fetch(API+'/rooms/'+name,{headers:H});
    const room=await r.json();
    const pr=await fetch(API+'/presence',{headers:H});
    const presence=await pr.json();
    const onlineSet=new Set(presence.filter(p=>p.online).map(p=>p.name));
    document.getElementById('members').innerHTML='Members: '+
      room.members.map(m=>{
        const dot=onlineSet.has(m)?'dot-online':'dot-offline';
        return '<span class="member"><span class="dot '+dot+'"></span>'+esc(m)+'</span>';
      }).join('');
  }catch(e){}
  connectSSE();
}

async function connectSSE(){
  if(sse)sse.close();
  let sseToken=TOKEN;
  try{
    const r=await fetch(API+'/stream/token',{method:'POST',headers:H,
      body:JSON.stringify({recipient:NAME})});
    if(r.ok){const d=await r.json();sseToken=d.token;}
  }catch(e){}
  sse=new EventSource(API+'/stream/'+NAME+'?token='+sseToken);
  sse.addEventListener('message',e=>{
    try{
      const msg=JSON.parse(e.data);
      if(msg.room===currentRoom){
        const el=document.getElementById('messages');
        el.innerHTML+=formatMsg(msg);
        el.scrollTop=el.scrollHeight;
      }
    }catch(e){}
  });
}

function formatMsg(msg){
  const ts=esc((msg.timestamp||'').substring(11,19));
  const type=msg.message_type||'chat';
  const safeType=['claim','status','request','alert','sync'].includes(type)?type:'chat';
  let tag='';
  if(safeType!=='chat')tag='<span class="tag tag-'+safeType+'">'+esc(type)+'</span>';
  return '<div class="msg"><span class="ts">'+ts+
    '</span><span class="sender">'+esc(msg.from_name||'?')+
    '</span>'+tag+esc(msg.content||'')+'</div>';
}

async function sendMsg(){
  const input=document.getElementById('msgInput');
  const text=input.value.trim();
  if(!text||!currentRoom)return;
  input.value='';
  try{await fetch(API+'/rooms/'+currentRoom+'/messages',{
    method:'POST',headers:H,
    body:JSON.stringify({from_name:NAME,content:text})
  })}catch(e){}
}

document.getElementById('msgInput').addEventListener('keydown',
  e=>{if(e.key==='Enter')sendMsg()});

async function refreshPresence(){
  if(!currentRoom)return;
  try{
    const r=await fetch(API+'/rooms/'+currentRoom,{headers:H});
    const room=await r.json();
    const pr=await fetch(API+'/presence',{headers:H});
    const presence=await pr.json();
    const onlineSet=new Set(presence.filter(p=>p.online).map(p=>p.name));
    document.getElementById('members').innerHTML='Members: '+
      room.members.map(m=>{
        const dot=onlineSet.has(m)?'dot-online':'dot-offline';
        return '<span class="member"><span class="dot '+dot+'"></span>'+esc(m)+'</span>';
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
