# Quorus — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-10-16 (release audit; see Recent Changes)

---

## Current State

Quorus (package: quorus) is the coordination layer for AI agent swarms. "VS Code Live Share for AI Agents" — any model, any machine, any platform coordinates in real-time.

**Branch:** `feat/wake-rebuild-aug26` (2126 tests passing — Wake Rebuild streams F/R/D/L/T complete + three review passes closed). `feat/may4-sprint` is the pre-rebuild ancestor.

**Package:** `pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"`

**Entry point:** `quorus` command opens the TUI hub by default (like `claude` or `gemini`).

**Repo layout (monorepo):**

```
quorus/                    # core: relay server + shim re-exports
packages/
  sdk/  → quorus_sdk       # client library (Room, QuorusClient) — httpx only
  cli/  → quorus_cli       # CLI commands (quorus ...) + ui.py theme module
  mcp/  → quorus_mcp       # MCP server (FastMCP tools) — httpx + mcp SDK
  tui/  → quorus_tui       # `quorus` / `quorus begin` Rich terminal hub
```

`quorus/sdk.py`, `quorus/cli.py`, `quorus/mcp_server.py`, `quorus/tui_hub.py`,
`quorus/integrations/http_agent.py`, `quorus/decorators.py`, `quorus/watcher.py`
are re-export shims so all historical `from quorus.X import Y` imports work.

**Setup (3 commands):**

```bash
pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"
quorus init <your-name> --relay-url <url> --secret <secret>
# restart claude code — done
quorus           # opens the hub
```

**Config:** `~/.quorus/config.json` (pointer) + `~/.quorus/profiles/<name>.json` (per-profile data). Legacy `~/mcp-tunnel/` + `~/.murmur/` still read.

**Profile split for cross-identity coordination:**

The TUI uses ONE profile at a time; switch via `quorus -w <name>` or by editing `~/.quorus/config.json`'s `current` field. Conventional split:

- `default` profile — `instance_name=arav-codex`, used by the Codex CLI / MCP servers / agents (so they continue to post under their agent identity).
- `human` profile — `instance_name=arav`, used by the human owner when typing in the TUI (so messages render as `@arav` not `@arav-codex`).

Why this matters: the relay enforces `JWT.sub == from_name` (anti-impersonation; returns 403 `Cannot send as another user`). For the human and the agent to appear as distinct senders, each must have its own participant + api_key in the same tenant. `quorus whoami` confirms the active identity.

**What's built:**

| Module                      | What                                                                                           |
| --------------------------- | ---------------------------------------------------------------------------------------------- |
| quorus/relay.py             | FastAPI relay: rooms, SSE fan-out, history, presence, rate limiting, health, admin             |
| quorus_mcp/                 | MCP server: 26 tools — messaging, rooms, locks, state, social verbs, 10 phase-1 primitives, approve  |
| quorus_cli/cli.py           | 30+ CLI commands (quorus ...) — `quorus` with no args opens TUI                                |
| quorus_cli/ui.py            | Shared theme, banner, spinner, error/success/info primitives                                   |
| quorus_tui/hub.py           | Full-screen TUI hub: rooms panel, agent list, live chat, first-run wizard                      |
| quorus/routes/room_state.py | Primitive A+B: GET state, PATCH goal, POST decisions, POST/DELETE locks (mutex)                |
| quorus/watcher.py           | Primitive C: SSE-driven daemon, writes .quorus/context.md for IDE indexing                     |
| quorus/dashboard.py         | Web dashboard: live messages + swarm activity panel + usage bar                                |
| quorus/backends/            | In-memory + Redis + Postgres + SQLite backends (incl. RoomStateBackend)                        |
| tests/                      | 2126 tests passing (`pytest -q`, ~2 min)                                                       |

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich, hatchling

**Key features:**

- Rooms with fan-out messaging (send once, all members receive)
- **SSE-only push delivery** — zero polling, instant delivery
- **Shared state matrix** — goal, locked files, claimed tasks, decisions, active agents
- **Distributed mutex locking** — claim files with TTL, SSE broadcast LOCK_ACQUIRED/LOCK_RELEASED
- **Watcher daemon** — writes .quorus/context.md for IDE indexing, event-driven via SSE
- **MCP tools** (11): send/receive, rooms, locks, state, metrics, search
- **JWT auth + API keys** (`quorus_sk_*` prefix, legacy `murm_sk_*` still accepted)
- **Portable join tokens** (`quorus_join_*` prefix, legacy `murm_join_*` still accepted)
- **Rate limiting** on all write + history endpoints
- **Transactional outbox** — atomic Postgres writes + background worker fan-out
- **Audit ledger** — message lifecycle events (MESSAGE*CREATED → FANOUT*\* → DELIVERED)
- **Account-based identity** — participant_id in JWT claims + FK columns
- Docker + Railway/Render/Fly.io deploy configs

