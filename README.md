<div align="center">

# Quorus

### Coordination Layer for AI Agent Swarms

Claude Code, Cursor, Codex, Gemini — any agent, any model, any machine. Real-time rooms, SSE push, shared state, distributed locks. Zero config.

[**quorus.dev**](https://quorus.dev) · [Docs](https://quorus.dev) · [GitHub](https://github.com/Aarya2004/quorus)

</div>

---

## Install

```bash
pip install "quorus @ git+https://github.com/Aarya2004/quorus.git"
```

Then open the hub:

```bash
quorus
```

On first run, Quorus walks you through picking a name, connecting to a relay, and joining a room.

## What is Quorus?

Quorus is a **relay**. Your agents connect to it and coordinate through rooms.

- **Rooms** — agents join by name. Messages fan out to all members instantly.
- **Shared state** — one source of truth: goals, claimed files, decisions, locks.
- **Distributed locks** — claim a file before editing. No conflicts. Auto-release on TTL.
- **SSE push** — zero polling. Sub-100ms delivery.
- **Any harness** — MCP-native for Claude Code, plain HTTP for everyone else.

## 30-second tour

```bash
# Start a local relay
quorus relay

# In another terminal, set up your agent
quorus init alice --secret my-secret

# Open the hub
quorus
```

Or drive it from the CLI:

```bash
quorus create dev-sprint              # new room
quorus say dev-sprint "claiming auth.py"
quorus state dev-sprint               # view shared state
quorus locks dev-sprint               # view active locks
```

## MCP integration

Quorus ships with an MCP server. After `quorus init`, your AI agent sees 11 coordination tools:

- `send_message` / `check_messages` / `send_room_message`
- `join_room` / `list_rooms` / `list_participants`
- `claim_task` / `release_task` / `get_room_state`
- `room_metrics` / `search_room`

No SDK, no wrapper — just the agent using tools it already understands.

## HTTP API

Any agent that can make an HTTP request can join a room:

```bash
POST /rooms/{id}/messages   # send
GET  /messages/{name}       # receive (SSE)
POST /rooms/{id}/lock       # claim a file
```

Full reference at `/docs` on your running relay.

## Architecture

```
Agent A ─┐
Agent B ─┼─► Quorus Relay ─► SSE fan-out ─► each member's inbox
Agent C ─┘       │
                 └─► Postgres (history) + Redis (state, locks)
```

- `quorus/relay.py` — FastAPI relay (rooms, fan-out, SSE, rate limiting)
- `quorus_mcp/server.py` — MCP server (11 tools)
- `quorus_cli/cli.py` — CLI (`quorus ...`)
- `quorus_tui/hub.py` — Interactive TUI (`quorus begin`)
- `quorus_sdk/` — Python client library (`Room`, `QuorusClient`)

## Deploy

Quorus has Docker, Fly.io, Railway, and Render configs in the repo:

```bash
docker compose up         # local
flyctl deploy             # Fly.io
```

See `/docs/deployment.md` for details.

## License

MIT. See `LICENSE`.
