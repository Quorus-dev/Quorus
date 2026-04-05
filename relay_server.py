import asyncio
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

RELAY_SECRET = os.environ.get("RELAY_SECRET", "test-secret")

app = FastAPI(title="Claude Tunnel Relay")

# In-memory state
message_queues: dict[str, list[dict]] = defaultdict(list)
participants: set[str] = set()
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _reset_state():
    """Reset state between tests."""
    message_queues.clear()
    participants.clear()
    locks.clear()


async def verify_auth(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {RELAY_SECRET}":
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
    return {"id": message["id"], "timestamp": message["timestamp"]}


@app.get("/messages/{recipient}", dependencies=[Depends(verify_auth)])
async def get_messages(recipient: str):
    async with locks[recipient]:
        messages = list(message_queues[recipient])
        message_queues[recipient].clear()
    return messages


@app.get("/participants", dependencies=[Depends(verify_auth)])
async def list_participants_endpoint():
    return sorted(participants)
