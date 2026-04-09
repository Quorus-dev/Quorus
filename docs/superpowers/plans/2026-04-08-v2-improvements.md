# Claude Tunnel v2 Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured logging, message chunking, per-participant analytics with terminal charts, push notifications via MCP Channels, long-polling fallback, and fix the README.

**Architecture:** Six incremental features layered onto the existing relay server + MCP server. Each task is self-contained and produces a working, testable commit. Logging goes first (used by everything else), then chunking, analytics, long-polling, channels, and finally README.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, httpx, mcp, rich, pytest, pytest-asyncio

---

### Task 1: Structured Logging — Relay Server

**Files:**
- Modify: `relay_server.py`
- Modify: `tests/test_relay.py`

- [ ] **Step 1: Add logging setup to relay_server.py**

Add logging imports and configuration at the top of `relay_server.py`, after existing imports:

```python
import logging

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("claude_tunnel.relay")
```

- [ ] **Step 2: Add log statements throughout relay_server.py**

In `lifespan`:
```python
@asynccontextmanager
async def lifespan(app):
    logger.info("Relay server starting up")
    _load_from_file()
    yield
    logger.info("Relay server shutting down")
```

In `_save_to_file`:
```python
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
```

In `_load_from_file`:
```python
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
```

In `_trim_messages`, after the early return:
```python
    logger.info("Trimming %d oldest messages (total %d > cap %d)", to_remove, len(all_messages), MAX_MESSAGES)
```

In `verify_auth`, on failure:
```python
async def verify_auth(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {RELAY_SECRET}":
        logger.warning("Auth failure from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")
```

In `send_message`:
```python
    logger.info("Message %s: %s -> %s", message["id"], msg.from_name, msg.to)
```

In `get_messages`:
```python
    logger.info("Fetched %d messages for %s", len(messages), recipient)
```

- [ ] **Step 3: Run existing tests to confirm nothing breaks**

Run: `pytest tests/test_relay.py -v`
Expected: All 11 tests pass (logging adds no behavior changes)

- [ ] **Step 4: Commit**

```bash
git add relay_server.py
git commit -m "feat: add structured logging to relay server"
```

---

### Task 2: Structured Logging — MCP Server

**Files:**
- Modify: `mcp_server.py`

- [ ] **Step 1: Add logging setup to mcp_server.py**

Add after existing imports:

```python
import logging

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("claude_tunnel.mcp")
```

- [ ] **Step 2: Add log statements throughout mcp_server.py**

In `_load_config`, after building the config dict:
```python
    config = {
        "relay_url": get("RELAY_URL", "relay_url", "http://localhost:8080"),
        "relay_secret": get("RELAY_SECRET", "relay_secret", "test-secret"),
        "instance_name": get("INSTANCE_NAME", "instance_name", "default"),
    }
    masked_secret = config["relay_secret"][:4] + "***" if len(config["relay_secret"]) > 4 else "***"
    logger.info(
        "Config loaded: relay_url=%s, instance=%s, secret=%s",
        config["relay_url"], config["instance_name"], masked_secret,
    )
    return config
```

In `_send_message`, after successful send:
```python
        logger.info("Sent message to %s (id: %s)", to, data["id"])
```

In `_send_message`, in the except blocks:
```python
    except httpx.ConnectError:
        logger.warning("Cannot reach relay at %s", RELAY_URL)
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        logger.warning("Relay returned %d: %s", e.response.status_code, e.response.text)
        return f"Error: Relay returned {e.response.status_code}: {e.response.text}"
```

In `_check_messages`, after successful fetch:
```python
        logger.info("Received %d messages", len(messages))
```

In `_check_messages`, in the except blocks (same pattern as `_send_message`).

In `_list_participants`, after successful fetch:
```python
        logger.info("Listed %d participants", len(participants))
```

In `_list_participants`, in the except blocks (same pattern as `_send_message`).

- [ ] **Step 3: Run existing tests to confirm nothing breaks**

Run: `pytest tests/test_mcp.py -v`
Expected: All 4 tests pass

- [ ] **Step 4: Commit**

```bash
git add mcp_server.py
git commit -m "feat: add structured logging to MCP server"
```

