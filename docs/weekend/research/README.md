# Research teardowns — agent orchestrators, 2026-10-17

Redo of the five projects the lead assigned: `claude_codex_bridge`, `codeg`,
`Claude-Code-Workflow`, Vibe Kanban, `claude-squad`. Paperclip and Agent Mail are
`@arav-gemini`'s (see the room transcript / its branch).

**How to check this pass.** Each claim links to a specific line of the project's
README on GitHub (`.../blob/main/README.md?plain=1#L<line>`), or to the GitHub
API object it was read from. Everything was fetched with `webfetch` on
2026-10-17. Repo metadata (stars / language / license / archived / pushed_at)
comes from `https://api.github.com/repos/<owner>/<repo>` for each project.
Anything inferred rather than read is tagged **assumption**.

Scope note: this pass covers what each project *is and does*. It does **not**
repeat the notifications/competition audit in
`docs/NOTIFICATION_MVP_RESEARCH.md`, which already scored the category.

## Snapshot

| Project | Repo | Stars | Lang / license | Last push | Status |
|---|---|---|---|---|---|
| claude_codex_bridge (CCB) | [SeemSeam/claude_codex_bridge](https://github.com/SeemSeam/claude_codex_bridge) | 3,576 | Python / NOASSERTION | 2026-10-13 | Active (v8.7.11) |
| Codeg | [spacering-net/codeg](https://github.com/spacering-net/codeg) | 3,987 | Rust / Apache-2.0 | 2026-10-16 | Active |
| Claude-Code-Workflow (CCW) | [catlog22/Claude-Code-Workflow](https://github.com/catlog22/Claude-Code-Workflow) | 2,123 | TypeScript / MIT | 2026-06-18 | **Archived** → Maestro Flow |
| Vibe Kanban | [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban) | 28,378 | Rust / Apache-2.0 | 2026-09-19 | **Company shut down**; OSS |
| claude-squad | [smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad) | 8,615 | Go / AGPL-3.0 | 2026-08-20 | Active |

Star/language/license/last-push all read from each repo's
`https://api.github.com/repos/<owner>/<repo>` on 2026-10-17.

## What each one does better than us

- **CCB** — visible live TUI panes with human takeover, and typed agent→agent
  `ask`/`back` queues with draft protection. (details in `claude_codex_bridge.md`)
- **Codeg** — one structured (non-terminal) surface over 17 agents via ACP, and
  cross-agent session portability (`@`-mention another agent's session).
  (details in `codeg.md`)
- **CCW** — semantic multi-CLI routing ("use Gemini to analyze X" auto-picks the
  CLI) plus a coordinator/worker "beat" loop. (details in `claude-code-workflow.md`)
- **Vibe Kanban** — the human review surface: per-task workspace, inline diff
  comments sent back to the agent, live app preview. (details in `vibe-kanban.md`)
- **claude-squad** — an isolated git worktree + tmux session per task, with a
  one-key review-before-apply flow. (details in `claude-squad.md`)

## What nobody does (our wedge, restated against this batch)

Every project here is **single-user / single-machine** at its coordination layer.
None ships a neutral relay that a *different* user's machine joins, none has
rooms with durable cross-session memory, and none has cross-vendor
claiming/locks/audit between heterogeneous agents. That is the same gap
`docs/NOTIFICATION_MVP_RESEARCH.md` §5 named, and this batch doesn't close it:

- CCB, Codeg and claude-squad run many agents on one host for one human
  ([CCB L47-L53](https://github.com/SeemSeam/claude_codex_bridge/blob/main/README.md?plain=1#L47-L53),
  [Codeg L187-L189](https://github.com/spacering-net/codeg/blob/main/README.md?plain=1#L187-L189),
  [claude-squad L3](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L3)).
- Vibe Kanban's remote/SaaS layer is being removed — it is going "fully local"
  ([shutdown post](https://www.vibekanban.com/blog/shutdown)).
- CCW is archived
  ([README banner, L4](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L4)).

The two ideas below are the genuinely new ones worth stealing, both about the
*human's* view rather than the relay:

1. **CCB's draft-protected delivery into a live pane** — nobody in the relay
   space delivers a message into a running TUI without clobbering what the human
   is typing. **assumption**: we have not seen this in any relay-style tool.
2. **Vibe Kanban's inline diff comments as agent feedback** — review lives on
   the diff, not in chat. Quorus has no review surface at all today.
