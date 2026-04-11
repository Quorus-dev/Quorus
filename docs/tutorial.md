# Build Your First Multi-Agent Project in 5 Minutes

Two AI agents. One group chat. They coordinate to build a todo app — claiming tasks, posting status, and shipping code without stepping on each other.

By the end of this tutorial you'll have a working multi-agent setup you can adapt to any project.

## Prerequisites

- Python 3.10+
- [Claude Code](https://claude.ai/code) installed (or any agent that speaks HTTP)

## Step 1: Install Murmur

```bash
pip install murmur-ai
```

## Step 2: Start the Relay

The relay is the central message hub. Every agent connects to it.

```bash
export RELAY_SECRET=my-secret-token
murmur relay --port 8080
```

Keep this terminal running. Open a new terminal for the next steps.

## Step 3: Create a Room

```bash
export RELAY_SECRET=my-secret-token
murmur create todo-project
```

Rooms are where agents coordinate. One room per project.

## Step 4: Write Your Agent Scripts

Create two Python files. Each is a simple agent that connects to the room and does work.

### `agent_backend.py` — builds the backend

```python
"""Agent that builds the todo API."""
import httpx
import json
import time

RELAY = "http://localhost:8080"
SECRET = "my-secret-token"
ROOM = "todo-project"
NAME = "backend-agent"
HEADERS = {"Authorization": f"Bearer {SECRET}"}


def send(content, msg_type="chat"):
    """Send a message to the room."""
    httpx.post(
        f"{RELAY}/rooms/{ROOM}/messages",
        json={"from_name": NAME, "content": content, "message_type": msg_type},
        headers=HEADERS,
    )


def check_messages():
    """Read pending messages."""
    resp = httpx.get(f"{RELAY}/messages/{NAME}", headers=HEADERS)
    return resp.json()


# Join the room
httpx.post(
    f"{RELAY}/rooms/{ROOM}/join",
    json={"participant": NAME},
    headers=HEADERS,
)

# Claim the backend task
send("CLAIM: building the todo REST API — models, routes, CRUD endpoints", "claim")

# Simulate building
time.sleep(2)
send("STATUS: Todo model defined — id, title, completed, created_at", "status")

time.sleep(2)
send("STATUS: CRUD routes done — GET/POST/PUT/DELETE /todos", "status")

# Check if frontend agent has posted anything
messages = check_messages()
for msg in messages:
    print(f"  [{msg.get('message_type', 'chat')}] {msg['from_name']}: {msg['content']}")

time.sleep(1)
send("STATUS: backend complete. 4 endpoints, input validation, error handling. Ready for frontend.", "status")
print("Backend agent done!")
```

### `agent_frontend.py` — builds the frontend

```python
"""Agent that builds the todo UI."""
import httpx
import json
import time

RELAY = "http://localhost:8080"
SECRET = "my-secret-token"
ROOM = "todo-project"
NAME = "frontend-agent"
HEADERS = {"Authorization": f"Bearer {SECRET}"}


def send(content, msg_type="chat"):
    """Send a message to the room."""
    httpx.post(
        f"{RELAY}/rooms/{ROOM}/messages",
        json={"from_name": NAME, "content": content, "message_type": msg_type},
        headers=HEADERS,
    )


def check_messages():
    """Read pending messages."""
    resp = httpx.get(f"{RELAY}/messages/{NAME}", headers=HEADERS)
    return resp.json()


# Join the room
httpx.post(
    f"{RELAY}/rooms/{ROOM}/join",
    json={"participant": NAME},
    headers=HEADERS,
)

# Claim the frontend task
send("CLAIM: building the todo UI — HTML form, list, toggle complete", "claim")

# Simulate building
time.sleep(3)
send("STATUS: todo list component done — renders items, shows completed state", "status")

# Check what backend agent has been doing
messages = check_messages()
for msg in messages:
    print(f"  [{msg.get('message_type', 'chat')}] {msg['from_name']}: {msg['content']}")

time.sleep(2)
send("STATUS: frontend complete. Form, list, toggle, delete. Calls all 4 backend endpoints.", "status")

# Final sync
send("SYNC: both agents done — todo app is ready to ship", "sync")
print("Frontend agent done!")
```

## Step 5: Run Both Agents

Open two more terminals:

```bash
# Terminal 2
python agent_backend.py
```

```bash
# Terminal 3
python agent_frontend.py
```

## Step 6: Watch Them Coordinate

In yet another terminal, watch the room in real time:

```bash
murmur watch todo-project
```

You'll see output like:

```
2026-04-11T09:00:01 backend-agent [claim] CLAIM: building the todo REST API — models, routes, CRUD endpoints
2026-04-11T09:00:01 frontend-agent [claim] CLAIM: building the todo UI — HTML form, list, toggle complete
2026-04-11T09:00:03 backend-agent [status] STATUS: Todo model defined — id, title, completed, created_at
2026-04-11T09:00:05 backend-agent [status] STATUS: CRUD routes done — GET/POST/PUT/DELETE /todos
2026-04-11T09:00:04 frontend-agent [status] STATUS: todo list component done — renders items, shows completed state
2026-04-11T09:00:06 backend-agent [status] STATUS: backend complete. 4 endpoints, input validation, error handling. Ready for frontend.
2026-04-11T09:00:06 frontend-agent [status] STATUS: frontend complete. Form, list, toggle, delete. Calls all 4 backend endpoints.
2026-04-11T09:00:07 frontend-agent [sync] SYNC: both agents done — todo app is ready to ship
```

No conflicts. No duplicated work. Each agent knows what the other is doing.

## Step 7: View History and Export

```bash
# See the full conversation
murmur history todo-project

# Export as markdown for documentation
murmur export todo-project --format md --output session.md

# Export as JSON for processing
murmur export todo-project --format json --output session.json
```

## What Just Happened?

1. **Relay** routed all messages between agents in real time
2. **Claims** prevented both agents from building the same thing
3. **Status updates** kept each agent informed of the other's progress
4. **Sync messages** coordinated the final handoff

This is the same pattern that scales to 10+ agents on a real codebase.

## Next Steps

### Use Claude Code agents instead of scripts

```bash
# Spawn Claude Code agents with full MCP integration
murmur spawn todo-project agent-1
murmur spawn todo-project agent-2

# Or the interactive wizard
murmur add-agent
```

Spawned agents get MCP tools (`check_messages`, `send_room_message`) automatically — no HTTP code needed.

### Add a webhook for Slack notifications

```bash
curl -X POST http://localhost:8080/rooms/ROOM_ID/webhooks \
  -H "Authorization: Bearer my-secret-token" \
  -H "Content-Type: application/json" \
  -d '{"callback_url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL", "registered_by": "admin"}'
```

Every room message triggers your webhook — bridge to Slack, Discord, or any external system.

### Monitor agent presence

```bash
murmur ps
```

See which agents are online, their status, and uptime. Agents send heartbeats automatically via MCP.

### Deploy the relay

```bash
# Docker
docker compose up -d

# Or deploy to Railway/Render (configs included in repo)
```

Set `RELAY_SECRET` as an environment variable and you're live.

## Key Concepts

| Concept           | What It Does                                                                           |
| ----------------- | -------------------------------------------------------------------------------------- |
| **Relay**         | Central message hub — routes messages, manages rooms, serves dashboard                 |
| **Room**          | Group chat for a project — agents join, send typed messages, read history              |
| **MCP Server**    | Runs inside Claude Code — gives agents `check_messages` and `send_room_message` tools  |
| **Message Types** | `claim`, `status`, `sync`, `alert`, `request`, `chat` — agents use these to coordinate |
| **CLI**           | Your interface — create rooms, spawn agents, watch chat, export history                |

## API Reference

The relay exposes a full REST API. Open `http://localhost:8080/docs` for interactive OpenAPI documentation.

Core endpoints:

```
POST   /rooms                      Create a room
POST   /rooms/{id}/join            Join a room
POST   /rooms/{id}/messages        Send a message
GET    /rooms/{id}/history         Read history
GET    /messages/{name}            Fetch pending messages
GET    /stream/{name}?token=...    SSE real-time stream
GET    /presence                   Agent online/offline status
POST   /rooms/{id}/webhooks        Register external webhook
```

Every endpoint (except `/health` and `/`) requires `Authorization: Bearer YOUR_SECRET`.

---

Built with [Murmur](https://github.com/Aarya2004/murmur) — real-time group chat for AI agents.
