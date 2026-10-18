# Changelog

All notable changes to Quorus are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.5.1] — 2026-10-18

### Fixed
- Pacing never blocks the owner or skips.
- Say Ollama is down before waking on it, with pull hint on empty catalog.
- Rebuild a broken agent worktree and explain why.
- Worktrees-off never allows read-only writes.
- Gemini reads AGENTS.md and CLAUDE.md.
- A timed-out wake names its last step.
- Gemini bad model and opencode typo say so.
- A taken name can't lock out sign-in.
- Demo fallback checks login, shares only auth.
- Say why a model can't run, not 'exit 1'.

## [0.5.0] — 2026-10-17

First PyPI release, as `quorus-ai` (`pipx install quorus-ai`; the command is still `quorus`).

### Added

- **Any model.** `quorus agent add opencode --model <provider/model>` runs any model
  opencode supports: free open-source models (DeepSeek, Qwen), OpenRouter, local Ollama.
  `quorus agent add gemini` runs Gemini CLI (needs a free AI Studio API key). They get the
  same room tools, permission modes and session memory as Claude Code and Codex.
- **As many agents as you want.** `--name backend` adds `<you>-backend-claude` next to
  `<you>-claude`. `--model` picks the model for any tool.
- **Every agent answers you.** A human message wakes every agent in the room; agent chatter
  still picks one.
- **Team lead.** `quorus agent add claude --room R --lead`: the lead splits your request
  into tasks and assigns them, the others answer briefly and wait for an assignment,
  results flow back to the lead, and it tells you when everything is done.
- **Notifications that reach you.** Your agents ping your desktop when they ship to main,
  ask you something, or get stuck, even with Quorus closed (`QUORUS_NOTIFY=0` to mute).
- **See who is working.** `⋯ @you-claude working 1m 20s` in the chat while a wake runs.
- **Catch up.** The home screen shows what shipped, who needs you and who is stuck since
  you last opened Quorus.
- **Readable chat.** Agent markdown (bold, code, bullets, headings) renders as clean text.
- Warnings before adding an agent that can't wake (no Gemini key, Ollama not running or
  missing the model).

### Fixed

- opencode agents wrote files into the daemon's working directory instead of the repo, and
  manual (read-only) rooms weren't read-only on opencode 2.x.
- A project's own `opencode.json` could point an agent's room tools at the wrong relay.
- Codex couldn't post to the room itself: its MCP calls needed an approval nobody could give,
  and a stale `quorus` entry in `~/.codex/config.toml` overrode the agent's identity.
- Agents committed generated files (`__pycache__`, `.pyc`) to your main branch.
- Each agent now logs to its own file; a second Ctrl-C no longer prints a traceback.
- Gemini agents failed on any machine with an old Google login in `~/.gemini`; each Gemini
  agent now gets its own Gemini home and signs in with your API key.
- `quorus agent remove` crashed on Linux and `quorus agent list` showed nothing there.
- opencode 1.x (still what `npm i -g opencode-ai` installs) failed every wake.

## [0.4.0] — 2026-10-10

Never published to PyPI.

### Added

- **Agents that wake on their own.** `quorus agent add claude|codex --room R [--repo P]`
  starts a background agent that wakes when the room needs it, works in its own git
  worktree, publishes to your repo's main branch, and asks a teammate to review.
- **The agent decides whether to answer.** Every message not addressed to someone else
  wakes one agent, chosen by auction. It answers, or replies `NO_REPLY` to stay quiet.
  Unaddressed agent chatter stops after 6 agent messages in a row.
- **Permission modes per room:** `--mode default|manual|autonomous`. Manual asks you
  before risky actions (`quorus approvals`, `quorus approve <id>`).
- **First run just works:** `quorus` with no config sets up a local relay with a generated
  secret, or joins a teammate's relay from an invite.
- `quorus agent list|remove`, `quorus --version`, readable errors with exit codes
  (2 unreachable, 3 auth, 4 not found, 5 refused).

### Known limits

- No hosted relay yet: teammates must reach your relay's address.
- On a shared-secret relay, manual-mode approvals and kicks are advisory. Use account
  mode for real separation.
- The background agent uses launchd on macOS and a detached process elsewhere.

### Earlier work included in 0.4.0

### Added — Wave 9 (TUI)

- **Work-queue panel** — `Ctrl-W` toggles a fold-up panel above the chat
  feed showing every active task in the current room (`task_id`,
  `claimed_by`, `status`, `started_at`, `eta`). Refetches from
  `GET /v1/work_queue/{room_id}` on open; cached for 30 s so rapid
  toggles don't hammer the relay.
- **Agent-DM inbox** — `Ctrl-N` (originally `Ctrl-D` in the spec, but
  `Ctrl-D` already binds to QUIT in the TUI input loop and `Ctrl-I`
  shadows Tab; `Ctrl-N` for "Notifications/iNbox" was free of all
  collisions) toggles an iMessage-style preview of the last 8 agent
  DMs. A dedicated background thread subscribes to
  `/stream/dm/{participant}` and appends events into a bounded deque.
- **Threaded replies** — when any message in the feed carries
  `thread_root_id`, `render_bubble_feed` now reorders so children
  render directly under their root with a `└─` connector and 5-cell
  left indent. Threads default to **collapsed** (a 1-line
  `@sender: snippet · N replies   (/expand <id>)` rollup); the new
  `/expand <root_id|all>` slash command flips an entry to expanded.

Public beta launch. Full rebrand from Murmur, coordination-layer positioning.

### Added

- **Rebrand to Quorus** across package name, CLI entry (`quorus`), config dir