**Website:** https://www.quorus.dev (Vite + React + Tailwind, Vercel deploy)

---

## Security Posture (post-audit 2026-04-15)

Critical fixes landed this session:

- **AppleScript injection** in watcher osascript → now passes args via argv (not string interpolation)
- **Agent-name injection** in spawn → validated against `[A-Za-z0-9_-]{1,64}` at entry
- **Bootstrap secret comparison** → `hmac.compare_digest` (constant-time)
- **Invite-join DoS** → IP rate limit (10/min)
- **Admin tenant creation DoS** → IP rate limit (5/min)
- **Config TOCTOU race** → atomic `os.open` with 0o600 from creation
- **Invite page XSS** → all template vars `html.escape`d

Remaining medium-severity items tracked in code review output.

---

## In Progress

**Dogfood phase (2026-10-08).** The autonomous loop now works with real
agents on one laptop: human posts `@open <task>` → an idle agent claims it,
posts a plan, does the work in the bound repo, commits, @-mentions a teammate
for review → reviewer wakes, reviews, hands findings back → fixer wakes and
fixes → reviewer approves. Proven live 3× in `~/dev/quorus-playground`
(todo CLI: build → 2 review findings fixed → approve; `clear`; `count`).

Live setup: `scripts/dogfood.sh up <repo> [room]` (launchd relay on
127.0.0.1:8787 + one wake daemon per agent + `dogfood` hub profile);
`scripts/dogfood.sh connect` points the human's interactive Claude Code /
Codex MCP at it as `*-desktop` identities. State in `~/.quorus/dogfood/`.

Live push into an OPEN Claude window works: `scripts/dogfood.sh claude`
(= `claude --dangerously-load-development-channels server:quorus`). Room
messages that @-mention your desktop identity (or DM it) arrive as
`<channel>` events and start a turn with no keystrokes — proven live: mention
→ "PONG — todo.py" posted back in 12s. Codex woken agents run with
`-s workspace-write` (Arav opted in 2026-10-08; proven: codex built
`version`, committed, Claude reviewed + approved).

Next:
1. PyPI publish. Release-ready 2026-10-10 (20/20 scenarios, clean-wheel install on 3.10 and
   3.13, CI green). Blocked on Arav: `quorus` is taken on PyPI by an unrelated quantum
   project, so pick a distribution name (e.g. `quorus-ai`); and a PyPI token.
2. Channels are a research preview: custom servers need the dev flag and a
   one-time confirm per launch; interactive Codex has no push API.
3. Codex's own `quorus say` from inside its sandbox failed to reach the relay
   once (final reply still lands via the daemon) — investigate.
4. reflexd.py is ~2.8k lines (500-line rule) — split into a package.
5. Later: optional fast decision gate before a wake (OpenAI Decisions API on gpt-6-luna, or
   TypeSafe Jev) to skip agent runs that would end in NO_REPLY. Only when a key is set.

## Recent Changes

### Daemon commit hygiene (2026-10-17)

Auto-commit subjects preserve conventional prefixes, add `chore:` to prose,
and stop at a whole word within 50 characters. Empty replies and oversized
words get a short conventional fallback. Generated `graphify-out/**` files
are excluded from staging and dirty checks, including tracked refreshes;
regressions live in `tests/test_reflexd_commit_hygiene.py`.
Follow-up: both pre-wake sync and publication restore committed graph files
before rebasing; excluding them from dirty checks alone left Git refusing
the rebase. Scratch-repo regressions cover upstream graph changes and verify
that publication stays linear (`tests/test_reflexd_graph_rebase.py`).

### Harness exception visibility (2026-10-17)

Raised harness errors now post a short crash/timeout status on the wake's
thread instead of silently returning. Only the exception type is exposed;
the existing self-reply check suppresses false failures after an agent's
own answer. Regression tests cover crashes, timeouts, self-replies and
cancellation (`tests/test_reflexd_harness_failure.py`).

### Proactive + notified for every agent; lead; catch-up (2026-10-17)

Verified live with claude + codex + gemini + opencode in one room (real
logins): all 4 answer a human in 22s; `@open` claimed and shipped by one agent
in 37s (it found a second bug and handed it to Claude); peer review handoff;
one agent in two rooms/two repos; presence `busy` seen for all 4; owner
banners sent. New: reflexd posts presence busy/active per wake (TUI shows
`⋯ @x working 1m 20s`); daemon pings the owner (shipped / needs you / done /
stuck, quorus/notifications/agent_events.py, REFLEXD_OWNER); home screen
"While you were away" digest; `--lead`: lead splits and assigns, non-leads
answer human room messages READ-ONLY (enforced per harness, never resume that
session). Gemini: each agent gets GEMINI_CLI_HOME (user's ~/.gemini had a
retired Google login → IneligibleTierError even with a key); needs
gemini-cli >= 0.64. Lead test: 2 jobs split across agents, on main in 53s.

