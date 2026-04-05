# Claude Tunnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an MCP relay system that lets two Claude Code instances on different laptops communicate autonomously via a central HTTP relay server.

**Architecture:** A FastAPI relay server stores messages in per-recipient queues with file-based persistence. Each Claude Code instance runs a local MCP server (stdio transport) that exposes `send_message`, `check_messages`, and `list_participants` tools, making HTTP calls to the relay.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, httpx, mcp (FastMCP), pytest, pytest-asyncio, ruff

---

## File Structure

| File | Responsibility |
|------|---------------|
| `relay_server.py` | FastAPI HTTP relay — message storage, persistence, auth, trimming |
| `mcp_server.py` | MCP stdio server — exposes tools, makes HTTP calls to relay |
| `requirements.txt` | Runtime dependencies |
| `requirements-dev.txt` | Dev dependencies (testing, linting) |
| `pyproject.toml` | Ruff config |
| `tests/test_relay.py` | Relay server integration tests |
| `tests/test_mcp.py` | MCP server unit tests |
| `README.md` | Setup and usage instructions |

---

### Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `pyproject.toml`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi>=0.110.0
uvicorn>=0.29.0
httpx>=0.27.0
mcp>=1.2.0
```

- [ ] **Step 2: Create `requirements-dev.txt`**

```
-r requirements.txt
ruff>=0.4.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[tool.ruff]
target-version = "py310"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "W"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: Create empty `tests/__init__.py`**

Empty file.

- [ ] **Step 5: Install dependencies**

Run: `pip install -r requirements-dev.txt`
Expected: All packages install successfully.

- [ ] **Step 6: Commit**

```bash
git init
git add requirements.txt requirements-dev.txt pyproject.toml tests/__init__.py
git commit -m "chore: project setup with dependencies and config"
```

---

### Task 2: Relay Server — Auth and Health Check

**Files:**
- Create: `relay_server.py`
- Create: `tests/test_relay.py`

- [ ] **Step 1: Write failing tests for auth and health**

In `tests/test_relay.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from relay_server import app


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-secret"}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_check(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_no_auth_returns_401(client: AsyncClient):
    resp = await client.get("/participants")
    assert resp.status_code == 401


async def test_wrong_auth_returns_401(client: AsyncClient):
    resp = await client.get("/participants", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


async def test_correct_auth_returns_200(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/participants", headers=auth_headers)
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py -v`
Expected: FAIL — `relay_server` module does not exist.

- [ ] **Step 3: Implement relay server skeleton with auth**

In `relay_server.py`:

```python
import os

from fastapi import Depends, FastAPI, HTTPException, Request

RELAY_SECRET = os.environ.get("RELAY_SECRET", "test-secret")

app = FastAPI(title="Claude Tunnel Relay")


async def verify_auth(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {RELAY_SECRET}":
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/participants", dependencies=[Depends(verify_auth)])
async def list_participants():
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_relay.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check relay_server.py tests/test_relay.py`
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: relay server skeleton with auth middleware"
```

---

### Task 3: Relay Server — Send and Receive Messages

**Files:**
- Modify: `relay_server.py`
- Modify: `tests/test_relay.py`

- [ ] **Step 1: Write failing tests for send and receive**

Append to `tests/test_relay.py`:

```python
async def test_send_message(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hello bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert "timestamp" in data


async def test_receive_messages(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "msg1"},
        headers=auth_headers,
    )
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "msg2"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 2
    assert messages[0]["content"] == "msg1"
    assert messages[1]["content"] == "msg2"
    assert messages[0]["from_name"] == "alice"
    assert "id" in messages[0]
    assert "timestamp" in messages[0]


async def test_receive_clears_messages(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "hello"},
        headers=auth_headers,
    )
    await client.get("/messages/bob", headers=auth_headers)
    resp = await client.get("/messages/bob", headers=auth_headers)
    assert resp.json() == []


async def test_receive_no_messages(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/messages/nobody", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_send_registers_participant(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/messages",
        json={"from_name": "charlie", "to": "dave", "content": "hi"},
        headers=auth_headers,
    )
    resp = await client.get("/participants", headers=auth_headers)
    participants = resp.json()
    assert "charlie" in participants
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `pytest tests/test_relay.py -v`
Expected: New tests FAIL — `/messages` endpoint does not exist.

- [ ] **Step 3: Implement message endpoints**

Replace the full content of `relay_server.py`:

```python
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
```

- [ ] **Step 4: Update test fixture to reset state between tests**

Add to `tests/test_relay.py`, after the imports:

