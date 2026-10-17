# Teardown: Codeg

Repo: <https://github.com/spacering-net/codeg>
· README fetched: <https://raw.githubusercontent.com/spacering-net/codeg/main/README.md>
· Meta: <https://api.github.com/repos/spacering-net/codeg>
· Docs: <https://docs.codeg.app> · License: Apache-2.0 · Language: Rust
· Stars 3,987 · created 2026-02-09 · last push 2026-10-16

Codeg is "the multi-agent coding workspace": a desktop/server/mobile app that
runs 17 agents behind one *structured* (non-terminal) UI and lets them hand work
to each other. Newest and fastest-moving of the batch.

## What it does

- Speaks the **Agent Client Protocol (ACP)** to every supported agent, so all 17
  render as the same structured conversation with tool cards, live diffs, plans
  and permission prompts — explicitly "a structured conversation, not a
  terminal" ([L34](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L34),
  ["Seventeen come built in",
  L270](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L270)).
  Any ACP agent from the public registry can be added ([L270](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L270)).
- **Session portability**: imports, searches and resumes the history each agent
  CLI keeps on disk, and `@`-mentions an old session so an agent can read one a
  *different* agent wrote
  ([L119](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L119),
  [L121](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L121)).
- **Multi-agent collaboration by `@`**: each mentioned agent runs as its own
  session, side by side, streaming back into the thread — "Claude Code drafting
  while Codex reviews"
  ([L136](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L136)).
- **Unattended to-dos**: each to-do gets its own git worktree and branch, starts
  now or on a schedule, then waits for review; accept the diff and the agent
  lands it
  ([L153-L155](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L153-L155)).
  The repository panel adds them from issues/PRs by label and author, on a
  schedule or a webhook ([L155](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L155)).
- Runs as desktop app, self-hosted server ("any browser"), single Docker
  container, or native iPhone/iPad/Android clients, plus Telegram/Lark/WeChat
  channels
  ([L187-L189](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L187-L189)).

## What it does better than Quorus

1. **Cross-agent session portability.** Reading a session one vendor wrote from
   inside another vendor's session is genuinely novel in this batch, and it is
   the same "shared memory that survives across harnesses" problem Quorus solves
   with rooms — Codeg solves it for one user's local history
   ([L121](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L121)).
2. **One structured renderer for N agents.** Because it goes through ACP, adding
   an agent is a protocol adapter, not an N×M integration — the same architectural
   bet Quorus makes with MCP
   ([L270](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L270)).
3. **Scheduled, unattended work with review-gated landing.** The to-do queue is a
   real work queue with human accept before merge; Quorus's work queue has no
   "start on a cron" or issue/PR ingestion
   ([L153-L155](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L153-L155)).
4. **A mobile + server deployment story that actually shipped** — native
   iPhone, iPad and Android clients plus a Docker/server mode, not just a daemon
   ([L189](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L189)).

## Where it is weaker / different

- The server mode is a **single-host, token-authenticated** service you open in a
  browser; the human is one operator, not a team of separate machines
  ([L189](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L189),
  [L335](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L335)).
- **assumption**: no cross-machine relay between two users, no rooms, no
  claiming/locks/audit between heterogeneous agents. The README frames everything
  as "your machine", "your agents".
- Privacy is explicitly local-first: "parsing, storage, and project operations
  stay on your machine, and web and server modes are guarded by token
  authentication"
  ([L335](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L335)).
  Fine for one user, not a coordination layer.

## Takeaway for Quorus

The session-import idea is worth watching: if agents can already resume each
other's on-disk sessions, "room memory readable by a Claude today and a Codex
tomorrow" gets easier to build and easier to demo. Steal the concrete framing
("`@`-mention a session") for our shared-memory pitch, not the code.
