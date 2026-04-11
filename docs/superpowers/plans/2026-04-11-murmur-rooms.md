# Murmur Rooms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add group chat rooms, SSE push delivery, and a CLI to the existing Murmur relay — hackathon-ready by April 15, 2026.

**Architecture:** Rooms are broadcast groups layered on top of the existing per-recipient inbox model. Fan-out copies messages to each member's inbox. SSE replaces polling for real-time delivery. CLI wraps the HTTP API for human participation.

**Tech Stack:** Python 3.10+, FastAPI, asyncio, httpx, rich, pytest, pytest-asyncio

---

## File Map

| File                              | Action | Responsibility                                                                     |
| --------------------------------- | ------ | ---------------------------------------------------------------------------------- |
| `relay_server.py`                 | Modify | Add room state, room CRUD endpoints, room message fan-out, SSE stream endpoint     |
| `mcp_server.py`                   | Modify | Add room MCP tools (send_room_message, join_room, list_rooms), SSE client for push |
| `cli.py`                          | Create | Human CLI: watch, say, dm, rooms, members, create, invite                          |
| `pyproject.toml`                  | Modify | Add `murmur` console entrypoint                                                    |
| `tests/test_relay.py`             | Modify | Add room + SSE tests                                                               |
| `tests/test_rooms_integration.py` | Create | End-to-end room conversation tests                                                 |
| `tests/test_cli.py`               | Create | CLI command tests                                                                  |

---

### Task 1: Room State & Persistence

**Files:**

- Modify: `relay_server.py:46-51` (in-memory state)
- Modify: `relay_server.py:97-110` (`_reset_state`)
- Modify: `relay_server.py:141-157` (`_save_to_file`)
- Modify: `relay_server.py:166-185` (`_load_from_file`)
- Test: `tests/test_relay.py`

- [ ] **Step 1: Write failing test for room persistence**

```python
# In tests/test_relay.py — add at the end

async def test_room_persistence_save_and_load(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    """Rooms should survive save/load cycle."""
    filepath = str(tmp_path / "messages.json")

    with patch("relay_server.MESSAGES_FILE", filepath):
        await client.post(
            "/rooms",
            json={"name": "test-room", "created_by": "alice"},
            headers=auth_headers,
        )
        _save_to_file()

        _reset_state()
        _load_from_file()

        resp = await client.get("/rooms", headers=auth_headers)
        rooms = resp.json()
        assert len(rooms) == 1
        assert rooms[0]["name"] == "test-room"
        assert "alice" in rooms[0]["members"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_relay.py::test_room_persistence_save_and_load -v`
Expected: FAIL — `/rooms` endpoint does not exist (404)

- [ ] **Step 3: Add room state to relay_server.py**

Add after `webhooks` declaration (line 52):

```python
# Room state
rooms: dict[str, dict] = {}  # room_id -> {name, created_by, members, created_at}
```

Update `_reset_state()` — add inside the function body:

```python
rooms.clear()
```

Update `_save_to_file()` — add `"rooms"` to the `data` dict:

```python
"rooms": {
    rid: {
        "name": r["name"],
        "created_by": r["created_by"],
        "members": sorted(r["members"]),
        "created_at": r["created_at"],
    }
    for rid, r in rooms.items()
},
```

Update `_load_from_file()` — add after loading participants:

```python
for rid, rdata in data.get("rooms", {}).items():
    rooms[rid] = {
        "name": rdata["name"],
        "created_by": rdata["created_by"],
        "members": set(rdata.get("members", [])),
        "created_at": rdata["created_at"],
    }
```

- [ ] **Step 4: Continue to Task 2 (the endpoint is needed for the test to pass)**

---

### Task 2: Room CRUD Endpoints

**Files:**

- Modify: `relay_server.py` (add new Pydantic models and endpoints)
- Test: `tests/test_relay.py`

- [ ] **Step 1: Write failing tests for room CRUD**

```python
# In tests/test_relay.py — add at the end

async def test_create_room(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/rooms",
        json={"name": "dev-room", "created_by": "alice"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "dev-room"
    assert "alice" in data["members"]
    assert "id" in data
    assert "created_at" in data


async def test_create_room_duplicate_name_rejected(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/rooms",
        json={"name": "dev-room", "created_by": "alice"},
        headers=auth_headers,
    )
    resp = await client.post(
        "/rooms",
        json={"name": "dev-room", "created_by": "bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


async def test_list_rooms(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/rooms",
        json={"name": "room-a", "created_by": "alice"},
        headers=auth_headers,
    )
    await client.post(
        "/rooms",
        json={"name": "room-b", "created_by": "bob"},
        headers=auth_headers,
    )
    resp = await client.get("/rooms", headers=auth_headers)
    assert resp.status_code == 200
    rooms_list = resp.json()
    assert len(rooms_list) == 2
    names = {r["name"] for r in rooms_list}
    assert names == {"room-a", "room-b"}


async def test_get_room_by_id(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post(
        "/rooms",
        json={"name": "my-room", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "my-room"


async def test_get_nonexistent_room_returns_404(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/rooms/nonexistent-id", headers=auth_headers)
    assert resp.status_code == 404


async def test_join_room(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post(
        "/rooms",
        json={"name": "my-room", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    resp = await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "joined"

    resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)
    assert "bob" in resp.json()["members"]


async def test_leave_room(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post(
        "/rooms",
        json={"name": "my-room", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=auth_headers,
    )
    resp = await client.post(
        f"/rooms/{room_id}/leave",
        json={"participant": "bob"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "left"

    resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)
    assert "bob" not in resp.json()["members"]


async def test_room_endpoints_require_auth(client: AsyncClient):
    resp = await client.post("/rooms", json={"name": "x", "created_by": "y"})
    assert resp.status_code == 401
    resp = await client.get("/rooms")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py -k "room" -v`
