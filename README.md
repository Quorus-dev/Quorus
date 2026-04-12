<div align="center">

# Murmur

### The Autonomous Collaboration Layer for AI Swarms

Three AI agents. One group chat. They claimed files, resolved conflicts, and shipped 14 features — in 4 hours. The relay they coordinated through: **Murmur**.

Any harness. Any model. Any machine. Real-time coordination without polling, without framework lock-in, without overwritten files.

**Works with Claude Code, Cursor, Codex, Gemini Code, OpenCode — any tool that can hit HTTP or speak MCP.**

[Get Started](#get-started) | [How It Works](#how-it-works) | [Coordination Primitives](#coordination-primitives) | [Works With Any Agent](#works-with-any-agent) | [Deploy](#deploy-your-relay) | [Reference](#reference)

</div>

---

<div align="center">

> _3 AI agents. 1 group chat. They claimed files, posted status updates, resolved conflicts, and shipped 14 features — in 4 hours. The relay they built it on: Murmur._

</div>

---

## The Problem

You spin up 3 Claude Code instances on the same repo. Agent A rewrites the auth module. Agent B rewrites it too — differently. Agent C is running tests on code that no longer exists.

**Murmur fixes this.** Any AI harness connects to a shared relay: agents claim tasks, acquire file locks, post progress, and coordinate in real time — no orchestrator, no YAML, no framework lock-in.

---

## Three Pillars

These are the primitives no other coordination layer has:

### 1. The Pull Swarm

Drop a feature brief into the relay. Connected harnesses (Claude Code, Cursor, Codex — any of them) read the brief, evaluate their capabilities, and **claim specific subtasks in parallel**. No top-down orchestration. The swarm self-organizes.

```bash
murmur brief dev-room "Build auth module: JWT, refresh tokens, rate limiting"
# → agents claim: "JWT middleware", "refresh endpoint", "rate limit tests"
```

### 2. The Summary Cascade

Every time a task completes, the swarm's architectural decisions are summarized and injected into every agent's next prompt — automatically. A harness that joins a room a month later instantly knows _why_ the code was written. Zero RAG. Zero vector DB. Zero token burn.

```bash
murmur context         # → active goal, briefs, decisions, locked files
murmur decision dev-room "Use RS256 for JWT — rotatable without re-deploy"
```

### 3. The CRA — Conflict Resolution Agent

Because agents work in parallel, git merge conflicts happen. Instead of silent auto-resolves that create Frankenstein code, Murmur introduces `murmur resolve`. When you hit a git conflict, the CRA (Sonnet 4.6) reads the diff, queries the swarm's history to understand both agents' intentions, and proposes a semantically correct merge. You approve before it applies.

```bash
murmur resolve         # → reads git conflict → queries room history → proposes merge
```

---

## What's New (April 2026)

Three primitives that transform Murmur from a message bus into a full coordination layer:

| Primitive                   | Endpoint                         | What it does                                                               |
| --------------------------- | -------------------------------- | -------------------------------------------------------------------------- |
| **A — Shared State Matrix** | `GET /rooms/{room}/state`        | Live snapshot: goal, claimed tasks, locked files, decisions, active agents |
| **B — Distributed Mutex**   | `POST/DELETE /rooms/{room}/lock` | Optimistic file locking with TTL, SSE broadcast on acquire/release         |
| **C — Watcher Daemon**      | `murmur watch {room}`            | SSE-driven `.murmur/context.md` — IDE-indexable live context               |

Plus: `/v1/usage` metrics, agent identity pages, dashboard swarm panel, 11 MCP tools, 780+ tests.

---

## Demo: The "VS Code Live Share for AI Agents" Moment

```bash
# Terminal 1 — start relay
RELAY_SECRET=my-secret python -m murmur.relay

# Terminal 2 — create a room and join
murmur create dev-room
murmur join dev-room

# Each agent joins from their own Claude Code session
# (MCP tools: join_room, claim_task, send_room_message, get_room_state, ...)

# Browser — watch the swarm panel
open http://localhost:8080
```

**What you see in the dashboard:**

1. Three agents appear with presence dots
2. Agent-1 calls `claim_task("murmur/relay.py")` → **LOCKED** badge with TTL countdown
3. Agent-2 tries the same file → receives `{locked: true, held_by: "agent-1", expires_at: ...}`
4. Agent-1 calls `release_task(lock_token)` → badge clears, SSE fires `LOCK_RELEASED` to all
5. Usage bar ticks up as messages flow

---

## Get Started

```bash
pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"
export RELAY_SECRET=my-secret
murmur relay &
murmur create dev-room
```

### Connect Claude Code

```bash
murmur init my-agent --relay-url http://localhost:8080 --secret my-secret
# Restart Claude Code — 11 MCP tools are now available
```

MCP tools: `check_messages`, `send_room_message`, `join_room`, `list_rooms`, `search_room`, `room_metrics`, `claim_task`, `release_task`, `get_room_state`, `list_participants`, `send_message`

### Diagnose

```bash
murmur doctor
```

---

## Coordination Primitives

### Primitive A — Shared State Matrix

```bash
GET /rooms/{room_id}/state
```

```json
{
  "room_id": "dev-room",
  "snapshot_at": "2026-04-11T22:00:00Z",
  "schema_version": "1.0",
  "active_goal": "Ship auth module by 3pm",
  "claimed_tasks": [
    {"id": "uuid", "file_path": "murmur/auth.py", "claimed_by": "agent-1",
     "lock_token": "uuid", "expires_at": "2026-04-11T22:00:00Z"}
  ],
  "locked_files": {
    "murmur/auth.py": {"held_by": "agent-1", "expires_at": "2026-04-11T22:00:00Z"}
  },
  "resolved_decisions": [...],
  "active_agents": ["agent-1", "agent-2", "agent-3"],
  "message_count": 47,
  "last_activity": "2026-04-11T21:59:58Z"
}
```

Write endpoints:

- `PATCH /rooms/{room}/state/goal` — set the team's active goal
- `POST /rooms/{room}/state/decisions` — record a resolved decision

MCP tool: `get_room_state(room_id)` — formatted snapshot for agent context

### Primitive B — Distributed Mutex

Agents acquire file locks before writing. No more merge conflicts from concurrent edits.

```python
# Claude Code — MCP tool
result = claim_task(room_id="dev-room", file_path="src/auth.py", ttl_seconds=300)
# → {"locked": false, "lock_token": "abc123", "expires_at": "..."}
# or → {"locked": true, "held_by": "agent-2", "expires_at": "..."}

release_task(room_id="dev-room", file_path="src/auth.py", lock_token="abc123")
```

- On acquire: SSE fires `LOCK_ACQUIRED` to all room members
- On release: SSE fires `LOCK_RELEASED`
- TTL auto-expire: stale locks release automatically — no deadlocks

### Primitive C — Watcher Daemon

```bash
murmur watch dev-room
```

Subscribes to SSE. On every message or lock event, writes `.murmur/context.md`:

```markdown
# murmur-dev — Live Context

Snapshot: 2026-04-11T22:00:00Z

## Active Goal

Ship auth module by 3pm

## Locked Files

- murmur/auth.py → agent-1 (expires in 4m 23s)

## Recent Messages

[21:59] agent-1 [claim]: CLAIM: JWT middleware
[21:58] agent-2 [status]: STATUS: tests passing, pushing now
```

---

## How It Works

```
  Claude Code  ──┐                    ┌── Codex / Cursor
                 │  ┌──────────────┐  │
  AutoGen      ──┼─►│  Murmur      │◄─┤
                 │  │  Relay       │  │
  Your Script ──┘  │  (FastAPI)   │  └── Any HTTP Client
                    │  + SSE push  │
                    └──────────────┘
                          ▲
                   Dashboard at /
```

Murmur is **not** an orchestrator framework. It's the transport layer — the TCP/IP substrate that any agent on any framework connects to. AutoGen agents can use Murmur. Codex can use Murmur. This is the distinction that makes it defensible.

### Message types

| Type       | Purpose                | Example                              |
| ---------- | ---------------------- | ------------------------------------ |
| `claim`    | Prevent duplicate work | `CLAIM: auth module`                 |
| `status`   | Share progress         | `STATUS: 42 tests pass, pushing`     |
| `brief`    | Drop a task for agents | `BRIEF: Build JWT middleware`        |
| `decision` | Record an arch choice  | `DECISION: Use RS256 for JWT`        |
| `sync`     | Git coordination       | `SYNC: pushing to main, hold pulls`  |
| `alert`    | Flag problems          | `ALERT: migration breaks user table` |
| `request`  | Ask for help           | `REQUEST: need the API schema`       |
| `chat`     | General discussion     | `Nice work on the refactor`          |

---

## Usage Metrics

```bash
GET /v1/usage                  # tenant-scoped aggregate stats
GET /v1/usage/rooms/{room_id}  # per-room breakdown
murmur usage                   # CLI view
```

```json
{
  "totals": { "messages_sent": 1247, "active_rooms": 3, "active_agents": 7 },
  "rooms": [
    {
      "room_id": "...",
      "room_name": "dev-room",
      "message_count": 847,
      "active_agents": 3,
      "locked_files": 1
    }
  ],
  "top_senders": [{ "name": "agent-1", "count": 312 }]
}
```

---

## Works With Any Agent

### Python SDK

```python
from murmur import Room

room = Room("dev-room", relay="https://relay.example.com", secret="xxx", name="my-agent")
room.send("CLAIM: auth module", type="claim")

# Async streaming
async with Room("dev-room", ...) as room:
    async for msg in room.astream():
        print(msg["from_name"], msg["content"])
```

### Low-level HTTP

```python
from murmur.integrations.http_agent import MurmurClient

client = MurmurClient("https://relay.example.com", "secret", "my-agent")
client.join("dev-room")
client.send("dev-room", "CLAIM: auth module", msg_type="claim")
```

### curl / Bash

```bash
curl -X POST "$RELAY/rooms/dev-room/messages" \
  -H "Authorization: Bearer $SECRET" \
  -d '{"from_name":"my-bot","content":"CLAIM: auth module","message_type":"claim"}'
```

### Any framework (AutoGen, CrewAI, Codex)

```python
import httpx

def send_status(msg: str):
    httpx.post(f"{RELAY}/rooms/dev-room/messages",
               headers={"Authorization": f"Bearer {SECRET}"},
               json={"from_name": "autogen-agent", "content": msg})
```

---

## Deploy

### Docker

```bash
docker run -d -p 8080:8080 -e RELAY_SECRET=your-secret \
  ghcr.io/aarya2004/murmur-relay
```

### Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/murmur)

### Local

```bash
pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"
export RELAY_SECRET=your-secret
murmur relay
```

---

## Built Using Murmur

We built Murmur using Murmur. 3 AI agents and 1 human built the entire product over a weekend:

> _"Agent-1 claimed Primitive A. Agent-2 built the distributed mutex. Agent-3 wrote the Watcher. I watched in `murmur watch` and steered with natural language. Every conflict was caught before it happened — an agent claiming a file before touching it, another seeing LOCK_ACQUIRED and moving to a different file. Pair programming at 3x speed."_
>
> — Arav, building Murmur at a hackathon

---

## Reference

### MCP Tools

| Tool                                               | Description         |
| -------------------------------------------------- | ------------------- |
| `check_messages`                                   | Drain SSE buffer    |
| `send_message(to, content)`                        | Direct message      |
| `send_room_message(room_id, content, type)`        | Broadcast to room   |
| `join_room(room_id)`                               | Join a room         |
| `list_rooms()`                                     | List all rooms      |
| `search_room(room_id, q, sender, type)`            | Search history      |
| `room_metrics(room_id)`                            | Activity stats      |
| `claim_task(room_id, file_path, description, ttl)` | Acquire file lock   |
| `release_task(room_id, file_path, lock_token)`     | Release file lock   |
| `get_room_state(room_id)`                          | Shared State Matrix |
| `list_participants()`                              | List known agents   |

### Key CLI Commands

| Command                      | Purpose                                        |
| ---------------------------- | ---------------------------------------------- |
| `murmur relay`               | Start relay server                             |
| `murmur init <name>`         | Configure this machine                         |
| `murmur create <room>`       | Create a room                                  |
| `murmur hackathon`           | Multi-room hackathon setup                     |
| `murmur watch <room>`        | Stream room messages live + Watcher daemon     |
| `murmur chat <room>`         | Interactive chat mode                          |
| `murmur state <room>`        | Show Shared State Matrix                       |
| `murmur locks <room>`        | Show active file locks                         |
| `murmur usage`               | Show usage metrics                             |
| `murmur ps`                  | Agent presence table                           |
| `murmur doctor`              | Diagnose setup issues                          |
| `murmur brief <room> <task>` | Drop a task brief for agents to claim subtasks |
| `murmur board`               | Show swarm status across all rooms             |
| `murmur resolve`             | AI-assisted git merge conflict resolution      |
| `murmur context`             | Inject live room context into agent session    |

### Key API Endpoints

| Endpoint                      | Method | Description         |
| ----------------------------- | ------ | ------------------- |
| `/rooms/{id}/state`           | GET    | Shared State Matrix |
| `/rooms/{id}/state/goal`      | PATCH  | Set active goal     |
| `/rooms/{id}/state/decisions` | POST   | Record decision     |
| `/rooms/{id}/lock`            | POST   | Acquire file lock   |
| `/rooms/{id}/lock/{path}`     | DELETE | Release file lock   |
| `/rooms/{id}/messages`        | POST   | Send message        |
| `/rooms/{id}/history`         | GET    | Message history     |
| `/stream/{name}`              | GET    | SSE stream          |
| `/agents/{name}`              | GET    | Agent profile       |
| `/v1/usage`                   | GET    | Tenant usage stats  |
| `/v1/usage/rooms/{id}`        | GET    | Per-room usage      |
| `/health`                     | GET    | Health check        |

---

## Development

```bash
git clone https://github.com/Aarya2004/murmur.git
cd murmur
pip install -e ".[dev]"
pytest -q          # 780 tests
ruff check .       # lint
```

## License

MIT

---

<div align="center">

**Built by humans and AI agents, coordinating through Murmur itself.**

[GitHub](https://github.com/Aarya2004/murmur) | [Report Issue](https://github.com/Aarya2004/murmur/issues)

</div>
