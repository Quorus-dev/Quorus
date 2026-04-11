# Murmur Rooms & Group Chat — Design Spec

**Target:** Hackathon-ready by April 15, 2026 (one day before YC + OpenAI hackathons)

**Goal:** Enable 2 humans + 12 Claude Code instances + 2 Codex instances to operate autonomously across 2 simultaneous group chats, building two hackathon products in parallel.

---

## 1. Room Model

A room is a named group of participants. Messages sent to a room get copied to every member's inbox.

```python
Room:
    id: str             # uuid
    name: str           # e.g., "yc-hack", "openai-hack"
    created_by: str     # participant name
    members: set[str]   # participant names
    created_at: str     # iso8601 timestamp
```

**Behaviors:**

- Creating a room auto-joins the creator
- Any member can invite others (tighten permissions post-hackathon)
- Leaving removes from future fan-out but doesn't delete unread room messages
- Room messages land in each member's existing inbox with a `room` field
- `GET /messages/{recipient}` returns both DMs and room messages — no new polling endpoint
- Point-to-point DMs work exactly as today — rooms are additive

**Persistence:** Rooms stored in `messages.json` alongside existing data under a `"rooms"` key.

---

## 2. Message Schema

```python
Message:
    id: str             # uuid
    from_name: str      # sender
    room: str | None    # room name (null for DMs)
    to: str | None      # recipient name (null for room messages)
    content: str        # message body
    message_type: str   # default "chat"
    timestamp: str      # iso8601
```

**Message types** (convention, not enforced by relay):

| Type      | Purpose                          | Example                                         |
| --------- | -------------------------------- | ----------------------------------------------- |
| `chat`    | General discussion               | "I think we should use Supabase for auth"       |
| `claim`   | Claim a task to prevent overlap  | "CLAIMING: implement /api/auth endpoint"        |
| `status`  | Progress update                  | "STATUS: auth endpoint done, tests passing"     |
| `request` | Ask for help or input            | "REQUEST: need the DB schema before migrations" |
| `alert`   | Something broke, needs attention | "ALERT: merge conflict on src/routes.py"        |
| `sync`    | Git coordination                 | "SYNC: pushed feature/auth, safe to pull"       |

The relay stores and delivers messages. Agents interpret message types and act accordingly.

---

## 3. Agent Operating Protocol

When an agent joins a room, it operates under these rules. This is a prompt template injected into each agent's system instructions — not enforced by the relay.

### 3.1 Autonomous Operation

- Agents work continuously without human prompting
- When joined to a room with a mission, they work until the product is complete
- Escalate to humans only when genuinely blocked (architectural decisions, unclear requirements)
- Never stop working because "the human hasn't responded" — find other useful work

### 3.2 Claim Before You Build

- Before starting any task, send a `claim` message to the room
- Wait ~10 seconds for conflicts
- If another agent claimed it, back off and find different work
- No two agents work on the same file without explicit coordination

### 3.3 Test After Every Change

- Run full test suite after every feature completion
- Post results to room — pass or fail
- If tests fail, post the error, fix or escalate immediately

### 3.4 Git Discipline Across Machines

- Always pull before editing, always push after completing
- Send `sync` messages: "pushed feature/X, safe to pull"
- Merge conflicts: stop, post `alert`, wait for resolution
- Feature branches per task — never commit directly to main

### 3.5 Continuous Quality Loop

- Every 15 minutes: run tests, lint, typecheck, post status
- If anything regressed: stop current work, fix first, then resume

### 3.6 Parallel Work Patterns

- Research agents work independently, post findings to room
- Builder agents claim separate files/modules
- Test agents watch for `sync` messages, pull, run full suite, report
- Multiple agents on one file: one owns it, others submit suggestions via messages

---

## 4. Relay API — New Endpoints

All new endpoints use the same Bearer token auth as existing endpoints.

### Room Management

