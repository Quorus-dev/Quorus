# Teardown: claude-squad

Repo: <https://github.com/smtg-ai/claude-squad>
· README fetched: <https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md>
· Meta: <https://api.github.com/repos/smtg-ai/claude-squad>
· Docs/site: <https://smtg-ai.github.io/claude-squad/> · License: AGPL-3.0
· Language: Go · Stars 8,615 · created 2025-03-09 · last push 2026-08-20

claude-squad is a terminal app that runs multiple coding agents in isolated git
worktrees and lets you review/apply their work. It is the purest "session
manager" of the batch — parallelism and isolation, no inter-agent messaging.

## What it does

- Manages multiple agents — Claude Code, Codex, Gemini, Aider — in separate
  workspaces "allowing you to work on multiple tasks simultaneously"
  ([L3](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L3)).
- **Each task gets its own isolated git workspace, "so no conflicts"**
  ([L12](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L12)).
- Under the hood: **tmux** for isolated terminal sessions + **git worktrees** for
  isolated codebases, each on its own branch
  ([L152-L153](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L152-L153)).
- **Review before applying**: "Review changes before applying them, checkout
  changes before pushing them" — menu keys `s` (commit+push), `c` (checkout;
  commits and pauses), `r` (resume)
  ([L11](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L11),
  [L103-L105](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L103-L105)).
- Background/yolo mode: `-y, --autoyes` auto-accepts prompts for Claude Code and
  Aider ([L67](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L67)).
- Profiles let one config hold several `name`/`program` pairs to switch programs
  ([L117-L139](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L117-L139)).
- Installed binary is `cs`; requires `tmux` and `gh`
  ([L22](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L22),
  [L49-L50](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L49-L50)).

## What it does better than Quorus

1. **Worktree isolation is the default, not an option.** Every task is already
   on its own branch in its own worktree, so parallel agents cannot collide
   ([L12](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L12),
   [L153](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L153)).
   Quorus assigns a worktree per *session* via the harness; the tool itself does
   not guarantee isolation per task.
2. **A one-key review-before-apply flow.** Commit+push, checkout or resume are
   single keystrokes in the menu, which is a cleaner review loop than our chat
   `✅ <hash>` posts
   ([L103-L105](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L103-L105)).
3. **Zero-ceremony parallel fan-out** — `n` creates a session, `N` creates one
   with a prompt
   ([L95-L96](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L95-L96)).

## Where it is weaker / different

- **No inter-agent communication.** The README never mentions agents talking to
  each other, shared state, or coordination; the model is a human juggling
  isolated parallel tasks
  ([L3](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L3),
  [L91-L111](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L91-L111)).
  **assumption**: there is no claim/lock/message layer at all.
- **Single machine, single user, local TUI** — no relay, no rooms, no mobile
  ([L3](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L3),
  [L47-L50](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L47-L50)).
- Licensed **AGPL-3.0**
  ([API](https://api.github.com/repos/smtg-ai/claude-squad),
  [L158](https://github.com/smtg-ai/claude-squad/blob/main/README.md?plain=1#L158)),
  which restricts how its code could be reused.

## Takeaway for Quorus

The worktree-per-task guarantee and the review-before-apply keystrokes are the
two ideas worth mirroring. Neither requires a protocol change; both are UX around
what we already do. Quorus's counter-claim stays intact: claude-squad is
isolation without coordination, and coordination is our moat.
