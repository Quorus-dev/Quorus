import asyncio
import json
import logging
import os
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

RELAY_SECRET = os.environ.get("RELAY_SECRET", "test-secret")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("claude_tunnel.relay")

@asynccontextmanager
async def lifespan(app):
    logger.info("Relay server starting up")
    _load_from_file()
    yield
    logger.info("Relay server shutting down")


app = FastAPI(title="Claude Tunnel Relay", lifespan=lifespan)

# In-memory state
message_queues: dict[str, list[dict]] = defaultdict(list)
participants: set[str] = set()
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "1000"))


def _reset_state():
    """Reset state between tests."""
    message_queues.clear()
    participants.clear()
    locks.clear()


def _save_to_file():
    """Save current state to JSON file."""
    data = {
        "messages": {k: list(v) for k, v in message_queues.items()},
        "participants": sorted(participants),
    }
    try:
        with open(MESSAGES_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        logger.error("Failed to save state to %s", MESSAGES_FILE)


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
    logger.info("Loaded state from %s", MESSAGES_FILE)


def _trim_messages():
    """If total messages exceed MAX_MESSAGES, remove oldest across all queues."""
    all_messages = []
    for recipient, msgs in message_queues.items():
        for msg in msgs:
            all_messages.append((recipient, msg))

    if len(all_messages) <= MAX_MESSAGES:
        return

    all_messages.sort(key=lambda x: x[1]["timestamp"])
    to_remove = len(all_messages) - MAX_MESSAGES
    logger.info("Trimming %d oldest messages (total %d > cap %d)", to_remove, len(all_messages), MAX_MESSAGES)
    remove_set = set()
    for i in range(to_remove):
        remove_set.add(all_messages[i][1]["id"])

    for recipient in message_queues:
        message_queues[recipient] = [
            m for m in message_queues[recipient] if m["id"] not in remove_set
        ]


async def verify_auth(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {RELAY_SECRET}":
        logger.warning("Auth failure from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")


class SendMessageRequest(BaseModel):
    from_name: str
    to: str
    content: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/messages", dependencies=[Depends(verify_auth)])
async def send_message(msg: SendMessageRequest):
    message = {
        "id": str(uuid.uuid4()),
        "from_name": msg.from_name,
        "to": msg.to,
        "content": msg.content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    async with locks[msg.to]:
        message_queues[msg.to].append(message)
        participants.add(msg.from_name)
        _trim_messages()
        _save_to_file()
    logger.info("Message %s: %s -> %s", message["id"], msg.from_name, msg.to)
    return {"id": message["id"], "timestamp": message["timestamp"]}


@app.get("/messages/{recipient}", dependencies=[Depends(verify_auth)])
async def get_messages(recipient: str):
    async with locks[recipient]:
        messages = list(message_queues[recipient])
        message_queues[recipient].clear()
    logger.info("Fetched %d messages for %s", len(messages), recipient)
    return messages


@app.get("/participants", dependencies=[Depends(verify_auth)])
async def list_participants_endpoint():
    return sorted(participants)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