---

### Task 3: Message Chunking

**Files:**
- Modify: `relay_server.py`
- Modify: `tests/test_relay.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_relay.py`:

```python
async def test_small_message_has_no_chunk_fields(client: AsyncClient, auth_headers: dict):
    """Messages under the size limit should have no chunk metadata."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "small"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob", headers=auth_headers)
    msg = resp.json()[0]
    assert "chunk_group" not in msg


async def test_large_message_is_chunked_and_reassembled(client: AsyncClient, auth_headers: dict):
    """A message exceeding MAX_MESSAGE_SIZE should be chunked on send and reassembled on fetch."""
    large_content = "x" * 200  # Will exceed our patched limit

    with patch("relay_server.MAX_MESSAGE_SIZE", 100):
        resp = await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": large_content},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        resp = await client.get("/messages/bob", headers=auth_headers)
        messages = resp.json()
        # Should be reassembled into a single message
        assert len(messages) == 1
        assert messages[0]["content"] == large_content
        assert "chunk_group" not in messages[0]


async def test_incomplete_chunks_held_back(client: AsyncClient, auth_headers: dict):
    """If not all chunks have arrived, they should not be returned."""
    from relay_server import locks, message_queues

    # Manually insert an incomplete chunk group
    async with locks["bob"]:
        message_queues["bob"].append({
            "id": "chunk-1",
            "from_name": "alice",
            "to": "bob",
            "content": "part1",
            "timestamp": "2026-04-08T00:00:00Z",
            "chunk_group": "group-1",
            "chunk_index": 0,
            "chunk_total": 2,
        })

    resp = await client.get("/messages/bob", headers=auth_headers)
    messages = resp.json()
    # Incomplete group should be held back
    assert len(messages) == 0

    # Now add the second chunk
    async with locks["bob"]:
        message_queues["bob"].append({
            "id": "chunk-2",
            "from_name": "alice",
            "to": "bob",
            "content": "part2",
            "timestamp": "2026-04-08T00:00:01Z",
            "chunk_group": "group-1",
            "chunk_index": 1,
            "chunk_total": 2,
        })

    resp = await client.get("/messages/bob", headers=auth_headers)
    messages = resp.json()
    assert len(messages) == 1
    assert messages[0]["content"] == "part1part2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py::test_large_message_is_chunked_and_reassembled tests/test_relay.py::test_incomplete_chunks_held_back tests/test_relay.py::test_small_message_has_no_chunk_fields -v`
Expected: FAIL (no chunking logic, no `MAX_MESSAGE_SIZE`)

- [ ] **Step 3: Add MAX_MESSAGE_SIZE constant to relay_server.py**

Add after the `MAX_MESSAGES` line (line 28):

```python
MAX_MESSAGE_SIZE = int(os.environ.get("MAX_MESSAGE_SIZE", "51200"))
```

- [ ] **Step 4: Implement chunking in send_message**

Replace the `send_message` endpoint in `relay_server.py`:

```python
@app.post("/messages", dependencies=[Depends(verify_auth)])
async def send_message(msg: SendMessageRequest):
    timestamp = datetime.now(timezone.utc).isoformat()
    content = msg.content

    if len(content.encode("utf-8")) > MAX_MESSAGE_SIZE:
        # Chunk the message
        chunk_group = str(uuid.uuid4())
        chunks = []
        encoded = content.encode("utf-8")
        for i in range(0, len(encoded), MAX_MESSAGE_SIZE):
            chunk_bytes = encoded[i : i + MAX_MESSAGE_SIZE]
            chunks.append(chunk_bytes.decode("utf-8", errors="replace"))
        chunk_total = len(chunks)

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
            participants.add(msg.from_name)
            _trim_messages()
            _save_to_file()
        logger.info(
            "Message chunked into %d parts (%s -> %s, group %s)",
            chunk_total, msg.from_name, msg.to, chunk_group,
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
            participants.add(msg.from_name)
            _trim_messages()
            _save_to_file()
        logger.info("Message %s: %s -> %s", message["id"], msg.from_name, msg.to)
        return {"id": message["id"], "timestamp": message["timestamp"]}
```