Expected: FAIL — endpoints don't exist

- [ ] **Step 3: Add Pydantic models and room endpoints to relay_server.py**

Add Pydantic models after `RegisterWebhookRequest`:

```python
class CreateRoomRequest(BaseModel):
    name: str
    created_by: str

    @field_validator("name", "created_by")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class JoinLeaveRequest(BaseModel):
    participant: str

    @field_validator("participant")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)
```

Add room endpoints after the `/webhooks` endpoints:

```python
def _find_room_by_name(name: str) -> str | None:
    """Return room_id for a given room name, or None."""
    for rid, room in rooms.items():
        if room["name"] == name:
            return rid
    return None


@app.post("/rooms", dependencies=[Depends(verify_auth)])
async def create_room(req: CreateRoomRequest):
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
    return [
        {
            "id": rid,
            "name": r["name"],
            "members": sorted(r["members"]),
            "created_at": r["created_at"],
        }
        for rid, r in rooms.items()
    ]


@app.get("/rooms/{room_id}", dependencies=[Depends(verify_auth)])
async def get_room(room_id: str):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return {
        "id": room_id,
        "name": room["name"],
        "members": sorted(room["members"]),
        "created_at": room["created_at"],
    }


@app.post("/rooms/{room_id}/join", dependencies=[Depends(verify_auth)])
async def join_room(room_id: str, req: JoinLeaveRequest):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    room["members"].add(req.participant)
    participants.add(req.participant)
    await _persist_state()
    logger.info("%s joined room %s", req.participant, room["name"])
    return {"status": "joined"}


@app.post("/rooms/{room_id}/leave", dependencies=[Depends(verify_auth)])
async def leave_room(room_id: str, req: JoinLeaveRequest):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    room["members"].discard(req.participant)
    await _persist_state()
    logger.info("%s left room %s", req.participant, room["name"])
    return {"status": "left"}
```

- [ ] **Step 4: Run all room tests + the persistence test from Task 1**

Run: `pytest tests/test_relay.py -k "room" -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to verify nothing broke**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: add room CRUD endpoints with persistence"
git push
```

---

### Task 3: Room Message Fan-Out

**Files:**

- Modify: `relay_server.py` (add room message endpoint)
- Test: `tests/test_relay.py`

- [ ] **Step 1: Write failing tests for room messaging**

```python
# In tests/test_relay.py

async def test_send_room_message_fans_out(client: AsyncClient, auth_headers: dict):
    """Message sent to room should appear in all members' inboxes except sender."""
    create_resp = await client.post(
        "/rooms",
        json={"name": "team", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=auth_headers,
    )
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "charlie"},
        headers=auth_headers,
    )

    resp = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "hello team"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Bob gets the message
    resp = await client.get("/messages/bob", headers=auth_headers)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hello team"
    assert msgs[0]["from_name"] == "alice"
    assert msgs[0]["room"] == "team"

    # Charlie gets the message
    resp = await client.get("/messages/charlie", headers=auth_headers)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["room"] == "team"

    # Alice does NOT get her own message
    resp = await client.get("/messages/alice", headers=auth_headers)
    assert resp.json() == []


async def test_room_message_with_type(client: AsyncClient, auth_headers: dict):
    """Room messages should support message_type field."""
    create_resp = await client.post(
        "/rooms",
        json={"name": "team", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=auth_headers,
    )

    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "CLAIMING: auth module", "message_type": "claim"},
        headers=auth_headers,
    )

    resp = await client.get("/messages/bob", headers=auth_headers)
    msgs = resp.json()
    assert msgs[0]["message_type"] == "claim"


async def test_room_message_nonmember_rejected(client: AsyncClient, auth_headers: dict):
    """Non-members cannot send messages to a room."""
    create_resp = await client.post(
        "/rooms",
        json={"name": "private", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]

    resp = await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "eve", "content": "let me in"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


async def test_room_message_nonexistent_room_returns_404(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/rooms/fake-room/messages",
        json={"from_name": "alice", "content": "hello"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_dm_still_works_after_rooms_added(client: AsyncClient, auth_headers: dict):
    """Existing DM functionality should be unaffected by room additions."""
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "direct msg"},
        headers=auth_headers,
    )
    resp = await client.get("/messages/bob", headers=auth_headers)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == "direct msg"
    assert msgs[0].get("room") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py -k "room_message" -v`
