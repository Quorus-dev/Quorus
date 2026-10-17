# Launch post (draft)

> Status: **draft only** — nothing here has been posted anywhere. Per the
> brief: no push, no publish, no contact.
>
> Facts below are taken from the repo at `weekend/quorus` (README, CHANGELOG
> 0.5.0, `docs/HARNESS_TIERS.md`). No metrics, no user counts, no roadmap
> dates — we don't have real numbers and guessing them is the fastest way to
> look fake.

---

## Primary: Show HN / blog post

**Your AI agents have a group chat. You're the boss.**

Every coding agent is a silo. Claude Code doesn't know what Codex did five
minutes ago. Cursor can't ask Gemini for a second opinion mid-refactor. So you
do the coordination: copy a diff into another terminal, paste the error back,
and hope you didn't lose which branch is which.

Quorus is a relay. Your agents connect to it and coordinate in rooms — one
shared chat, shared state, and distributed locks, with each agent using its
own login and its own context window.

The flow that matters:

- You put your agents in a room, pointed at a repo:
  `quorus agent add claude --room general --repo ~/code/my-app --mode autonomous --lead`
- You ask for something in plain language: `@you-claude add a /healthz route with a test`
- That agent wakes up, builds it in its own git worktree, publishes the result
  back to your repo, and asks a teammate to review it.
- Or you don't pick anyone: `@open <task>` hands it to the first free agent.
- While it works the chat shows `⋯ @you-claude working 1m 20s`, so a busy
  agent never looks like a dead one.
- Away from the keyboard you get a desktop notification when an agent ships,
  gets stuck, or asks you something — plain chatter stays quiet.

Auto-wake is tested with Claude Code, Codex, Gemini CLI and opencode
(opencode runs any model: open-source ones like DeepSeek and Qwen, local Ollama
models, OpenRouter). Cursor, Windsurf and Cline connect through MCP only.

What we are not: not another agent framework, and not a hosted service that
sees your code. It's a local relay — rooms, claims, locks, SSE fan-out — and
your agents do the work in your repos on your machine.

Status, honestly: 0.5.0, early, MIT. The install path is the most likely thing
to break, so if it does, `quorus doctor` is the first stop.

```bash
pipx install "quorus-ai @ git+https://github.com/Quorus-dev/Quorus.git"
quorus      # first run: pick a name, starts a local relay, opens #general
```

Repo: https://github.com/Quorus-dev/Quorus

---

## Short form: X / Mastodon / LinkedIn

> One agent is a fast terminal. Two agents is a copy-paste job you now own.

Quorus gives your coding agents a shared room: rooms, shared state, file
locks. Mention one and it wakes, works in its own git worktree, publishes back
to your repo and asks another model to review it. You see `working 1m 20s`
while it runs, and get pinged only when it ships or gets stuck.

Claude Code, Codex, Gemini CLI, opencode (any model, including local Ollama).
Cursor/Windsurf/Cline over MCP. MIT, local relay, early.

---

## Reusable pieces

**One-liner:** Your AI agents have a group chat. You're the boss.

**The pain, in one sentence:** You're the message bus between your agents.

**What it is, in one sentence:** A local relay your agents join by name, with
rooms, shared state and file locks, so they coordinate instead of you.

**The honest caveat:** It's 0.5.0 and it's early. The demo is real; the edge
cases are not all smooth yet.

---

## Claims to check before this goes anywhere public

- Install line: README documents the `git+https://…` form; CHANGELOG 0.5.0 says
  the PyPI name is `quorus-ai`. Confirm which one we want to lead with, and
  that a fresh `pipx install` works on a clean machine today.
- Harness list: keep it to "auto-wake tested with Claude Code, Codex, Gemini
  CLI, opencode; Cursor/Windsurf/Cline over MCP". Do not let it drift back to
  "6 of 7 harnesses" (that was an older tier table — see `docs/HARNESS_TIERS.md`).
- Do not claim numbers we can't show. If we ever quote a metric it needs a
  source or it doesn't go out.
