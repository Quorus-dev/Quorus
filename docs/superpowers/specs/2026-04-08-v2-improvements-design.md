# Claude Tunnel v2 Improvements — Design Spec

Adds structured logging, message chunking, per-participant analytics with terminal charts, push notifications via MCP Channels, and long-polling fallback.

## 1. Structured Logging

Add Python `logging` to both `relay_server.py` and `mcp_server.py`.

### Relay Server

Log at these points:
- **INFO**: Message sent (sender, recipient, message ID), messages fetched (recipient, count), persistence save/load, startup/shutdown, webhook registered/deregistered, analytics request
- **WARNING**: Auth failure (IP if available), corrupt persistence file skipped, webhook delivery failure
- **ERROR**: File I/O failure on save/load

### MCP Server

Log at these points:
- **INFO**: Config loaded (mask secret to first 4 chars + `***`), tool invocations (tool name, params), channel notification sent
- **WARNING**: Relay unreachable, auth error from relay
- **ERROR**: Unexpected exceptions in tool handlers, local webhook server failure

### Configuration

- `LOG_LEVEL` env var, defaults to `INFO`
- Format: `%(asctime)s %(levelname)s %(name)s %(message)s`
- Logger names: `claude_tunnel.relay` and `claude_tunnel.mcp`

## 2. Message Chunking

The relay transparently chunks large messages and reassembles them on fetch.

### Sending (relay-side, `POST /messages`)

When `content` exceeds `MAX_MESSAGE_SIZE` (default 50KB, configurable via env var):

1. Split content into chunks of `MAX_MESSAGE_SIZE` bytes
2. Generate a shared `chunk_group` UUID
3. Store each chunk as a separate message with extra fields:
   ```json
   {
     "id": "uuid",
     "from_name": "alice",
     "to": "bob",
     "content": "<chunk content>",
     "timestamp": "...",
     "chunk_group": "group-uuid",
     "chunk_index": 0,
     "chunk_total": 3
   }
   ```
4. All chunks stored atomically under the recipient's lock

Messages under the limit are stored as-is (no chunk fields).

### Fetching (relay-side, `GET /messages/{recipient}`)

1. Scan the recipient's queue for chunk groups
2. For each group: if all chunks are present, reassemble content in order and return as a single message (strip chunk fields)
3. If a group is incomplete (not all chunks arrived), hold those chunks back — do not return or clear them
4. Non-chunked messages are returned as normal

### Limits

- `MAX_MESSAGE_SIZE`: default `51200` (50KB), configurable via env var
- No limit on number of chunks per message (bounded by `MAX_MESSAGES` cap)

## 3. Analytics

### In-Memory Counters

Track alongside existing state (not in a separate data structure):

```python
analytics: dict = {
    "total_sent": 0,
    "total_delivered": 0,
    "per_participant": {
        "alice": {"sent": 0, "received": 0},
        "bob": {"sent": 0, "received": 0}
    },
    "hourly_volume": {}  # {"2026-04-08T10:00:00Z": 5, ...}
}
```

- `total_sent` incremented on `POST /messages`
- `total_delivered` incremented on `GET /messages/{recipient}` (by count of messages returned)
- `per_participant[name].sent` incremented on send
- `per_participant[name].received` incremented on fetch
- `hourly_volume[hour]` incremented on send (hour truncated to `YYYY-MM-DDTHH:00:00Z`)
- Hourly volume entries older than 72 hours are pruned on each write

### Persistence

Analytics are saved in `messages.json` under a top-level `"analytics"` key alongside `"messages"` and `"participants"`. Loaded on startup.

### Endpoint

`GET /analytics` — behind same bearer token auth.

Response:
```json
{
  "total_messages_sent": 142,
  "total_messages_delivered": 130,
  "messages_pending": 12,
  "participants": {
    "alice": {"sent": 80, "received": 62},
    "bob": {"sent": 62, "received": 68}
  },
  "hourly_volume": [
    {"hour": "2026-04-08T10:00:00Z", "count": 15},
    {"hour": "2026-04-08T11:00:00Z", "count": 22}
  ],
  "uptime_seconds": 3600
}
```

`messages_pending` is computed live from current queue sizes.

### CLI Tool (`analytics.py`)

A standalone script that hits `GET /analytics` and renders output using `rich`:

- **Summary table**: total sent, delivered, pending, uptime
- **Participant table**: per-participant sent/received counts
- **Hourly bar chart**: horizontal bars showing message volume per hour (last 72h)

Configuration: reads the same `~/claude-tunnel/config.json` for `relay_url` and `relay_secret`.

