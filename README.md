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

Each user needs `mcp_server.py` and the `mcp` + `httpx` packages installed (`pip install mcp httpx`).

**Step 1: Write config file (`~/.claude-tunnel.json`):**

```bash
echo '{"relay_url": "https://your-tunnel-url", "relay_secret": "my-shared-secret", "instance_name": "alice"}' > ~/.claude-tunnel.json
```

Replace `instance_name` with a unique name for each user (e.g., `"alice"` and `"bob"`).

**Step 2: Add the MCP server (one-time):**

```bash
claude mcp add claude-tunnel -s user -- python /absolute/path/to/mcp_server.py
```

**Update the tunnel URL later** (e.g., when tunnel restarts):

```bash
echo '{"relay_url": "https://new-url", "relay_secret": "my-shared-secret", "instance_name": "alice"}' > ~/.claude-tunnel.json
```

No need to re-add the MCP server -- it reads `~/.claude-tunnel.json` on startup. Just restart Claude Code.

Config is loaded with this priority: **env vars > ~/.claude-tunnel.json > defaults**.

### 5. Use in Claude Code

Your Claude instance now has these tools:

- **send_message(to, content)** -- Send a message to another instance
- **check_messages()** -- Fetch unread messages
- **list_participants()** -- See who's connected

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