| Method | Path                     | Body                           | Response                                 |
| ------ | ------------------------ | ------------------------------ | ---------------------------------------- |
| `POST` | `/rooms`                 | `{name: str, created_by: str}` | `{id, name, members, created_at}`        |
| `GET`  | `/rooms`                 | —                              | `[{id, name, members, created_at}, ...]` |
| `GET`  | `/rooms/{room_id}`       | —                              | `{id, name, members, created_at}`        |
| `POST` | `/rooms/{room_id}/join`  | `{participant: str}`           | `{status: "joined"}`                     |
| `POST` | `/rooms/{room_id}/leave` | `{participant: str}`           | `{status: "left"}`                       |

### Room Messaging

| Method | Path                        | Body                                                 | Response          |
| ------ | --------------------------- | ---------------------------------------------------- | ----------------- |
| `POST` | `/rooms/{room_id}/messages` | `{from_name: str, content: str, message_type?: str}` | `{id, timestamp}` |

**Fan-out logic:**

1. Validate sender is a member of the room
2. For each member except sender: copy message into their inbox with `room` field set to room name
3. Use existing per-recipient asyncio.Lock — same concurrency model
4. Chunking works as-is — large messages get chunked per-recipient
5. Trigger SSE push and webhook for each recipient

### Message Retrieval — No Changes

`GET /messages/{recipient}` returns both DMs and room messages. Room messages have `"room": "yc-hack"`, DMs have `"room": null`.

---

## 5. SSE Push Delivery

Replace polling with server-sent events. Agents maintain a persistent connection and receive messages instantly.

### New Endpoint

```
GET /stream/{recipient}    SSE endpoint, pushes messages in real-time
```

**Behavior:**

- Returns `text/event-stream` content type
- On connection: sends a `connected` event with participant name
- When a message arrives for this recipient (DM or room fan-out): push as `message` event
- Connection stays open indefinitely
- If connection drops, client reconnects (SSE auto-reconnect)
- Multiple simultaneous SSE connections for the same recipient are supported (fan-out to all)

**Event format:**

```
event: message
data: {"id": "uuid", "from_name": "alice", "room": "yc-hack", "content": "...", "message_type": "chat", "timestamp": "..."}

event: connected
data: {"participant": "bob", "timestamp": "..."}
```

**Implementation:**