Expected: FAIL — room message endpoint doesn't exist

- [ ] **Step 3: Add RoomMessageRequest model and endpoint**

Add Pydantic model:

```python
class RoomMessageRequest(BaseModel):
    from_name: str
    content: str
    message_type: str = "chat"

    @field_validator("from_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)
```

Add endpoint after room CRUD endpoints:

```python
@app.post("/rooms/{room_id}/messages", dependencies=[Depends(verify_auth)])
async def send_room_message(room_id: str, msg: RoomMessageRequest):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if msg.from_name not in room["members"]:
        raise HTTPException(status_code=403, detail="Not a member of this room")

    timestamp = datetime.now(timezone.utc).isoformat()
    message_id = str(uuid.uuid4())
    recipients = room["members"] - {msg.from_name}

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
        # Push to SSE subscribers (Task 4 adds sse_queues)
        for q in sse_queues.get(recipient, []):
            await q.put(fan_out_msg)
        await _notify_webhook(recipient, fan_out_msg)

    participants.add(msg.from_name)
    _track_send(msg.from_name)
    _trim_messages()
    await _persist_state()

    logger.info(
        "Room message %s in %s: %s -> %d recipients",
        message_id, room["name"], msg.from_name, len(recipients),
    )
    return {"id": message_id, "timestamp": timestamp}
```

Also update the existing `send_message` endpoint to include `"room": None` in the message dict so DMs and room messages have a consistent schema. Add `"room": None` to both the chunked and non-chunked message dicts in the existing `send_message` function.

Add `sse_queues` to state (will be used in Task 4 — initialize empty for now):

```python
sse_queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
```

Add to `_reset_state()`:

```python
sse_queues.clear()
```

- [ ] **Step 4: Run all tests**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: add room message fan-out endpoint"
git push
```

---

### Task 4: SSE Stream Endpoint

**Files:**

- Modify: `relay_server.py` (add `/stream/{recipient}` SSE endpoint)
- Test: `tests/test_relay.py`

- [ ] **Step 1: Write failing tests for SSE**

```python
# In tests/test_relay.py

import json as json_module


async def test_sse_stream_receives_message(client: AsyncClient, auth_headers: dict):
    """SSE stream should receive messages pushed after connection."""
    from relay_server import sse_queues

    # Simulate SSE subscription by adding a queue directly
    q: asyncio.Queue = asyncio.Queue()
    sse_queues["bob"].append(q)

    # Send a message to bob
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "sse test"},
        headers=auth_headers,
    )

    # The message should be in the SSE queue
    msg = await asyncio.wait_for(q.get(), timeout=2)
    assert msg["content"] == "sse test"
    assert msg["from_name"] == "alice"

    # Cleanup
    sse_queues["bob"].remove(q)


async def test_sse_endpoint_returns_event_stream(client: AsyncClient, auth_headers: dict):
    """SSE endpoint should return text/event-stream content type."""
    # Send a message first so the stream has something to return
    await client.post(
        "/messages",
        json={"from_name": "alice", "to": "bob", "content": "for sse"},
        headers=auth_headers,
    )

    async with client.stream(
        "GET",
        "/stream/bob",
        params={"token": "test-secret"},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        # Read first two events: connected + message
        lines = []
        async for line in resp.aiter_lines():
            lines.append(line)
            if len(lines) >= 6:  # connected event (3 lines) + message event (3 lines)
                break

        # First event should be "connected"
        assert any("connected" in line for line in lines)


async def test_sse_pushes_room_messages(client: AsyncClient, auth_headers: dict):
    """Room fan-out should push to SSE subscribers."""
    from relay_server import sse_queues

    q: asyncio.Queue = asyncio.Queue()
    sse_queues["bob"].append(q)

    create_resp = await client.post(
        "/rooms",
        json={"name": "sse-room", "created_by": "alice"},
        headers=auth_headers,
    )
    room_id = create_resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=auth_headers,
    )

    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "room sse test"},
        headers=auth_headers,
    )

    msg = await asyncio.wait_for(q.get(), timeout=2)
    assert msg["content"] == "room sse test"
    assert msg["room"] == "sse-room"

    sse_queues["bob"].remove(q)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_relay.py -k "sse" -v`
Expected: FAIL

- [ ] **Step 3: Add SSE endpoint and wire up SSE push in send_message**

Add import at top of relay_server.py:

```python
from fastapi.responses import StreamingResponse
```

Add SSE endpoint:

```python
@app.get("/stream/{recipient}")
async def stream_messages(recipient: str, token: str = ""):
    """SSE endpoint for real-time message delivery."""
    if token != RELAY_SECRET:
        raise HTTPException(status_code=401, detail="Invalid token")

    q: asyncio.Queue = asyncio.Queue()
    sse_queues[recipient].append(q)
    logger.info("SSE client connected for %s", recipient)

    async def event_generator():
        try:
            # Send connected event
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
                    # Send keepalive comment to prevent connection timeout
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
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
```

Update existing `send_message` endpoint — add SSE push after `message_events[msg.to].set()` in both the chunked and non-chunked branches:

```python
# Push to SSE subscribers
for q in sse_queues.get(msg.to, []):
    await q.put(message)  # or the reassembled message for chunks