```python
from relay_server import _reset_state


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_relay.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 6: Lint**

Run: `ruff check relay_server.py tests/test_relay.py`
Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: relay server send/receive messages with participant tracking"
```

---

### Task 4: Relay Server — File Persistence and Message Cap

**Files:**
- Modify: `relay_server.py`
- Modify: `tests/test_relay.py`

- [ ] **Step 1: Write failing tests for persistence and cap**

Append to `tests/test_relay.py`:

```python
import json
import tempfile
from unittest.mock import patch

from relay_server import _load_from_file, _save_to_file


async def test_file_persistence_save_and_load(client: AsyncClient, auth_headers: dict):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        filepath = f.name

    with patch("relay_server.MESSAGES_FILE", filepath):
        await client.post(
            "/messages",
            json={"from_name": "alice", "to": "bob", "content": "persist me"},
            headers=auth_headers,
        )
        _save_to_file()

        _reset_state()
        _load_from_file()

        resp = await client.get("/messages/bob", headers=auth_headers)
        messages = resp.json()
        assert len(messages) == 1
        assert messages[0]["content"] == "persist me"

    import os
    os.unlink(filepath)


async def test_message_cap_trims_oldest(client: AsyncClient, auth_headers: dict):
    with patch("relay_server.MAX_MESSAGES", 5):
        for i in range(7):
            await client.post(
                "/messages",
                json={"from_name": "alice", "to": "bob", "content": f"msg-{i}"},
                headers=auth_headers,
            )

        resp = await client.get("/messages/bob", headers=auth_headers)
        messages = resp.json()
        assert len(messages) == 5
        assert messages[0]["content"] == "msg-2"
        assert messages[-1]["content"] == "msg-6"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py::test_file_persistence_save_and_load tests/test_relay.py::test_message_cap_trims_oldest -v`
Expected: FAIL — `_load_from_file` and `_save_to_file` do not exist.

- [ ] **Step 3: Add persistence and cap to relay server**

Add the following to `relay_server.py` after the state variables:

```python
MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "1000"))


def _save_to_file():
    """Save current state to JSON file."""
    data = {
        "messages": {k: list(v) for k, v in message_queues.items()},
        "participants": sorted(participants),
    }
    with open(MESSAGES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_from_file():
    """Load state from JSON file if it exists."""
    if not os.path.exists(MESSAGES_FILE):
        return
    with open(MESSAGES_FILE) as f:
        data = json.load(f)
    for recipient, msgs in data.get("messages", {}).items():
        message_queues[recipient] = msgs
    for p in data.get("participants", []):
        participants.add(p)


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
    remove_set = set()
    for i in range(to_remove):
        remove_set.add(all_messages[i][1]["id"])

    for recipient in message_queues:
        message_queues[recipient] = [
            m for m in message_queues[recipient] if m["id"] not in remove_set
        ]
```

Also add `import json` to the top of `relay_server.py`.

Update the `send_message` endpoint to call trim and save after writing:

```python
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
        _trim_messages()
        _save_to_file()
    participants.add(msg.from_name)
    return {"id": message["id"], "timestamp": message["timestamp"]}
```

Add a startup event to load from file:

```python
@app.on_event("startup")
async def startup():
    _load_from_file()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_relay.py -v`
Expected: All 11 tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check relay_server.py tests/test_relay.py`
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: file persistence and message cap trimming"
```

---

### Task 5: Relay Server — Concurrency Test

**Files:**
- Modify: `tests/test_relay.py`

- [ ] **Step 1: Write concurrency test**

Append to `tests/test_relay.py`:

```python
async def test_concurrent_sends_no_data_loss(client: AsyncClient, auth_headers: dict):
    """Send 20 messages concurrently to same recipient, verify none lost."""

    async def send_one(i: int):
        await client.post(
            "/messages",
            json={"from_name": f"sender-{i}", "to": "target", "content": f"msg-{i}"},
            headers=auth_headers,
        )

    await asyncio.gather(*[send_one(i) for i in range(20)])

    resp = await client.get("/messages/target", headers=auth_headers)
    messages = resp.json()
    assert len(messages) == 20
    contents = {m["content"] for m in messages}
    assert contents == {f"msg-{i}" for i in range(20)}
```

Also add `import asyncio` at the top of `tests/test_relay.py`.

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_relay.py::test_concurrent_sends_no_data_loss -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_relay.py
git commit -m "test: concurrent send safety verification"
```

---

### Task 6: Relay Server — Uvicorn Entrypoint

**Files:**
- Modify: `relay_server.py`

- [ ] **Step 1: Add main block to relay server**

Append to the bottom of `relay_server.py`:

```python
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