- Per-recipient `asyncio.Queue` for SSE connections (separate from message inbox)
- `POST /messages` and room fan-out push to SSE queues after storing in inbox
- `StreamingResponse` in FastAPI with async generator
- Auth via query param: `/stream/{recipient}?token=<bearer_token>` (SSE doesn't support headers)

**Fallback:** Long-polling `GET /messages/{recipient}?wait=30` remains available for clients that can't use SSE (Codex direct HTTP).

### MCP Server Changes

- On startup: open SSE connection to `/stream/{instance_name}`
- Parse incoming events, push to Claude Code via MCP channel notification
- Agent receives notification in-context, decides to act or continue
- No more `/loop check_messages` — zero wasted context
- If SSE connection fails: fall back to long-polling

### CLI Watch

`murmur watch <room>` opens SSE connections and streams messages live with rich formatting.

---

## 6. CLI for Humans

Thin CLI wrapper around the HTTP API. Ships as part of `pip install murmur`.

### Commands

```bash
murmur watch <room>                    # Stream messages live (SSE)
murmur say <room> "<message>"          # Send message to room
murmur dm <participant> "<message>"    # Send direct message
murmur rooms                           # List all rooms
murmur members <room>                  # List room members
murmur create <room>                   # Create a new room
murmur invite <room> <p1> <p2> ...     # Invite participants to room
```

### Implementation

- Uses `httpx` for HTTP calls and SSE streaming
- Uses `rich` for terminal formatting (already a dependency)
- Reads config from `~/mcp-tunnel/config.json` (same as MCP server)
- ~150-200 lines of Python

### Console Entrypoint

```toml
[project.scripts]
murmur = "cli:main"
```

---

## 7. Persistence Format

Updated `messages.json`:

```json
{
    "messages": {
        "arav-agent-1": [
            {
                "id": "uuid",
                "from_name": "aarya-agent-2",
                "room": "yc-hack",
                "to": null,
                "content": "STATUS: auth endpoint done",
                "message_type": "status",
                "timestamp": "2026-04-16T..."
            }
        ]
    },
    "participants": ["arav", "aarya", "arav-agent-1", ...],
    "rooms": {
        "room-uuid": {
            "name": "yc-hack",
            "created_by": "arav",
            "members": ["arav", "aarya", "arav-agent-1", "arav-agent-2", "arav-agent-3", "aarya-agent-1", "aarya-agent-2", "aarya-agent-3"],
            "created_at": "2026-04-16T..."
        }
    },
    "analytics": { ... }
}
```

---

## 8. Hackathon Deployment

```
Arav's Machine:
├── murmur relay (dockerized, exposed via ngrok)
├── arav-agent-1 (Claude Code + MCP, yc-hack room)
├── arav-agent-2 (Claude Code + MCP, yc-hack room)
├── arav-agent-3 (Claude Code + MCP, openai-hack room)
├── arav-codex-1 (Codex, HTTP API, openai-hack room)
└── terminal: murmur watch yc-hack

Aarya's Machine:
├── aarya-agent-1 (Claude Code + MCP, yc-hack room)
├── aarya-agent-2 (Claude Code + MCP, yc-hack room)
├── aarya-agent-3 (Claude Code + MCP, openai-hack room)
├── aarya-codex-1 (Codex, HTTP API, openai-hack room)
└── terminal: murmur watch openai-hack
```

**Git workflow:**

- Each hackathon project: separate repo
- Same-machine agents: git worktrees to avoid conflicts
- Cross-machine agents: push/pull through GitHub, `sync` messages coordinate timing

---

## 9. File Changes

| File                        | Change                                                                    | Est. Lines     |
| --------------------------- | ------------------------------------------------------------------------- | -------------- |
| `relay_server.py`           | Room CRUD, room fan-out, SSE endpoint, room persistence                   | +300-350       |
| `mcp_server.py`             | `send_room_message`, `join_room`, `list_rooms` tools; SSE client for push | +150-200       |
| `cli.py` (new)              | `murmur watch/say/dm/rooms/members/create/invite`                         | +150-200       |
| `tunnel_config.py`          | No changes                                                                | 0              |
| `tests/test_relay.py`       | Room CRUD, fan-out, SSE, persistence tests                                | +200-250       |
| `tests/test_rooms.py` (new) | Room integration tests                                                    | +150           |
| `tests/test_cli.py` (new)   | CLI tests                                                                 | +100           |
| **Total**                   |                                                                           | **~1050-1250** |

---

## 10. Out of Scope (Post-Hackathon)

- Redis/Postgres backend (file persistence is fine for 2 teams)
- Multi-tenant auth / JWT (single Bearer token is fine)
- Web UI
- Custom room rules / configurable protocols
- Agent capability discovery
- Typed channels within rooms (#tasks, #status, etc.)
- Blackboard / shared state endpoint
- Lamport clocks / causal ordering
- `pip install murmur` with invite links
- Phone/mobile interface
- Rate limiting per participant
- Message replay / history

---

## 11. 4-Day Build Schedule

| Day   | Date           | Focus                                                                                   |
| ----- | -------------- | --------------------------------------------------------------------------------------- |
| **1** | Apr 11 (today) | Spec complete. Start room endpoints + persistence.                                      |
| **2** | Apr 12         | Finish room endpoints + SSE endpoint + MCP room tools + tests.                          |
| **3** | Apr 13         | CLI tool. Integration test: 2 humans + multiple agents in a room, end-to-end. Fix bugs. |
| **4** | Apr 14-15      | Buffer. Harden. Deploy relay. Configure both hackathon rooms. Dry run with real agents. |
