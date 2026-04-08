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