```

- [ ] **Step 4: Run all tests**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add relay_server.py tests/test_relay.py
git commit -m "feat: add SSE stream endpoint for real-time push"
git push
```

---

### Task 5: MCP Server Room Tools

**Files:**

- Modify: `mcp_server.py` (add room tools + SSE client)
- Test: `tests/test_mcp.py`

- [ ] **Step 1: Write failing tests for MCP room tools**

```python
# In tests/test_mcp.py — add at end

async def test_send_room_message_posts_to_relay(configure_mcp):
    """send_room_message should POST to /rooms/{room_id}/messages."""
    from mcp_server import _send_room_message

    mock_client = _make_mock_client(
        200,
        {"id": "msg-1", "timestamp": "2026-04-11T00:00:00Z"},
    )
    with patch("mcp_server._get_http_client", return_value=mock_client):
        result = await _send_room_message("room-123", "hello room")

    assert "msg-1" in result
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "/rooms/room-123/messages" in call_args[0][0]
    assert call_args[1]["json"]["from_name"] == "test-instance"
    assert call_args[1]["json"]["content"] == "hello room"


async def test_join_room_posts_to_relay(configure_mcp):
    """join_room should POST to /rooms/{room_id}/join."""
    from mcp_server import _join_room

    mock_client = _make_mock_client(200, {"status": "joined"})
    with patch("mcp_server._get_http_client", return_value=mock_client):
        result = await _join_room("room-123")

    assert "joined" in result.lower()
    mock_client.post.assert_called_once()


async def test_list_rooms_gets_from_relay(configure_mcp):
    """list_rooms should GET /rooms."""
    from mcp_server import _list_rooms

    mock_rooms = [
        {"id": "r1", "name": "yc-hack", "members": ["alice", "bob"], "created_at": "2026-04-11T00:00:00Z"},
    ]
    mock_client = _make_mock_client(200, mock_rooms)
    with patch("mcp_server._get_http_client", return_value=mock_client):
        result = await _list_rooms()

    assert "yc-hack" in result
    mock_client.get.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp.py -k "room" -v`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Add room internal functions and MCP tools to mcp_server.py**

Add internal functions after `_list_participants`:

```python
async def _send_room_message(
    room_id: str, content: str, message_type: str = "chat", context: Context | None = None
) -> str:
    """Internal: send a message to a room via the relay."""
    await _remember_session(context)
    try:
        client = _get_http_client()
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room_id}/messages",
            json={
                "from_name": INSTANCE_NAME,
                "content": content,
                "message_type": message_type,
            },
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("Sent room message to %s (id: %s)", room_id, data["id"])
        return f"Room message sent (id: {data['id']})"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)


async def _join_room(room_id: str, context: Context | None = None) -> str:
    """Internal: join a room."""
    await _remember_session(context)
    try:
        client = _get_http_client()
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room_id}/join",
            json={"participant": INSTANCE_NAME},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        logger.info("Joined room %s", room_id)
        return f"Joined room {room_id}"
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)


async def _list_rooms(context: Context | None = None) -> str:
    """Internal: list all rooms."""
    await _remember_session(context)
    try:
        client = _get_http_client()
        resp = await client.get(
            f"{RELAY_URL}/rooms",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        rooms_list = resp.json()
        logger.info("Listed %d rooms", len(rooms_list))
        if not rooms_list:
            return "No rooms yet."
        lines = []
        for r in rooms_list:
            members = ", ".join(r["members"])
            lines.append(f"  {r['name']} (id: {r['id']}) — members: {members}")
        return "Rooms:\n" + "\n".join(lines)
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return _relay_error_message(e)
```

Add MCP tool wrappers after the existing tool definitions:

```python
@mcp.tool()
async def send_room_message(
    room_id: str, content: str, message_type: str = "chat", context: Context = None
) -> str:
    """Send a message to a room. All room members will receive it.

    Args:
        room_id: The room ID to send to
        content: The message content
        message_type: Optional type: chat, claim, status, request, alert, sync
    """
    return await _send_room_message(room_id, content, message_type, context)


@mcp.tool()
async def join_room(room_id: str, context: Context = None) -> str:
    """Join a room to start receiving messages from it.

    Args:
        room_id: The room ID to join
    """
    return await _join_room(room_id, context)


@mcp.tool()
async def list_rooms(context: Context = None) -> str:
    """List all available rooms with their members."""
    return await _list_rooms(context)
```

- [ ] **Step 4: Run all tests**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add mcp_server.py tests/test_mcp.py
git commit -m "feat: add room MCP tools (send, join, list)"
git push
```

---

### Task 6: MCP SSE Client for Push Notifications

**Files:**

- Modify: `mcp_server.py` (replace background polling with SSE client)

- [ ] **Step 1: Write failing test for SSE-based background listener**

```python
# In tests/test_mcp.py

