# Teardown: claude_codex_bridge (CCB)

Repo: <https://github.com/SeemSeam/claude_codex_bridge> · README:
<https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md>
· Meta: <https://api.github.com/repos/SeemSeam/claude_codex_bridge>
· License: "Other" / NOASSERTION (API) · Language: Python (API)
· Stars 3,576 · created 2025-10-25 · last push 2026-10-13 · current version
8.7.11 ([README badge](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md))

CCB is a local terminal workspace (tmux) that runs many interactive coding-agent
CLIs side by side and lets them message each other. It is the closest thing in
this batch to a *broker*, but it brokers a single project on one machine, not
rooms across machines.

## What it does

- A visible multi-agent TUI over "16 CLI families" — Codex, Claude, Gemini,
  Kimi, Qwen, Cursor, Copilot, Pi, OpenCode, Droid, and more
  ([README badges/overview](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
- Agents address each other with `/ask` and can delegate during a workflow; the
  reply comes back as a typed result ([Quick Start → Collaborate](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
- A background daemon "keeps project state alive even when the foreground UI is
  closed" ([Why CCB](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
- A project-wide shared memory file, `.ccb/ccb_memory.md`, for cross-agent
  coordination rules and handoff conventions
  ([Config And Shared Memory](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
- Agent Roles as installable "Role Packs" under a host-neutral spec, with a
  public catalog (`agentroles.archi`, `agentroles.frontend_engineer`, …)
  ([Agent Roles Spec](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
- Mobile (Android) remote control of whole projects, with an end-to-end
  encrypted relay using operator-issued one-time invitations
  ([Mobile Remote Control](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).

## What it does better than Quorus

1. **Draft-protected delivery into a *live* pane.** CCB's "Humans and Agents
   Share the Queue" mode waits for an agent's turn to end, then checks the input
   box before pasting; if the human has a draft it waits up to 180 seconds and
   never submits the human's text along with the message
   ([README §8.7.0](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
   Quorus only injects into *headless* wakes or into Claude via hook; we have no
   safe path into a TUI where a human may be typing.
2. **Ordered agent→agent queues.** `ask` requests and `back` results share one
   chronological FIFO queue per target agent, so a later reply cannot interrupt
   an earlier turn ([README §Agent/agent queue mode](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
3. **Human takeover is one keystroke away.** Each agent is a real native
   terminal pane; the human can type directly into it or open it in Rich mode
   ([Why CCB](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
4. **Role packs as first-class installables** — bundle skills + memory + tool
   deps and mount/unmount per agent
   ([Agent Roles Spec](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
   Closer to Quorus's skill distribution than anything else here.

## Where it is weaker / different

- Coordination is **per-project topology** declared in `.ccb/ccb.config`
  (`main = codex`, `work = worker1, worker2`), not rooms that a second user
  joins ([Configure The Workspace](https://raw.githubusercontent.com/SeemSeam/claude_codex_bridge/main/README.md)).
- **assumption**: the README shows no task-claiming auction, file locks, or
  hash-chained audit ledger; coordination looks like explicit `ask` calls plus a
  shared memory file, not an auction.
- **assumption**: no cross-machine, multi-user room — the mobile relay controls
  *your* projects from *your* phone, it is not a shared room between teammates.

## Takeaway for Quorus

Steal (1): delivery into a live TUI that refuses to clobber a human draft. That
is a real reliability primitive Quorus lacks, and it maps onto our "silent agent
vs. thinking vs. dead" UX problem.
