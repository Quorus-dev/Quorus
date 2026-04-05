# Claude Tunnel — Design Spec

A relay-based MCP system that lets two Claude Code instances on different laptops communicate autonomously.

## Architecture

Two components:

### 1. Central Relay Server (`relay_server.py`)

A FastAPI app that accepts, stores, and serves messages between named participants.

**Responsibilities:**
- Store messages in per-recipient queues
- Persist messages to a JSON file on disk
- Cap total messages at 1000 (configurable). When the limit is hit, drop the oldest messages across all queues.
- Validate a shared secret via `Authorization: Bearer <token>` header on every request
- Track participants (anyone who has sent a message is considered registered)
- Default port: 8080 (configurable via `PORT` env var)
- Persistence file: `messages.json` in the working directory (configurable via `MESSAGES_FILE` env var)

**REST Endpoints:**

| Method | Path | Body / Params | Response | Description |
|--------|------|---------------|----------|-------------|
| `POST` | `/messages` | `{from: str, to: str, content: str}` | `{id, timestamp}` | Send a message |
| `GET` | `/messages/{recipient}` | — | `[{id, from, content, timestamp}, ...]` | Fetch and clear unread messages |
| `GET` | `/participants` | — | `[str, ...]` | List known participant names |

**Concurrency model:**
- One `asyncio.Lock` per recipient queue, acquired for both reads and writes
- File persistence happens inside the lock — only one coroutine writes at a time
- Message fetch is atomic: read + clear happens under the lock

**File persistence format:**
```json
{
  "messages": {
    "bob": [
      {"id": "uuid", "from": "alice", "content": "hey", "timestamp": "2026-04-05T12:00:00Z"}
    ],
    "alice": []
  }
}
```

**File size management:**
- After every write, check total message count across all queues
- If total exceeds the cap (default 1000), sort all messages by timestamp and remove the oldest until under the limit
- This prevents unbounded file growth

### 2. MCP Client Server (`mcp_server.py`)

A Python MCP server using stdio transport. Runs locally on each laptop. Makes HTTP calls to the central relay.

**Configuration via environment variables:**
- `RELAY_URL` — URL of the relay server (e.g., `https://abc123.ngrok.io`)
- `RELAY_SECRET` — shared secret for auth
- `INSTANCE_NAME` — this instance's name (e.g., "alice" or "bob")

**MCP Tools exposed:**

| Tool | Parameters | Returns | Description |
|------|-----------|---------|-------------|
| `send_message` | `to: str, content: str` | Confirmation with message ID | Send a message to another participant |
| `check_messages` | — | List of unread messages | Fetch and clear unread messages for this instance |
| `list_participants` | — | List of participant names | Show who has sent messages through the relay |

**Error handling:**
- Returns clear error message if relay is unreachable
- Returns auth error if secret is wrong
- Returns empty list (not error) if no messages on `check_messages`

## Data Flow

```
Claude A (laptop 1)                          Claude B (laptop 2)
    |                                            |
    v                                            v
MCP Server A                                MCP Server B
(stdio, local)                              (stdio, local)
    |                                            |
    v                                            v
    +------ HTTP -----> Relay Server <----- HTTP -+
                    (laptop 1 + ngrok)
```

1. Claude A calls `send_message(to="bob", content="hey, what's the status?")`
2. MCP Server A POSTs to the relay: `{from: "alice", to: "bob", content: "..."}`
3. Relay stores the message in bob's queue, persists to disk
4. Claude B calls `check_messages()` (via `/loop` polling every few seconds)
5. MCP Server B GETs from relay: `/messages/bob`
6. Relay returns all unread messages for bob and clears them from the queue
7. Claude B sees the message and can respond with `send_message(to="alice", ...)`

## Race Conditions & Mitigations

| Concern | Mitigation |
|---------|------------|
| Simultaneous sends to same recipient | Per-recipient `asyncio.Lock` serializes writes |
| Read-while-write | Read+clear and write share the same lock per recipient |
| File persistence race | Disk writes happen inside the lock |
| Message loss on fetch | If HTTP response fails after clearing queue, messages are lost. Acceptable for this use case. |
| File size growth | Capped at 1000 messages, oldest trimmed automatically |

## Setup Flow

### Host (runs relay + ngrok)

```bash
pip install -r requirements.txt
python relay_server.py  # starts on port 8080
ngrok http 8080         # gives public URL
```

### Both users (configure MCP in Claude Code)

Add to Claude Code MCP config (`.claude.json` or settings):

```json
{
  "mcpServers": {
    "claude-tunnel": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "RELAY_URL": "https://xxxx.ngrok.io",
        "RELAY_SECRET": "shared-secret-here",
        "INSTANCE_NAME": "alice"
      }
    }
  }
}
```

Replace `INSTANCE_NAME` with `"alice"` or `"bob"` (or any unique name).

### Polling for messages

Each Claude instance uses `/loop` to poll:
```
/loop 10s check_messages
```

## Testing & Linting

### Linting

`ruff` for linting and formatting. Config in `pyproject.toml`.

### Tests

`pytest` + `pytest-asyncio` for async test support. `httpx.AsyncClient` for testing FastAPI.

**Relay server tests (`tests/test_relay.py`):**
- Send a message, fetch it for the recipient — verify content and format
- Fetch messages for a recipient with no messages — verify empty list
- Send without auth / wrong auth — verify 401
- Send multiple messages, verify ordering (oldest first)
- Verify file persistence: send messages, reload state from file, verify messages survive
- Verify message cap: send >1000 messages, verify oldest are trimmed
- Concurrent sends to same recipient — verify no data loss or corruption
- List participants — verify senders appear

**MCP server tests (`tests/test_mcp.py`):**
- `send_message` produces correct HTTP POST to relay
- `check_messages` produces correct HTTP GET to relay
- `list_participants` produces correct HTTP GET to relay
- Error handling when relay is unreachable
- Error handling when relay returns 401

## Project Structure

```
claude_tunnel/
├── relay_server.py         # FastAPI relay (host runs this)
├── mcp_server.py           # MCP server (both users run locally)
├── requirements.txt        # fastapi, uvicorn, mcp, httpx
├── requirements-dev.txt    # ruff, pytest, pytest-asyncio
├── tests/
│   ├── test_relay.py
│   └── test_mcp.py
├── pyproject.toml          # ruff config
└── README.md               # setup instructions
```

## Dependencies

**Runtime (`requirements.txt`):**
- `fastapi`
- `uvicorn`
- `mcp`
- `httpx`

**Dev (`requirements-dev.txt`):**
- `ruff`
- `pytest`
- `pytest-asyncio`

## Out of Scope

- End-to-end encryption
- Message delivery guarantees / retry
- Multi-room / channel support
- Web UI
- Persistent connections (WebSockets/SSE)
- Per-user auth tokens (single shared secret only)