### Linux verified in Docker (2026-10-16)

First Linux run (python:3.12-slim, wheel install, opencode 2.0.32 + free
DeepSeek): first-run wizard starts the local relay, two agents reply in ~8s,
fix published to main, peer review, no junk committed. Fixed in the CLI:
`agent remove` crashed on missing launchctl (daemon kept running), `agent
list` was empty, `add` claimed "restarts at login", no-pgrep images got a
duplicate daemon (now a verified pid file, quorus_cli/agent_proc.py), and
opencode 1.x (what `npm i -g opencode-ai` installs) is warned about: its
`run` rejects `--standalone`, every wake is "harness errored". Linux has no
restart-on-reboot: re-run `quorus agent add`.

### Any-model agents: opencode, Gemini, Ollama (2026-10-16)

`quorus agent add opencode|gemini` now get the same wake as Claude/Codex
(scripts/reflexd_models.py): Quorus room tools, room permission mode, session
resume, `--model`. `--name LABEL` allows many agents per tool
(`<you>-<label>-<tool>`). Verified live on opencode 2.0.30 with free
DeepSeek: two launchd agents, fix on main in 18s, peer review, then the
builder added tests on its own. Fixed on the way: opencode wrote files into
$PWD (`/` under launchd); 2.x ignores the old `permission` map (manual rooms
weren't read-only); project opencode.json beat our config (now
OPENCODE_CONFIG_CONTENT, secrets blanked); first-run race between two
opencode agents (one retry). Ollama works (qwen3:4b made the edit) but needs
OLLAMA_CONTEXT_LENGTH=32768 and is slow on a laptop. Gemini CLI: personal
Google login is dead (IneligibleTierError), needs an AI Studio key; built and
unit-tested, not yet live-verified.

### Fresh-user run + launch prep (2026-10-16)

Installed the wheel as a brand-new user and followed the README. Found and fixed:
- Daemon auto-committed `__pycache__` to the user's main; leftovers now ignored.
- Codex could never use its Quorus tools: MCP approval auto-cancelled in
  `codex exec`, and the user's `~/.codex/config.toml` quorus entry (human's
  secret) overrode the agent's. Fixed and verified live.
- Chatter: stray "/ defer" lines, agents forced to answer passing mentions,
  15-line replies. Second Ctrl-C printed a traceback. Hub never said how to add an agent.
- Repo: removed a hardcoded old-relay API key and stale secret configs; internal
  notes moved here; README has a real demo GIF and the new tagline.
- Still open: Linux daemon path untested; agent worktrees live outside the repo.

### Every agent answers the human (2026-10-16)

- A human's message, or any direct @-mention, wakes every agent it reaches,
  with no auction and no NO_REPLY option (Arav's rule: all agents answer him).
  Open tasks and agent-to-agent chatter still pick one agent, which may stay silent.
- Verified live in #demo: both agents answered an unaddressed message in ~15s.

### Release audit by Codex (2026-10-16)

Ten fixes, each with a failing-first test. Report kept out of git; summary:
- Secrets: redacted from relay access logs, Sentry events, daemon logs and
  harness stderr; credential files written via private temp + atomic replace.
- Auth: DM streams refuse the shared secret when DATABASE_URL is set;
  `quorus agent add` joins with the owner key in account mode (was HTTP 403).
- Paths: participant names validated before building daemon state paths;
  supervisor log follows the active config dir (was hard-coded to the real home).
- README now claims auto-wake only for Claude Code and Codex.
- Open (medium): agent worktrees live at `<repo-parent>/.quorus-worktrees/`,
  outside the bound repo; moving them needs a migration that keeps unpublished branches.

## Architecture

```
Any Agent (Claude Code / Codex / Cursor / Gemini / Ollama / browser)
    ↓
HTTP API or MCP tools
    ↓
[Quorus Relay (FastAPI)] ← SSE push / long-poll / webhook
    ↓
Fan-out to room members
    ↓
Each member's inbox → agent reads via check_messages / HTTP GET / SSE stream
```

---

## Key Decisions

- Package name: `quorus` (was `murmur-ai`)
- Entry command: `quorus` (was `murmur`)
- Config dir: `~/.quorus/` (was `~/mcp-tunnel/`, then `~/.murmur/`)
- `quorus` with no args opens TUI (matches claude/gemini convention)
- Legacy prefixes (`murm_sk_`, `murm_join_`) accepted on verify
- Free stack only, MIT licensed
- SSE-only delivery — no polling

---

## Contributors

- **Arav** (aravkek) — co-founder, parent Claude directing agents
- **Saad** — co-founder
- **Aarya** (Aarya2004) — co-founder
