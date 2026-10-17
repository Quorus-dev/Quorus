# Teardown: claude-squad

Repo: <https://github.com/smtg-ai/claude-squad> · README:
<https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md>
· Meta: <https://api.github.com/repos/smtg-ai/claude-squad>
· Docs/site: <https://smtg-ai.github.io/claude-squad/> · License: AGPL-3.0
· Language: Go · Stars 8,615 · created 2025-03-09 · last push 2026-08-20

claude-squad is a terminal app that runs multiple coding agents in isolated git
worktrees and lets you review/apply their work. It is the purest "session
manager" of the batch — parallelism and isolation, no inter-agent messaging.

## What it does

- Manages multiple agents — Claude Code, Codex, Gemini, Aider — in separate
  workspaces "allowing you to work on multiple tasks simultaneously"
  ([README intro](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
- **Each task gets its own isolated git workspace, "so no conflicts"**
  ([Highlights](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
- Under the hood: **tmux** for isolated terminal sessions + **git worktrees** for
  isolated codebases, each on its own branch
  ([How It Works](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
- **Review before applying**: "Review changes before applying them, checkout
  changes before pushing them" — menu keys `s` (commit+push), `c` (checkout;
  commits and pauses), `r` (resume)
  ([Highlights](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md),
  [Menu](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
- Background/yolo mode: `-y, --autoyes` auto-accepts prompts for Claude Code and
  Aider ([Usage](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
- Profiles let one config hold several `name`/`program` pairs to switch programs
  ([Profiles](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
- Installed binary is `cs`; requires `tmux` and `gh`
  ([Prerequisites](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).

## What it does better than Quorus

1. **Worktree isolation is the default, not an option.** Every task is already
   on its own branch in its own worktree, so parallel agents cannot collide
   ([How It Works](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
   Quorus assigns a worktree per *session* via the harness; the tool itself does
   not guarantee isolation per task.
2. **A one-key review-before-apply flow.** Commit+push, checkout or resume are
   single keystrokes in the menu, which is a cleaner review loop than our chat
   `✅ <hash>` posts ([Menu](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
3. **Zero-ceremony parallel fan-out** — `n` creates a session, `N` creates one
   with a prompt ([Menu](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).

## Where it is weaker / different

- **No inter-agent communication.** The README never mentions agents talking to
  each other, shared state, or coordination; the model is a human juggling
  isolated parallel tasks
  ([README](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
  **assumption**: there is no claim/lock/message layer at all.
- **Single machine, single user, local TUI** — no relay, no rooms, no mobile
  ([README](https://raw.githubusercontent.com/smtg-ai/claude-squad/main/README.md)).
- Licensed **AGPL-3.0** ([API](https://api.github.com/repos/smtg-ai/claude-squad)),
  which restricts how its code could be reused.

## Takeaway for Quorus

The worktree-per-task guarantee and the review-before-apply keystrokes are the
two ideas worth mirroring. Neither requires a protocol change; both are UX around
what we already do. Quorus's counter-claim stays intact: claude-squad is
isolation without coordination, and coordination is our moat.