- [ ] **Step 5: Implement reassembly in get_messages**

Add the reassembly helper and replace the `get_messages` endpoint in `relay_server.py`:

```python
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
            # All chunks present — reassemble
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

    # Sort ready messages by timestamp to preserve ordering
    ready.sort(key=lambda m: m["timestamp"])
    return ready, held_back


@app.get("/messages/{recipient}", dependencies=[Depends(verify_auth)])
async def get_messages(recipient: str):
    async with locks[recipient]:
        all_msgs = list(message_queues[recipient])
        ready, held_back = _reassemble_chunks(all_msgs)
        message_queues[recipient] = held_back
    logger.info("Fetched %d messages for %s (%d chunks held back)", len(ready), recipient, len(held_back))
    return ready
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_relay.py -v`
Expected: All tests pass (old + new)

- [ ] **Step 7: Commit**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: add transparent message chunking with reassembly"
```

---

### Task 4: Analytics — Relay Server Counters + Endpoint

**Files:**
- Modify: `relay_server.py`
- Modify: `tests/test_relay.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_relay.py`:

```python
async def test_analytics_empty(client: AsyncClient, auth_headers: dict):
    """Analytics endpoint should return zeros when no messages have been sent."""
    resp = await client.get("/analytics", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_messages_sent"] == 0
    assert data["total_messages_delivered"] == 0
    assert data["messages_pending"] == 0
    assert data["participants"] == {}
    assert isinstance(data["hourly_volume"], list)
    assert "uptime_seconds" in data


async def test_analytics_after_send_and_receive(client: AsyncClient, auth_headers: dict):
    """Analytics should track sends and deliveries."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hello"},
        headers=auth_headers,
    )

    resp = await client.get("/analytics", headers=auth_headers)
    data = resp.json()
    assert data["total_messages_sent"] == 2
    assert data["total_messages_delivered"] == 0
    assert data["messages_pending"] == 2
    assert data["participants"]["alice"]["sent"] == 2

    # Now bob fetches
    await client.get("/messages/bob", headers=auth_headers)

    resp = await client.get("/analytics", headers=auth_headers)
    data = resp.json()
    assert data["total_messages_delivered"] == 2
    assert data["messages_pending"] == 0
    assert data["participants"]["bob"]["received"] == 2


async def test_analytics_hourly_volume(client: AsyncClient, auth_headers: dict):
    """Analytics should track hourly message volume."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    resp = await client.get("/analytics", headers=auth_headers)
    data = resp.json()
    assert len(data["hourly_volume"]) >= 1
    assert data["hourly_volume"][0]["count"] >= 1


async def test_analytics_requires_auth(client: AsyncClient):
    """Analytics endpoint should require auth."""
    resp = await client.get("/analytics")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py::test_analytics_empty tests/test_relay.py::test_analytics_after_send_and_receive tests/test_relay.py::test_analytics_hourly_volume tests/test_relay.py::test_analytics_requires_auth -v`
Expected: FAIL (no `/analytics` endpoint)

- [ ] **Step 3: Add analytics state and helpers to relay_server.py**

Add `import time` and `timedelta` to imports (update `from datetime import datetime, timezone` to `from datetime import datetime, timedelta, timezone`).

Add after the `MAX_MESSAGE_SIZE` constant:

```python
# Analytics state
analytics: dict = {
    "total_sent": 0,
    "total_delivered": 0,
    "per_participant": {},
    "hourly_volume": {},
}
_start_time = time.time()
```

Update `_reset_state` to also reset analytics:

```python
def _reset_state():
    """Reset state between tests."""
    global _start_time
    message_queues.clear()
    participants.clear()
    locks.clear()
    analytics["total_sent"] = 0
    analytics["total_delivered"] = 0
    analytics["per_participant"].clear()
    analytics["hourly_volume"].clear()
    _start_time = time.time()
```

Add helper functions:

```python
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
```

- [ ] **Step 4: Wire analytics into send_message and get_messages**

In `send_message`, inside both the chunked and non-chunked lock blocks, after `participants.add(msg.from_name)`, add:

```python
            _track_send(msg.from_name)
```

In `get_messages`, after the lock block releases, add:

```python
    _track_delivery(recipient, len(ready))
```

- [ ] **Step 5: Add the /analytics endpoint**

Add to `relay_server.py`:

```python
@app.get("/analytics", dependencies=[Depends(verify_auth)])
async def get_analytics():
    _prune_hourly_volume()
    pending = sum(len(msgs) for msgs in message_queues.values())
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
```

- [ ] **Step 6: Update persistence to include analytics**

Update `_save_to_file` to include analytics in the data dict:

```python
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
```

Update `_load_from_file` to load analytics (add after the participants loading loop):

```python
    saved_analytics = data.get("analytics", {})
    analytics["total_sent"] = saved_analytics.get("total_sent", 0)
    analytics["total_delivered"] = saved_analytics.get("total_delivered", 0)
    analytics["per_participant"] = saved_analytics.get("per_participant", {})
    analytics["hourly_volume"] = saved_analytics.get("hourly_volume", {})
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_relay.py -v`
Expected: All tests pass (old + new)

- [ ] **Step 8: Commit**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: add per-participant analytics with persistence"
```

---

### Task 5: Analytics CLI Tool

**Files:**
- Create: `analytics.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add rich to requirements.txt**

Add to `requirements.txt`:

```
rich>=13.0.0
```

- [ ] **Step 2: Create analytics.py**

```python
"""CLI tool for viewing Claude Tunnel analytics in the terminal."""

import json
import sys
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

CONFIG_DIR = Path.home() / "claude-tunnel"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Load config from ~/claude-tunnel/config.json."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def fetch_analytics(relay_url: str, relay_secret: str) -> dict:
    """Fetch analytics data from the relay server."""
    resp = httpx.get(
        f"{relay_url}/analytics",
        headers={"Authorization": f"Bearer {relay_secret}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def render(data: dict) -> None:
    """Render analytics data as terminal tables and charts."""
    console = Console()

    # Summary table
    summary = Table(title="Relay Summary")
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Total Sent", str(data["total_messages_sent"]))
    summary.add_row("Total Delivered", str(data["total_messages_delivered"]))
    summary.add_row("Pending", str(data["messages_pending"]))
    summary.add_row("Uptime", f"{data['uptime_seconds']}s")
    console.print(summary)
    console.print()

    # Participant table
    if data["participants"]:
        ptable = Table(title="Participants")
        ptable.add_column("Name", style="bold")
        ptable.add_column("Sent", justify="right")
        ptable.add_column("Received", justify="right")
        for name, stats in sorted(data["participants"].items()):
            ptable.add_row(name, str(stats["sent"]), str(stats["received"]))
        console.print(ptable)
        console.print()

    # Hourly volume bar chart
    hourly = data.get("hourly_volume", [])
    if hourly:
        max_count = max(h["count"] for h in hourly)
        bar_width = 40
        console.print("[bold]Hourly Volume (last 72h)[/bold]")
        for entry in hourly:
            hour_label = entry["hour"][11:16]  # Extract HH:MM
            count = entry["count"]
            bar_len = int((count / max_count) * bar_width) if max_count > 0 else 0
            bar = "\u2588" * bar_len
            console.print(f"  {hour_label} \u2502 {bar} {count}")
        console.print()


def main():
    config = load_config()
    relay_url = config.get("relay_url", "http://localhost:8080")
    relay_secret = config.get("relay_secret", "test-secret")

    try:
        data = fetch_analytics(relay_url, relay_secret)
    except httpx.ConnectError:
        print(f"Error: Cannot reach relay at {relay_url}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"Error: Relay returned {e.response.status_code}", file=sys.stderr)
        sys.exit(1)

    render(data)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add analytics.py requirements.txt
git commit -m "feat: add terminal analytics CLI with rich charts"
```

---

### Task 6: Long-Polling Fallback

**Files:**
- Modify: `relay_server.py`
- Modify: `tests/test_relay.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_relay.py`:

```python
async def test_long_poll_returns_immediately_with_messages(client: AsyncClient, auth_headers: dict):
    """Long-poll should return immediately if messages are already queued."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hi"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob?wait=10", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_long_poll_returns_empty_on_timeout(client: AsyncClient, auth_headers: dict):
    """Long-poll should return empty list after timeout if no messages arrive."""
    resp = await client.get("/messages/nobody?wait=1", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_long_poll_wakes_on_new_message(client: AsyncClient, auth_headers: dict):
    """Long-poll should return as soon as a message arrives."""
    import time as time_mod

    async def poll():
        return await client.get("/messages/bob?wait=10", headers=auth_headers)

    async def send_delayed():
        await asyncio.sleep(0.5)
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "wake up"},
            headers=auth_headers,
        )

    start = time_mod.monotonic()
    poll_task = asyncio.create_task(poll())
    send_task = asyncio.create_task(send_delayed())

    resp = await poll_task
    await send_task
    elapsed = time_mod.monotonic() - start

    assert len(resp.json()) == 1
    assert resp.json()[0]["content"] == "wake up"
    # Should have returned well before the 10s timeout
    assert elapsed < 5


async def test_long_poll_clamps_max_wait(client: AsyncClient, auth_headers: dict):
    """Wait values above 60 should be clamped to 60."""
    # We can't easily test the clamp directly, but we can verify the request works
    resp = await client.get("/messages/nobody?wait=120", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_no_wait_param_returns_immediately(client: AsyncClient, auth_headers: dict):
    """Without wait param, should return immediately even if empty."""
    resp = await client.get("/messages/nobody", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py::test_long_poll_wakes_on_new_message -v`
Expected: FAIL (no `wait` param support — though the test may pass vacuously since the endpoint ignores unknown query params and returns empty immediately; `test_long_poll_wakes_on_new_message` will fail because the message won't be returned)

- [ ] **Step 3: Add per-recipient events to relay_server.py**

Add to the in-memory state section:

```python
message_events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
```

Update `_reset_state` to add:

```python
    message_events.clear()
```

- [ ] **Step 4: Signal event in send_message**

In `send_message`, after both the chunked and non-chunked lock blocks release (after `_save_to_file()`), add:

```python
    message_events[msg.to].set()
    message_events[msg.to].clear()
```

- [ ] **Step 5: Implement long-polling in get_messages**

Replace the `get_messages` endpoint:

```python
@app.get("/messages/{recipient}", dependencies=[Depends(verify_auth)])
async def get_messages(recipient: str, wait: int = 0):
    wait = min(max(wait, 0), 60)  # Clamp to 0-60

    async with locks[recipient]:
        all_msgs = list(message_queues[recipient])
        ready, held_back = _reassemble_chunks(all_msgs)
        message_queues[recipient] = held_back

    if not ready and wait > 0:
        try:
            await asyncio.wait_for(message_events[recipient].wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        # Re-check after waking
        async with locks[recipient]:
            all_msgs = list(message_queues[recipient])
            ready, held_back = _reassemble_chunks(all_msgs)
            message_queues[recipient] = held_back

    _track_delivery(recipient, len(ready))
    logger.info(
        "Fetched %d messages for %s (%d chunks held back)",
        len(ready), recipient, len(held_back),
    )
    return ready
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_relay.py -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: add long-polling fallback for message fetching"
```

---

### Task 7: Webhooks — Relay Server Side

**Files:**
- Modify: `relay_server.py`
- Modify: `tests/test_relay.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_relay.py`:

```python
async def test_register_webhook(client: AsyncClient, auth_headers: dict):
    """Should register a webhook for an instance."""
    resp = await client.post(
        "/webhooks",
        json={"instance_name": "bob", "callback_url": "http://localhost:9999/incoming"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "registered"


async def test_delete_webhook(client: AsyncClient, auth_headers: dict):
    """Should deregister a webhook."""
    await client.post(
        "/webhooks",
        json={"instance_name": "bob", "callback_url": "http://localhost:9999/incoming"},
        headers=auth_headers,
    )
    resp = await client.delete("/webhooks/bob", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"


async def test_delete_nonexistent_webhook(client: AsyncClient, auth_headers: dict):
    """Deleting a non-existent webhook should still return 200."""
    resp = await client.delete("/webhooks/nobody", headers=auth_headers)
    assert resp.status_code == 200


async def test_webhook_called_on_message(client: AsyncClient, auth_headers: dict):
    """When a webhook is registered, sending a message should POST to the callback."""
    from unittest.mock import AsyncMock, patch as mock_patch

    # Register webhook
    await client.post(
        "/webhooks",
        json={"instance_name": "bob", "callback_url": "http://localhost:9999/incoming"},
        headers=auth_headers,
    )

    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)

    with mock_patch("relay_server.httpx.AsyncClient", return_value=mock_client_instance):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "push this"},
            headers=auth_headers,
        )

    mock_client_instance.post.assert_called_once()
    call_args = mock_client_instance.post.call_args
    assert call_args[0][0] == "http://localhost:9999/incoming"
    assert call_args[1]["json"]["content"] == "push this"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py::test_register_webhook tests/test_relay.py::test_webhook_called_on_message -v`
Expected: FAIL (no `/webhooks` endpoints)

- [ ] **Step 3: Add webhook state and endpoints**

Add `import httpx` to relay_server.py imports.

Add to the in-memory state section:

```python
webhooks: dict[str, str] = {}  # instance_name -> callback_url
```

Update `_reset_state` to add:

```python
    webhooks.clear()
```

Add the Pydantic model:

```python
class RegisterWebhookRequest(BaseModel):
    instance_name: str
    callback_url: str
```

Add the endpoints:

```python
@app.post("/webhooks", dependencies=[Depends(verify_auth)])
async def register_webhook(req: RegisterWebhookRequest):
    webhooks[req.instance_name] = req.callback_url
    logger.info("Webhook registered: %s -> %s", req.instance_name, req.callback_url)
    return {"status": "registered"}


@app.delete("/webhooks/{instance_name}", dependencies=[Depends(verify_auth)])
async def delete_webhook(instance_name: str):
    webhooks.pop(instance_name, None)
    logger.info("Webhook removed: %s", instance_name)
    return {"status": "removed"}
```

- [ ] **Step 4: Add webhook push to send_message**

Add a helper function:

```python
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
        logger.warning("Webhook delivery failed for %s at %s", recipient, callback_url)
```

In `send_message`, after the lock block and after the event signal, add:

For the non-chunked branch:
```python
    await _notify_webhook(msg.to, message)
```

For the chunked branch:
```python
    await _notify_webhook(msg.to, messages[0])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_relay.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: add webhook registration and push notification on message send"
```

---

### Task 8: MCP Channels — MCP Server Push

**Files:**
- Modify: `mcp_server.py`
- Modify: `tests/test_mcp.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp.py`:

```python
async def test_webhook_receiver_handles_incoming():
    """The local webhook receiver should accept POST /incoming and queue it."""
    from mcp_server import _create_webhook_app, _incoming_queue

    # Clear the queue
    while not _incoming_queue.empty():
        _incoming_queue.get_nowait()

    webhook_app = _create_webhook_app()

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=webhook_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/incoming",
            json={
                "id": "msg-1",
                "from_name": "alice",
                "content": "pushed",
                "timestamp": "2026-04-08T12:00:00Z",
            },
        )
        assert resp.status_code == 200

    msg = _incoming_queue.get_nowait()
    assert msg["from_name"] == "alice"
    assert msg["content"] == "pushed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp.py::test_webhook_receiver_handles_incoming -v`
Expected: FAIL (no `_create_webhook_app`, no `_incoming_queue`)

- [ ] **Step 3: Add webhook receiver to mcp_server.py**

Add imports at the top of `mcp_server.py`:

```python
import asyncio
import queue
import socket
import threading

from fastapi import FastAPI as WebhookFastAPI
import uvicorn
```

Add after the `INSTANCE_NAME` assignment:

```python
# Queue for incoming webhook-pushed messages
_incoming_queue: queue.Queue = queue.Queue()


def _create_webhook_app() -> WebhookFastAPI:
    """Create the local webhook receiver FastAPI app."""
    webhook_app = WebhookFastAPI()

    @webhook_app.post("/incoming")
    async def incoming(request: dict):
        _incoming_queue.put(request)
        logger.info("Received push notification from %s", request.get("from_name", "unknown"))
        return {"status": "received"}

    return webhook_app


def _find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_webhook_server() -> int:
    """Start the local webhook HTTP server in a background thread. Returns the port."""
    port = _find_free_port()
    webhook_app = _create_webhook_app()

    def run():
        uvicorn.run(webhook_app, host="127.0.0.1", port=port, log_level="warning")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info("Webhook receiver started on 127.0.0.1:%d", port)
    return port


async def _register_webhook(port: int):
    """Register this instance's webhook with the relay."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RELAY_URL}/webhooks",
                json={
                    "instance_name": INSTANCE_NAME,
                    "callback_url": f"http://127.0.0.1:{port}/incoming",
                },
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            logger.info("Webhook registered with relay")
    except Exception:
        logger.warning("Failed to register webhook with relay - falling back to polling")


async def _deregister_webhook():
    """Deregister this instance's webhook from the relay."""
    try:
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{RELAY_URL}/webhooks/{INSTANCE_NAME}",
                headers=_auth_headers(),
            )
            logger.info("Webhook deregistered from relay")
    except Exception:
        logger.warning("Failed to deregister webhook")
```

- [ ] **Step 4: Add channel notification support**

Add after the webhook functions:

```python
_mcp_session = None  # Set when MCP server starts


async def _channel_notify(msg: dict):
    """Send a channel notification to Claude Code."""
    if _mcp_session is None:
        return
    try:
        formatted = f"[{msg.get('timestamp', '')}] {msg.get('from_name', 'unknown')}: {msg.get('content', '')}"
        await _mcp_session.send_notification(
            method="notifications/claude/channel",
            params={"channel": "claude-tunnel", "message": formatted},
        )
        logger.info("Channel notification sent for message from %s", msg.get("from_name"))
    except Exception:
        logger.warning("Failed to send channel notification")


async def _poll_incoming_queue():
    """Background task to drain the incoming queue and send channel notifications."""
    while True:
        try:
            msg = _incoming_queue.get_nowait()
            await _channel_notify(msg)
        except queue.Empty:
            await asyncio.sleep(0.1)
```

- [ ] **Step 5: Update MCP server initialization and check_messages to use long-polling**

Check the `FastMCP` constructor to see if it accepts a `capabilities` param. If it does:

```python
mcp = FastMCP("claude-tunnel", capabilities={"experimental": {"claude/channel": {}}})
```

If not, check if capabilities are set via a decorator or method (e.g., `mcp.capabilities(...)`) and use that. The key capability to declare is `{"experimental": {"claude/channel": {}}}`.

Update `_check_messages` to use long-polling — change the GET URL:

```python
            resp = await client.get(
                f"{RELAY_URL}/messages/{INSTANCE_NAME}?wait=30",
                headers=_auth_headers(),
                timeout=35,
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_mcp.py -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add mcp_server.py tests/test_mcp.py
git commit -m "feat: add MCP Channels push with local webhook receiver"
```

---

### Task 9: Fix README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace README.md contents**

Replace the full contents of `README.md` with:

````markdown
# Claude Tunnel

A relay-based MCP system that lets two Claude Code instances on different laptops communicate autonomously.

## How It Works

1. A central **relay server** (FastAPI) stores messages in per-recipient queues
2. Each Claude Code instance runs a local **MCP server** that exposes messaging tools
3. The MCP server makes HTTP calls to the relay
4. Messages are persisted to a JSON file on disk
5. **Push notifications** via MCP Channels deliver messages instantly (with long-polling fallback)

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the relay server (on the host's laptop)

```bash
RELAY_SECRET=my-shared-secret python relay_server.py
```

### 3. Expose via ngrok (on the host's laptop)

```bash
ngrok http 8080
```

Note the public URL (e.g., `https://abc123.ngrok.io`).

### 4. Configure Claude Code (both users)

Each user needs `mcp_server.py` and the `mcp` + `httpx` packages installed (`pip install mcp httpx`).

**Step 1: Write config file (`~/claude-tunnel/config.json`):**

```bash
mkdir -p ~/claude-tunnel
echo '{"relay_url": "https://your-tunnel-url", "relay_secret": "my-shared-secret", "instance_name": "alice"}' > ~/claude-tunnel/config.json
```

Replace `instance_name` with a unique name for each user (e.g., `"alice"` and `"bob"`).

**Step 2: Add the MCP server (one-time):**

```bash
claude mcp add claude-tunnel -s user -- python /absolute/path/to/mcp_server.py
```

To enable push notifications via Channels, add with `--channels`:

```bash
claude mcp add claude-tunnel -s user --channels -- python /absolute/path/to/mcp_server.py
```

**Update the tunnel URL later** (e.g., when tunnel restarts):

```bash
echo '{"relay_url": "https://new-url", "relay_secret": "my-shared-secret", "instance_name": "alice"}' > ~/claude-tunnel/config.json
```

No need to re-add the MCP server -- it reads `~/claude-tunnel/config.json` on startup. Just restart Claude Code.

Config is loaded with this priority: **env vars > ~/claude-tunnel/config.json > defaults**.

### 5. Use in Claude Code

Your Claude instance now has these tools:

- **send_message(to, content)** -- Send a message to another instance (large messages are auto-chunked)
- **check_messages()** -- Fetch unread messages (uses long-polling with 30s wait)
- **list_participants()** -- See who's connected

With Channels enabled, messages are pushed to your session automatically. Without Channels, poll manually:

```
/loop 10s check_messages
```

## Push Notifications

Claude Tunnel supports two mechanisms for real-time message delivery:

### MCP Channels (primary)

When enabled, the MCP server runs a local webhook receiver. The relay pushes new messages to it, which are then forwarded to Claude Code via `notifications/claude/channel`.

**Requirements:**
- Claude Code v2.1.80+
- MCP server added with `--channels` flag

### Long-Polling (fallback)

`check_messages` uses long-polling by default -- the relay holds the request open for up to 30 seconds, returning immediately when a message arrives. This works without any special flags.

The `wait` parameter is configurable on the raw API: `GET /messages/{recipient}?wait=N` (max 60 seconds).

## Analytics

View relay usage stats in the terminal:

```bash
python analytics.py
```

This shows:
- Total sent/delivered/pending message counts
- Per-participant sent/received breakdown
- Hourly message volume bar chart (last 72h)

The relay also exposes `GET /analytics` (requires auth) for programmatic access.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `RELAY_SECRET` | `test-secret` | Shared auth token |
| `PORT` | `8080` | Relay server port |
| `MESSAGES_FILE` | `messages.json` | Persistence file path |
| `MAX_MESSAGES` | `1000` | Max messages before trimming |
| `MAX_MESSAGE_SIZE` | `51200` | Max message size in bytes (larger messages are auto-chunked) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `RELAY_URL` | `http://localhost:8080` | Relay URL (MCP server) |
| `INSTANCE_NAME` | `default` | This instance's name (MCP server) |

## Project Structure

```
claude_tunnel/
├── relay_server.py         # FastAPI relay with analytics, webhooks, long-polling
├── mcp_server.py           # MCP server with channels + local webhook receiver
├── analytics.py            # CLI tool for terminal analytics charts
├── requirements.txt
├── requirements-dev.txt
├── tests/
│   ├── test_relay.py
│   ├── test_mcp.py
│   └── test_integration.py
├── pyproject.toml
└── README.md
```

## Development

```bash
pip install -r requirements-dev.txt
pytest -v
ruff check .
```
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README with channels, long-polling, analytics, and fix config path"
```

---

### Task 10: Final Integration Verification

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Add analytics check to the integration test**

Add to the end of `test_two_instances_conversation` in `tests/test_integration.py`, before the final "no more messages" checks:

```python
    # Analytics should reflect the conversation
    resp = await client.get("/analytics", headers=HEADERS)
    data = resp.json()
    assert data["total_messages_sent"] == 2
    assert data["total_messages_delivered"] == 2
    assert "alice" in data["participants"]
    assert "bob" in data["participants"]
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 3: Run the linter**

Run: `ruff check .`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add analytics verification to integration test"
```
