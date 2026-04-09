# MCP Tunnel

A relay-based MCP system that lets multiple MCP-capable clients on different laptops communicate through a shared relay.

## How It Works

1. A central **relay server** (FastAPI) stores messages in per-recipient queues
2. Each laptop runs a local **MCP server** that exposes messaging tools to its harness
3. The MCP server makes HTTP calls to the relay
4. Messages are persisted to a JSON file on disk
5. Optional client-specific push notifications can surface new messages instantly, with standard tool polling as the portable fallback

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

### 4. Configure Your MCP Client (all users)

Each user needs `mcp_server.py` and the `mcp` + `httpx` packages installed (`pip install mcp httpx`).

**Step 1: Write config file (`~/mcp-tunnel/config.json`):**

```bash
mkdir -p ~/mcp-tunnel
echo '{"relay_url": "https://your-tunnel-url", "relay_secret": "my-shared-secret", "instance_name": "alice"}' > ~/mcp-tunnel/config.json
```

Replace `instance_name` with a unique name for each user (e.g., `"alice"` and `"bob"`).

The old `~/claude-tunnel/config.json` path is still accepted for backward compatibility.

If your harness supports a custom notification method and you want automatic push delivery, add notification settings:

```bash
echo '{"relay_url": "https://your-tunnel-url", "relay_secret": "my-shared-secret", "instance_name": "alice", "enable_background_polling": true, "push_notification_method": "notifications/claude/channel", "push_notification_channel": "mcp-tunnel"}' > ~/mcp-tunnel/config.json
```

If your harness does not support custom notifications, omit those fields. The standard MCP tools still work everywhere.

**Step 2: Register the stdio MCP server in your harness**

The server is a plain stdio MCP server:

```bash
python /absolute/path/to/mcp_server.py
```

How you register that command depends on the client:

- Claude Code: add it as an MCP server, optionally with `--channels` if you use `notifications/claude/channel`
- Codex, Cursor, or other MCP hosts: add the same stdio command in that client's MCP/server configuration
- Any client that only supports standard MCP tools can use `send_message`, `check_messages`, and `list_participants` without any push-specific setup

**Update the tunnel URL later** (e.g., when tunnel restarts):

```bash
echo '{"relay_url": "https://new-url", "relay_secret": "my-shared-secret", "instance_name": "alice"}' > ~/mcp-tunnel/config.json
```

No need to re-add the MCP server. It reads the config file on startup.

Config is loaded with this priority: **env vars > `~/mcp-tunnel/config.json` > legacy `~/claude-tunnel/config.json` > defaults**.

### 5. Use In Your Client

Your MCP client now has these tools:

- **send_message(to, content)** -- Send a message to another instance (large messages are auto-chunked)
- **check_messages()** -- Fetch unread messages (uses long-polling with 30s wait)
- **list_participants()** -- See known participant names

If you configured background polling plus a client-specific notification method, messages can be surfaced automatically. Otherwise, poll manually:

```
/loop 10s check_messages
```

## Compatibility

MCP Tunnel supports two modes:

### Standard MCP Tools

This is the portable mode and should work across Codex, Cursor, Claude Code, and any other MCP-capable harness. The client invokes the standard tools:

- `send_message`
- `check_messages`
- `list_participants`

### Optional Push Notifications

If a harness supports custom server-to-client notifications, you can configure:

- `enable_background_polling: true`
- `push_notification_method`
- `push_notification_channel` (optional)

For Claude Code, the compatible method today is `notifications/claude/channel` plus `--channels`.

This setup still works behind NAT and standard home networks because only the relay needs to be publicly reachable. Client laptops do not need inbound tunnels or webhook endpoints.

`check_messages` always remains the portable fallback. The raw API supports `GET /messages/{recipient}?wait=N` with `N` up to 60 seconds.

## Analytics

View relay usage stats in the terminal:

```bash
python analytics.py
```

`analytics.py` reads the same shared config and env vars as `mcp_server.py`.

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
| `MCP_TUNNEL_CONFIG_DIR` | unset | Override config directory |
| `ENABLE_BACKGROUND_POLLING` | `false` | Enable background relay polling in the MCP server |
| `ENABLE_CHANNELS` | `false` | Legacy alias for `ENABLE_BACKGROUND_POLLING` |
| `PUSH_NOTIFICATION_METHOD` | unset | Optional client-specific notification method |
| `PUSH_NOTIFICATION_CHANNEL` | `mcp-tunnel` | Optional notification channel name |

## Project Structure

```
claude_tunnel/
├── relay_server.py         # FastAPI relay with analytics, optional webhooks, long-polling
├── mcp_server.py           # MCP server with standard tools + optional push notifications
├── tunnel_config.py        # Shared config loading + env override logic
├── analytics.py            # CLI tool for terminal analytics charts
├── requirements.txt
├── requirements-dev.txt
├── tests/
│   ├── test_relay.py
│   ├── test_mcp.py
│   ├── test_integration.py
│   └── test_config.py
├── pyproject.toml
└── README.md
```

## Development

```bash
pip install -r requirements-dev.txt
pytest -v
ruff check .
```