async def test_sse_listener_processes_message_event(configure_mcp):
    """SSE listener should parse message events and append to pending buffer."""
    from mcp_server import _append_pending_messages, _drain_pending_messages, _process_sse_event

    await _process_sse_event(
        "message",
        '{"id": "m1", "from_name": "alice", "content": "hi", "timestamp": "2026-04-11T00:00:00Z"}',
    )

    messages = await _drain_pending_messages()
    assert len(messages) == 1
    assert messages[0]["from_name"] == "alice"


async def test_sse_listener_ignores_connected_event(configure_mcp):
    """Connected events should not be treated as messages."""
    from mcp_server import _drain_pending_messages, _process_sse_event

    await _process_sse_event(
        "connected",
        '{"participant": "bob", "timestamp": "2026-04-11T00:00:00Z"}',
    )

    messages = await _drain_pending_messages()
    assert len(messages) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp.py -k "sse" -v`
Expected: FAIL — `_process_sse_event` doesn't exist

- [ ] **Step 3: Add SSE event processor and SSE background listener**

Add to mcp_server.py:

```python
import json as json_module


async def _process_sse_event(event_type: str, data: str) -> None:
    """Handle a parsed SSE event from the relay stream."""
    if event_type != "message":
        return
    try:
        msg = json_module.loads(data)
        await _append_pending_messages([msg])
        await _notify_active_session([msg])
    except (json_module.JSONDecodeError, KeyError):
        logger.warning("Failed to parse SSE event data: %s", data[:200])


async def _sse_listener(stop_event: asyncio.Event) -> None:
    """Connect to relay SSE stream and process events. Reconnects on failure."""
    while not stop_event.is_set():
        try:
            client = _get_http_client()
            url = f"{RELAY_URL}/stream/{INSTANCE_NAME}"
            params = {"token": RELAY_SECRET}
            async with client.stream("GET", url, params=params, timeout=None) as resp:
                if resp.status_code != 200:
                    logger.warning("SSE stream returned %d, retrying", resp.status_code)
                    await asyncio.sleep(2)
                    continue

                logger.info("SSE stream connected for %s", INSTANCE_NAME)
                event_type = ""
                event_data = ""

                async for line in resp.aiter_lines():
                    if stop_event.is_set():
                        break
                    line = line.strip()
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        event_data = line[5:].strip()
                    elif line == "" and event_type:
                        await _process_sse_event(event_type, event_data)
                        event_type = ""
                        event_data = ""
                    # Ignore comment lines (keepalives starting with ":")

        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            logger.warning("SSE connection lost, reconnecting in 2s")
        except Exception:
            logger.warning("SSE listener error, reconnecting in 2s", exc_info=True)

        if not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
```

Update `_mcp_lifespan` to use SSE listener instead of polling when available:

```python
@asynccontextmanager
async def _mcp_lifespan(server: FastMCP):
    global _http_client
    _http_client = httpx.AsyncClient()

    stop_event = asyncio.Event()
    background_task = None

    if ENABLE_BACKGROUND_POLLING:
        # Prefer SSE if relay supports it, fall back to polling
        background_task = asyncio.create_task(_sse_listener(stop_event))
        logger.info("SSE background listener enabled")

    try:
        yield {"stop_event": stop_event}
    finally:
        stop_event.set()
        if background_task is not None:
            background_task.cancel()
            with suppress(asyncio.CancelledError):
                await background_task
        if _http_client is not None:
            await _http_client.aclose()
        _reset_runtime_state()
```

- [ ] **Step 4: Run all tests**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add mcp_server.py tests/test_mcp.py
git commit -m "feat: add SSE client for real-time push notifications"
git push
```

---

### Task 7: CLI Tool

**Files:**

- Create: `cli.py`
- Modify: `pyproject.toml` (add console entrypoint)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for CLI functions**

Create `tests/test_cli.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest


async def test_cli_list_rooms():
    """CLI rooms command should fetch and display rooms."""
    from cli import _list_rooms

    mock_rooms = [
        {"id": "r1", "name": "yc-hack", "members": ["alice", "bob"], "created_at": "2026-04-11T00:00:00Z"},
    ]
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_rooms
    mock_resp.raise_for_status = lambda: None

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("cli._get_client", return_value=mock_client):
        result = await _list_rooms()

    assert len(result) == 1
    assert result[0]["name"] == "yc-hack"


async def test_cli_create_room():
    """CLI create command should POST to /rooms."""
    from cli import _create_room

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "r1", "name": "new-room", "members": ["alice"], "created_at": "2026-04-11T00:00:00Z"}
    mock_resp.raise_for_status = lambda: None

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("cli._get_client", return_value=mock_client):
        result = await _create_room("new-room")

    assert result["name"] == "new-room"
    mock_client.post.assert_called_once()


async def test_cli_say():
    """CLI say command should find room ID and POST message."""
    from cli import _say

    mock_rooms_resp = AsyncMock()
    mock_rooms_resp.json.return_value = [{"id": "r1", "name": "yc-hack", "members": ["alice"]}]
    mock_rooms_resp.raise_for_status = lambda: None

    mock_msg_resp = AsyncMock()
    mock_msg_resp.json.return_value = {"id": "m1", "timestamp": "2026-04-11T00:00:00Z"}
    mock_msg_resp.raise_for_status = lambda: None

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_rooms_resp)
    mock_client.post = AsyncMock(return_value=mock_msg_resp)

    with patch("cli._get_client", return_value=mock_client):
        result = await _say("yc-hack", "hello team")

    assert result["id"] == "m1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `cli` module doesn't exist

- [ ] **Step 3: Create cli.py**

```python
"""Murmur CLI — human interface for rooms and messaging."""