Usage: `python analytics.py`

### New Dependency

- `rich` added to `requirements.txt`

## 4. MCP Channels (Push Notifications)

### Goal

When a message arrives at the relay for a recipient, push it to their Claude Code session immediately via MCP Channels.

### Relay Side

**New endpoints:**

| Method | Path | Body | Response | Description |
|--------|------|------|----------|-------------|
| `POST` | `/webhooks` | `{"instance_name": str, "callback_url": str}` | `{"status": "registered"}` | Register a webhook for push delivery |
| `DELETE` | `/webhooks/{instance_name}` | — | `{"status": "removed"}` | Deregister a webhook |

**Webhook storage**: In-memory dict `webhooks: dict[str, str]` mapping instance name to callback URL. Not persisted — MCP servers re-register on startup.

**Push behavior** (in `POST /messages`):
1. After storing the message, check if the recipient has a registered webhook
2. If yes, fire an async POST to the callback URL with the message payload
3. If the webhook POST fails (timeout, connection error), log a warning and continue — the message stays in the queue for polling/long-poll pickup
4. Webhook POST timeout: 5 seconds

### MCP Server Side

**Local webhook receiver**:
- On startup, bind a lightweight HTTP server (using `uvicorn` + a small FastAPI app) on `127.0.0.1` with a random available port in a background thread
- Register with relay: `POST /webhooks {"instance_name": "<name>", "callback_url": "http://127.0.0.1:<port>/incoming"}`
- `POST /incoming` handler: receives message payload, sends `notifications/claude/channel` to Claude Code via the MCP server's session object (both run in the same process)

**Channel capability**:
- MCP server declares the `claude/channel` experimental capability at registration
- Channel notification format:
  ```json
  {
    "method": "notifications/claude/channel",
    "params": {
      "channel": "claude-tunnel",
      "message": "[timestamp] sender: content"
    }
  }
  ```

**Startup with `--channels` flag**:
- Claude Code must be started with `--channels` to enable channel notifications
- Document this in README

**Shutdown**:
- On MCP server shutdown, deregister webhook: `DELETE /webhooks/{instance_name}`

## 5. Long-Polling Fallback

### Behavior

`GET /messages/{recipient}` gains an optional `wait` query parameter:

- `?wait=30` — hold the connection open for up to 30 seconds if no messages are available
- If a message arrives during the wait, return immediately with the messages
- If timeout expires with no messages, return empty list `[]`
- No `wait` param (or `wait=0`) — existing instant-return behavior
- Max wait: 60 seconds (clamp higher values)

### Implementation

- Per-recipient `asyncio.Event` stored in a dict (like locks)
- On `POST /messages`, after storing, call `event.set()` for the recipient, then immediately `event.clear()`
- On `GET /messages/{recipient}` with `wait>0` and empty queue: `await asyncio.wait_for(event.wait(), timeout=wait)`
- If the event fires, re-check the queue under the lock and return messages

### Why

Channels is the primary push mechanism, but long-polling serves as a reliable fallback when:
- Channels feature is disabled or buggy (it's still in preview)
- Webhook delivery fails
- User prefers not to use `--channels`

## 6. README Updates

- Fix config path: `~/.claude-tunnel.json` → `~/claude-tunnel/config.json`
- Fix config priority line: `env vars > ~/claude-tunnel/config.json > defaults`
- Add section on Channels setup (declaring capability, `--channels` flag)
- Add section on long-polling (`?wait=N` parameter)
- Add analytics CLI usage
- Add `rich` to the dependency install instructions
- Update project structure to include `analytics.py`

## Updated Project Structure

```
claude_tunnel/
├── relay_server.py         # FastAPI relay with analytics, webhooks, long-polling
├── mcp_server.py           # MCP server with channels + local webhook receiver
├── analytics.py            # CLI tool for terminal analytics charts
├── requirements.txt        # + rich
├── requirements-dev.txt
├── tests/
│   ├── test_relay.py       # + analytics, chunking, long-polling, webhook tests
│   ├── test_mcp.py         # + channel notification tests
│   └── test_integration.py
├── pyproject.toml
└── README.md
```

## New/Updated Dependencies

**Runtime (`requirements.txt`):**
- `rich>=13.0.0` (terminal charts in analytics CLI)

No other new dependencies — `uvicorn` and `fastapi` are already present for the local webhook server.

## Out of Scope (unchanged)

- End-to-end encryption
- Message delivery guarantees / retry (beyond webhook fallback to polling)
- Multi-room / channel support (rooms)
- Web UI dashboard (terminal charts only)
- Per-user auth tokens
