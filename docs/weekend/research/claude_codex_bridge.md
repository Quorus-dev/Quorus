# Teardown: claude_codex_bridge (CCB)

Repo: <https://github.com/SeemSeam/claude_codex_bridge>
· README fetched: <https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md>
· Meta: <https://api.github.com/repos/SeemSeam/claude_codex_bridge>
· License: "Other" / NOASSERTION · Language: Python
· Stars 3,576 · created 2025-10-25 · last push 2026-10-13 · current version
8.7.11 (README badge, [L9](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L9))

CCB is a local terminal workspace (tmux) that runs many interactive coding-agent
CLIs side by side and lets them message each other. It is the closest thing in
this batch to a *broker*, but it brokers a single project on one machine, not
rooms across machines.

## What it does

- A visible multi-agent TUI over "16 CLI families" — Codex, Claude, Gemini,
  Kimi, Qwen, Cursor, Copilot, Pi, OpenCode, Droid, and more
  ([provider badge, L11](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L11)).
- Agents address each other with `/ask` and can delegate during a workflow
  ([L316](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L316)).
- A background daemon "keeps project state alive even when the foreground UI is
  closed" ([L51](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L51)).
- A project-wide shared memory file, `.ccb/ccb_memory.md`, for cross-agent
  coordination rules and handoff conventions
  ([L316](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L316),
  [L402](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L402)).
- Agent Roles as installable "Role Packs" under a host-neutral spec, with a
  public catalog (`agentroles.archi`, `agentroles.frontend_engineer`, …)
  ([L378](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L378)).
- Mobile (Android) remote control of whole projects, with an end-to-end
  encrypted relay using operator-issued one-time invitations
  ([L320](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L320),
  [L726](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L726)).

## What it does better than Quorus

1. **Draft-protected delivery into a *live* pane.** CCB's queue mode waits for
   an agent's turn to end, then checks the input box before pasting; if the human
   has a draft it waits up to **180 seconds** and never submits the human's text
   along with the message
   ([L121](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L121)).
   Quorus only injects into *headless* wakes or into Claude via hook; we have no
   safe path into a TUI where a human may be typing.
2. **Ordered agent→agent queues.** `ask` requests and `back` results share one
   chronological FIFO queue per target agent, so a later reply cannot interrupt
   an earlier turn
   ([L132](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L132)).
3. **Human takeover is one keystroke away.** Each agent is a real native
   terminal pane the human can type into or open in Rich mode
   ([L48-L49](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L48-L49)).
4. **Role packs as first-class installables** — bundle skills + memory + tool
   deps and mount/unmount per agent
   ([L378](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L378)).
   Closer to Quorus's skill distribution than anything else here.

## Where it is weaker / different

- Coordination is **per-project topology** declared in `.ccb/ccb.config`
  (`main = "main:codex"`, `work = "worker1:codex(worktree), worker2:claude(worktree)"`),
  not rooms that a second user joins
  ([L287](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L287)).
- **assumption**: the README shows no task-claiming auction, file locks, or
  hash-chained audit ledger; coordination looks like explicit `ask` calls plus a
  shared memory file, not an auction.
- **assumption**: no cross-machine, multi-user room — the mobile relay controls
  *your* projects from *your* phone, it is not a shared room between teammates.

## Takeaway for Quorus

Steal (1): delivery into a live TUI that refuses to clobber a human draft. That
is a real reliability primitive Quorus lacks, and it maps onto our "silent agent
vs. thinking vs. dead" UX problem.