- [ ] **Step 2: Lint**

Run: `ruff check relay_server.py`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add relay_server.py
git commit -m "feat: add uvicorn entrypoint for relay server"
```

---

### Task 7: MCP Server — Implementation

**Files:**
- Create: `mcp_server.py`
- Create: `tests/test_mcp.py`

- [ ] **Step 1: Write failing tests for MCP server**

In `tests/test_mcp.py`:

```python
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Patch module-level constants BEFORE importing mcp_server
# so the functions use the test values.
import mcp_server


@pytest.fixture(autouse=True)
def configure_mcp():
    """Patch module-level constants for all tests."""
    with (
        patch.object(mcp_server, "RELAY_URL", "http://relay:8080"),
        patch.object(mcp_server, "RELAY_SECRET", "secret"),
        patch.object(mcp_server, "INSTANCE_NAME", "alice"),
    ):
        yield


def _make_mock_client(mock_method: str, return_value):
    """Helper to create a mock httpx.AsyncClient context manager."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = return_value
    mock_response.raise_for_status = AsyncMock()

    mock_client = AsyncMock()
    setattr(mock_client, mock_method, AsyncMock(return_value=mock_response))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


async def test_send_message_calls_relay():
    """send_message should POST to relay with correct payload."""
    mock_client = _make_mock_client(
        "post", {"id": "test-id", "timestamp": "2026-04-05T00:00:00Z"}
    )

    with patch("mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server._send_message("bob", "hello")

        mock_client.post.assert_called_once_with(
            "http://relay:8080/messages",
            json={"from_name": "alice", "to": "bob", "content": "hello"},
            headers={"Authorization": "Bearer secret"},
        )
        assert "test-id" in result


async def test_check_messages_calls_relay():
    """check_messages should GET from relay for this instance."""
    mock_client = _make_mock_client(
        "get",
        [{"id": "1", "from_name": "bob", "content": "hi", "timestamp": "2026-04-05T00:00:00Z"}],
    )

    with patch("mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server._check_messages()

        mock_client.get.assert_called_once_with(
            "http://relay:8080/messages/alice",
            headers={"Authorization": "Bearer secret"},
        )
        assert "bob" in result


async def test_list_participants_calls_relay():
    """list_participants should GET /participants from relay."""
    mock_client = _make_mock_client("get", ["alice", "bob"])

    with patch("mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server._list_participants()

        mock_client.get.assert_called_once_with(
            "http://relay:8080/participants",
            headers={"Authorization": "Bearer secret"},
        )
        assert "alice" in result
        assert "bob" in result


async def test_send_message_relay_unreachable():
    """send_message should return error when relay is down."""
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectError("Connection refused")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("mcp_server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server._send_message("bob", "hello")
        assert "error" in result.lower() or "cannot" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp.py -v`
Expected: FAIL — `mcp_server` module does not exist.

- [ ] **Step 3: Implement MCP server**

In `mcp_server.py`:

```python
import os

import httpx
from mcp.server.fastmcp import FastMCP

RELAY_URL = os.environ.get("RELAY_URL", "http://localhost:8080")
RELAY_SECRET = os.environ.get("RELAY_SECRET", "test-secret")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "default")

mcp = FastMCP("claude-tunnel")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RELAY_SECRET}"}


async def _send_message(to: str, content: str) -> str:
    """Internal: send a message via the relay."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RELAY_URL}/messages",
                json={"from_name": INSTANCE_NAME, "to": to, "content": content},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return f"Message sent (id: {data['id']})"
    except httpx.ConnectError:
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        return f"Error: Relay returned {e.response.status_code}: {e.response.text}"


async def _check_messages() -> str:
    """Internal: fetch unread messages from the relay."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{RELAY_URL}/messages/{INSTANCE_NAME}",
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            messages = resp.json()
            if not messages:
                return "No new messages."
            lines = []
            for msg in messages:
                lines.append(f"[{msg['timestamp']}] {msg['from_name']}: {msg['content']}")
            return "\n".join(lines)
    except httpx.ConnectError:
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        return f"Error: Relay returned {e.response.status_code}: {e.response.text}"


async def _list_participants() -> str:
    """Internal: list all known participants."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{RELAY_URL}/participants",
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            participants = resp.json()
            if not participants:
                return "No participants yet."
            return "Participants: " + ", ".join(participants)
    except httpx.ConnectError:
        return f"Error: Cannot reach relay server at {RELAY_URL}"
    except httpx.HTTPStatusError as e:
        return f"Error: Relay returned {e.response.status_code}: {e.response.text}"