import argparse
import asyncio
import json
import sys

import httpx
from rich.console import Console
from rich.table import Table

from tunnel_config import load_config

console = Console()
_config = load_config()
RELAY_URL = _config["relay_url"]
RELAY_SECRET = _config["relay_secret"]
INSTANCE_NAME = _config["instance_name"]


def _get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RELAY_SECRET}"}


async def _list_rooms() -> list[dict]:
    client = _get_client()
    resp = await client.get(f"{RELAY_URL}/rooms", headers=_auth_headers())
    resp.raise_for_status()
    return resp.json()


async def _create_room(name: str) -> dict:
    client = _get_client()
    resp = await client.post(
        f"{RELAY_URL}/rooms",
        json={"name": name, "created_by": INSTANCE_NAME},
        headers=_auth_headers(),
    )
    resp.raise_for_status()
    return resp.json()


async def _invite(room_name: str, participants: list[str]) -> None:
    client = _get_client()
    rooms = await _list_rooms()
    room = next((r for r in rooms if r["name"] == room_name), None)
    if not room:
        console.print(f"[red]Room '{room_name}' not found[/red]")
        return
    for p in participants:
        resp = await client.post(
            f"{RELAY_URL}/rooms/{room['id']}/join",
            json={"participant": p},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        console.print(f"  [green]+ {p}[/green] joined {room_name}")


async def _members(room_name: str) -> list[str]:
    rooms = await _list_rooms()
    room = next((r for r in rooms if r["name"] == room_name), None)
    if not room:
        return []
    return room["members"]


async def _say(room_name: str, message: str) -> dict:
    client = _get_client()
    rooms_resp = await client.get(f"{RELAY_URL}/rooms", headers=_auth_headers())
    rooms_resp.raise_for_status()
    rooms = rooms_resp.json()
    room = next((r for r in rooms if r["name"] == room_name), None)
    if not room:
        raise ValueError(f"Room '{room_name}' not found")
    resp = await client.post(
        f"{RELAY_URL}/rooms/{room['id']}/messages",
        json={"from_name": INSTANCE_NAME, "content": message},
        headers=_auth_headers(),
    )
    resp.raise_for_status()
    return resp.json()


async def _dm(to: str, message: str) -> dict:
    client = _get_client()
    resp = await client.post(
        f"{RELAY_URL}/messages",
        json={"from_name": INSTANCE_NAME, "to": to, "content": message},
        headers=_auth_headers(),
    )
    resp.raise_for_status()
    return resp.json()


async def _watch(room_name: str) -> None:
    """Stream messages from a room via SSE."""
    rooms = await _list_rooms()
    room = next((r for r in rooms if r["name"] == room_name), None)
    if not room:
        console.print(f"[red]Room '{room_name}' not found[/red]")
        return

    members = room["members"]
    console.print(f"[bold]Watching room: {room_name}[/bold]")
    console.print(f"Members: {', '.join(members)}")
    console.print("---")

    # Stream via SSE for this instance
    client = httpx.AsyncClient()
    try:
        url = f"{RELAY_URL}/stream/{INSTANCE_NAME}"
        async with client.stream("GET", url, params={"token": RELAY_SECRET}, timeout=None) as resp:
            event_type = ""
            event_data = ""
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    event_data = line[5:].strip()
                elif line == "" and event_type:
                    if event_type == "message":
                        try:
                            msg = json.loads(event_data)
                            room_label = msg.get("room", "DM")
                            msg_type = msg.get("message_type", "chat")
                            sender = msg.get("from_name", "?")
                            content = msg.get("content", "")
                            ts = msg.get("timestamp", "")[:19]

                            if msg.get("room") == room_name:
                                type_color = {
                                    "claim": "yellow",
                                    "status": "cyan",
                                    "request": "magenta",
                                    "alert": "red",
                                    "sync": "green",
                                }.get(msg_type, "white")
                                console.print(
                                    f"[dim]{ts}[/dim] "
                                    f"[bold]{sender}[/bold] "
                                    f"[{type_color}][{msg_type}][/{type_color}] "
                                    f"{content}"
                                )
                        except json.JSONDecodeError:
                            pass
                    event_type = ""
                    event_data = ""
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching.[/dim]")
    finally:
        await client.aclose()


def _cmd_rooms(args):
    rooms = asyncio.run(_list_rooms())
    if not rooms:
        console.print("[dim]No rooms yet.[/dim]")
        return
    table = Table(title="Rooms")
    table.add_column("Name", style="bold")
    table.add_column("Members")
    table.add_column("ID", style="dim")
    for r in rooms:
        table.add_row(r["name"], ", ".join(r["members"]), r["id"])
    console.print(table)


def _cmd_create(args):
    room = asyncio.run(_create_room(args.name))
    console.print(f"[green]Created room '{room['name']}' (id: {room['id']})[/green]")


def _cmd_invite(args):
    asyncio.run(_invite(args.room, args.participants))


def _cmd_members(args):
    members = asyncio.run(_members(args.room))
    if not members:
        console.print(f"[dim]Room '{args.room}' not found or empty.[/dim]")
        return
    for m in members:
        console.print(f"  {m}")


def _cmd_say(args):
    asyncio.run(_say(args.room, args.message))
    console.print(f"[green]Sent to {args.room}[/green]")


def _cmd_dm(args):
    asyncio.run(_dm(args.to, args.message))
    console.print(f"[green]DM sent to {args.to}[/green]")


def _cmd_watch(args):
    asyncio.run(_watch(args.room))


def main():
    parser = argparse.ArgumentParser(prog="murmur", description="Murmur CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("rooms", help="List all rooms")

    p_create = sub.add_parser("create", help="Create a room")
    p_create.add_argument("name", help="Room name")

    p_invite = sub.add_parser("invite", help="Invite participants to a room")
    p_invite.add_argument("room", help="Room name")
    p_invite.add_argument("participants", nargs="+", help="Participant names")

    p_members = sub.add_parser("members", help="List room members")
    p_members.add_argument("room", help="Room name")

    p_say = sub.add_parser("say", help="Send message to a room")
    p_say.add_argument("room", help="Room name")
    p_say.add_argument("message", help="Message content")

    p_dm = sub.add_parser("dm", help="Send direct message")
    p_dm.add_argument("to", help="Recipient name")
    p_dm.add_argument("message", help="Message content")

    p_watch = sub.add_parser("watch", help="Watch a room live")
    p_watch.add_argument("room", help="Room name")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "rooms": _cmd_rooms,
        "create": _cmd_create,
        "invite": _cmd_invite,
        "members": _cmd_members,
        "say": _cmd_say,
        "dm": _cmd_dm,
        "watch": _cmd_watch,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add console entrypoint to pyproject.toml**

Add to `[project.scripts]`:

```toml
murmur = "cli:main"
```

- [ ] **Step 5: Run CLI tests**

Run: `pytest tests/test_cli.py -v`
Expected: ALL PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 7: Commit and push**

```bash
git add cli.py tests/test_cli.py pyproject.toml
git commit -m "feat: add murmur CLI for room management and live watch"
git push
```

---

### Task 8: Room Integration Test

**Files:**

- Create: `tests/test_rooms_integration.py`

- [ ] **Step 1: Write end-to-end room conversation test**

```python
"""End-to-end test: multi-agent room conversation with fan-out."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from relay_server import _reset_state, app, sse_queues

HEADERS = {"Authorization": "Bearer test-secret"}


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


async def test_multi_agent_room_conversation(client: AsyncClient):
    """Simulate a hackathon room with 3 agents coordinating."""
    # Create room
    resp = await client.post(
        "/rooms",
        json={"name": "yc-hack", "created_by": "arav-agent-1"},
        headers=HEADERS,
    )
    room_id = resp.json()["id"]

    # Join agents
    for agent in ["arav-agent-2", "aarya-agent-1", "aarya-agent-2"]:
        await client.post(
            f"/rooms/{room_id}/join",
            json={"participant": agent},
            headers=HEADERS,
        )

    # arav-agent-1 claims auth module
    await client.post(
        f"/rooms/{room_id}/messages",
        json={
            "from_name": "arav-agent-1",
            "content": "CLAIMING: auth module",
            "message_type": "claim",
        },
        headers=HEADERS,
    )

    # All other agents should see the claim
    for agent in ["arav-agent-2", "aarya-agent-1", "aarya-agent-2"]:
        resp = await client.get(f"/messages/{agent}", headers=HEADERS)
        msgs = resp.json()
        assert len(msgs) == 1
        assert msgs[0]["message_type"] == "claim"
        assert msgs[0]["room"] == "yc-hack"
        assert msgs[0]["from_name"] == "arav-agent-1"

    # arav-agent-1 should NOT have the message in their inbox
    resp = await client.get("/messages/arav-agent-1", headers=HEADERS)
    assert resp.json() == []

    # aarya-agent-1 sends status update
    await client.post(
        f"/rooms/{room_id}/messages",
        json={
            "from_name": "aarya-agent-1",
            "content": "STATUS: database schema ready, pushed to feature/db",
            "message_type": "status",
        },
        headers=HEADERS,
    )

    # arav-agent-1 should see aarya's status
    resp = await client.get("/messages/arav-agent-1", headers=HEADERS)
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["from_name"] == "aarya-agent-1"
    assert msgs[0]["message_type"] == "status"

    # DMs still work alongside rooms
    await client.post(
        "/messages",
        json={"from_name": "arav-agent-1", "to": "aarya-agent-1", "content": "private question"},
        headers=HEADERS,
    )
    resp = await client.get("/messages/aarya-agent-1", headers=HEADERS)
    msgs = resp.json()
    # Should have: status from aarya-agent-2 + arav-agent-2 (room) + DM
    dm_msgs = [m for m in msgs if m.get("room") is None]
    assert len(dm_msgs) == 1
    assert dm_msgs[0]["content"] == "private question"

    # Analytics should reflect room activity
    resp = await client.get("/analytics", headers=HEADERS)
    data = resp.json()
    assert data["total_messages_sent"] >= 3  # 2 room + 1 DM

    # All agents are participants
    resp = await client.get("/participants", headers=HEADERS)
    participants = resp.json()
    assert "arav-agent-1" in participants
    assert "aarya-agent-1" in participants


async def test_sse_receives_room_fanout(client: AsyncClient):
    """SSE subscribers should receive room fan-out messages."""
    # Setup SSE queue for bob
    q: asyncio.Queue = asyncio.Queue()
    sse_queues["bob"].append(q)

    # Create room and add bob
    resp = await client.post(
        "/rooms",
        json={"name": "sse-test", "created_by": "alice"},
        headers=HEADERS,
    )
    room_id = resp.json()["id"]
    await client.post(
        f"/rooms/{room_id}/join",
        json={"participant": "bob"},
        headers=HEADERS,
    )

    # Alice sends room message
    await client.post(
        f"/rooms/{room_id}/messages",
        json={"from_name": "alice", "content": "realtime test"},
        headers=HEADERS,
    )

    # Bob's SSE queue should have the message
    msg = await asyncio.wait_for(q.get(), timeout=2)
    assert msg["content"] == "realtime test"
    assert msg["room"] == "sse-test"

    sse_queues["bob"].remove(q)


async def test_two_rooms_isolation(client: AsyncClient):
    """Messages in one room should not leak to another room's members."""
    # Create two rooms
    resp1 = await client.post(
        "/rooms",
        json={"name": "room-a", "created_by": "alice"},
        headers=HEADERS,
    )
    room_a = resp1.json()["id"]
    await client.post(
        f"/rooms/{room_a}/join",
        json={"participant": "bob"},
        headers=HEADERS,
    )

    resp2 = await client.post(
        "/rooms",
        json={"name": "room-b", "created_by": "charlie"},
        headers=HEADERS,
    )
    room_b = resp2.json()["id"]
    await client.post(
        f"/rooms/{room_b}/join",
        json={"participant": "dave"},
        headers=HEADERS,
    )

    # Send to room-a
    await client.post(
        f"/rooms/{room_a}/messages",
        json={"from_name": "alice", "content": "room-a only"},
        headers=HEADERS,
    )

    # Bob (room-a) should see it
    resp = await client.get("/messages/bob", headers=HEADERS)
    assert len(resp.json()) == 1

    # Dave (room-b) should NOT see it
    resp = await client.get("/messages/dave", headers=HEADERS)
    assert resp.json() == []

    # Charlie (room-b) should NOT see it
    resp = await client.get("/messages/charlie", headers=HEADERS)
    assert resp.json() == []
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_rooms_integration.py -v`
Expected: ALL PASS (depends on Tasks 1-4 being complete)

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit and push**

```bash
git add tests/test_rooms_integration.py
git commit -m "test: add room integration tests with fan-out and isolation"
git push
```

---

### Task 9: Update CONTEXT.md and Final Verification

**Files:**

- Modify: `CONTEXT.md`

- [ ] **Step 1: Update CONTEXT.md with new state**

Update the "Current State" section to reflect rooms, SSE, and CLI are built. Update "Recent Changes" with the new commits. Move relevant "Next Up" items to done.

- [ ] **Step 2: Run full test suite one final time**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 3: Run linter**

Run: `ruff check .`
Expected: No errors (fix any that appear)

- [ ] **Step 4: Commit and push**

```bash
git add CONTEXT.md
git commit -m "docs: update CONTEXT.md with rooms, SSE, CLI"
git push
```

- [ ] **Step 5: Manual smoke test**

Start the relay:

```bash
RELAY_SECRET=hackathon-secret python relay_server.py
```

In another terminal:

```bash
export RELAY_URL=http://localhost:8080
export RELAY_SECRET=hackathon-secret
export INSTANCE_NAME=arav

python cli.py create yc-hack
python cli.py invite yc-hack agent-1 agent-2
python cli.py say yc-hack "hello from the CLI"
python cli.py watch yc-hack
```

In a third terminal, send a message via curl:

```bash
curl -X POST http://localhost:8080/rooms/<room_id>/messages \
  -H "Authorization: Bearer hackathon-secret" \
  -H "Content-Type: application/json" \
  -d '{"from_name": "agent-1", "content": "hello from agent", "message_type": "status"}'
```

Verify the watch terminal shows the message in real-time.
