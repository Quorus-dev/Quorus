import asyncio
import ipaddress
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import re

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, field_validator

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mcp_tunnel.relay")

RELAY_SECRET = os.environ.get("RELAY_SECRET", "test-secret")
if RELAY_SECRET == "test-secret":
    logger.warning(
        "RELAY_SECRET is set to the default 'test-secret'. "
        "Set RELAY_SECRET env var to a strong secret before deploying."
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

app = FastAPI(title="MCP Tunnel Relay", lifespan=lifespan)

# In-memory state
message_queues: dict[str, list[dict]] = defaultdict(list)
participants: set[str] = set()
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
persistence_lock = asyncio.Lock()
message_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
webhooks: dict[str, str] = {}  # instance_name -> callback_url

MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "1000"))
MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))
MESSAGE_TTL_SECONDS = int(os.environ.get("MESSAGE_TTL_SECONDS", str(24 * 60 * 60)))  # default 24h

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
    if auth != f"Bearer {RELAY_SECRET}":
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


@app.get("/health")
async def health():
    return {"status": "ok"}


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
        logger.info(
            "Message chunked into %d parts (%s -> %s, group %s)",
            chunk_total, msg.from_name, msg.to, chunk_group,
        )
        await _notify_webhook(
            msg.to,
            {
                "id": messages[0]["id"],
                "from_name": msg.from_name,
                "to": msg.to,
                "content": content,
                "timestamp": timestamp,
            },
        )
        return {"id": messages[0]["id"], "timestamp": timestamp}
    else:
        message = {
            "id": str(uuid.uuid4()),
            "from_name": msg.from_name,
            "to": msg.to,
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
        logger.info("Message %s: %s -> %s", message["id"], msg.from_name, msg.to)
        await _notify_webhook(msg.to, message)
        return {"id": message["id"], "timestamp": message["timestamp"]}


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


@app.get("/participants", dependencies=[Depends(verify_auth)])
async def list_participants_endpoint():
    return sorted(participants)


@app.post("/webhooks", dependencies=[Depends(verify_auth)])
async def register_webhook(req: RegisterWebhookRequest):
    callback_url = _validate_webhook_callback_url(req.callback_url)
    webhooks[req.instance_name] = callback_url
    participants.add(req.instance_name)
    await _persist_state()
    logger.info("Webhook registered: %s -> %s", req.instance_name, callback_url)
    return {"status": "registered"}


@app.delete("/webhooks/{instance_name}", dependencies=[Depends(verify_auth)])
async def delete_webhook(instance_name: str):
    webhooks.pop(instance_name, None)
    logger.info("Webhook removed: %s", instance_name)
    return {"status": "removed"}


@app.get("/analytics", dependencies=[Depends(verify_auth)])
async def get_analytics():
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


def main() -> None:
    """Run the relay server as a CLI entrypoint."""
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