@mcp.tool()
async def send_message(to: str, content: str) -> str:
    """Send a message to another Claude Code instance.

    Args:
        to: The name of the recipient instance (e.g., "bob")
        content: The message content to send
    """
    return await _send_message(to, content)


@mcp.tool()
async def check_messages() -> str:
    """Check for new messages sent to this instance. Returns all unread messages and clears them."""
    return await _check_messages()


@mcp.tool()
async def list_participants() -> str:
    """List all known participants who have sent messages through the relay."""
    return await _list_participants()


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check mcp_server.py tests/test_mcp.py`
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py tests/test_mcp.py
git commit -m "feat: MCP server with send, check, and list tools"
```

---

### Task 8: Integration Smoke Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

This test starts the relay server in-process and has two simulated MCP clients talk to each other.

In `tests/test_integration.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from relay_server import app, _reset_state


@pytest.fixture(autouse=True)
async def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer test-secret"}


async def test_two_instances_conversation(client: AsyncClient):
    """Simulate alice and bob exchanging messages through the relay."""
    # Alice sends to bob
    resp = await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "Hey bob, how are you?"},
        headers=HEADERS,
    )
    assert resp.status_code == 200

    # Bob checks messages
    resp = await client.get("/messages/bob", headers=HEADERS)
    messages = resp.json()
    assert len(messages) == 1
    assert messages[0]["from_name"] == "alice"
    assert messages[0]["content"] == "Hey bob, how are you?"

    # Bob replies to alice
    resp = await client.post(
        "/messages",
        json={"from_name": "bob", "to": "alice", "content": "Good! Working on the feature."},
        headers=HEADERS,
    )
    assert resp.status_code == 200

    # Alice checks messages
    resp = await client.get("/messages/alice", headers=HEADERS)
    messages = resp.json()
    assert len(messages) == 1
    assert messages[0]["from_name"] == "bob"
    assert messages[0]["content"] == "Good! Working on the feature."

    # Both are participants
    resp = await client.get("/participants", headers=HEADERS)
    participants = resp.json()
    assert "alice" in participants
    assert "bob" in participants

    # No more messages for either
    resp = await client.get("/messages/alice", headers=HEADERS)
    assert resp.json() == []
    resp = await client.get("/messages/bob", headers=HEADERS)
    assert resp.json() == []
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration test for two-instance conversation"
```

---

### Task 9: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README**

In `README.md`:

```markdown
# Claude Tunnel

A relay-based MCP system that lets two Claude Code instances on different laptops communicate autonomously.

## How It Works

1. A central **relay server** (FastAPI) stores messages in per-recipient queues
2. Each Claude Code instance runs a local **MCP server** that exposes messaging tools
3. The MCP server makes HTTP calls to the relay
4. Messages are persisted to a JSON file on disk

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

Add to your Claude Code MCP config (`.claude.json` or project settings):

```json
{
  "mcpServers": {
    "claude-tunnel": {
      "command": "python",
      "args": ["/absolute/path/to/mcp_server.py"],
      "env": {
        "RELAY_URL": "https://abc123.ngrok.io",
        "RELAY_SECRET": "my-shared-secret",
        "INSTANCE_NAME": "alice"
      }
    }
  }
}
```

Replace `INSTANCE_NAME` with a unique name for each user (e.g., `"alice"` and `"bob"`).

### 5. Use in Claude Code

Your Claude instance now has these tools:

- **send_message(to, content)** — Send a message to another instance
- **check_messages()** — Fetch unread messages
- **list_participants()** — See who's connected

To poll for messages automatically:

```
/loop 10s check_messages
```

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `RELAY_SECRET` | `test-secret` | Shared auth token |
| `PORT` | `8080` | Relay server port |
| `MESSAGES_FILE` | `messages.json` | Persistence file path |
| `MAX_MESSAGES` | `1000` | Max messages before trimming |
| `RELAY_URL` | `http://localhost:8080` | Relay URL (MCP server) |
| `INSTANCE_NAME` | `default` | This instance's name (MCP server) |

## Development

```bash
pip install -r requirements-dev.txt
pytest -v
ruff check .
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup instructions"
```

---

### Task 10: Final Lint and Full Test Run

**Files:** None (verification only)

- [ ] **Step 1: Run full linting**

Run: `ruff check .`
Expected: No errors.

- [ ] **Step 2: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS.

- [ ] **Step 3: Final commit if any fixes needed**

If linting or tests required fixes, commit them:

```bash
git add -A
git commit -m "fix: lint and test fixes"
```
