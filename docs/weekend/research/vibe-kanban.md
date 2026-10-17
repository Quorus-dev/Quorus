# Teardown: Vibe Kanban

Repo: <https://github.com/BloopAI/vibe-kanban>
· README fetched: <https://raw.githubusercontent.com/BloopAI/vibe-kanban/main/README.md>
· Meta: <https://api.github.com/repos/BloopAI/vibe-kanban> · License: Apache-2.0
· Language: Rust · Stars 28,378 (largest in this batch) · created 2025-06-14
· last push 2026-09-19

Vibe Kanban is a kanban board for planning coding-agent work, with a per-task
workspace for execution and a diff/preview surface for review. The company
behind it shut down in April 2026; the project continues open source, going
fully local.

## Status first

- The README carries a sunsetting banner: *"Vibe Kanban is sunsetting. Read the
  announcement."* ([L19-L20](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L19-L20)).
- Company shutdown post: *"Today we're shutting down bloop, the company behind
  Vibe Kanban. The Vibe Kanban project will live on, open source and community
  maintained."* — Louis Knight-Webb, 2026-04-10
  ([shutdown post](https://www.vibekanban.com/blog/shutdown)).
- Remote services (kanban issues, comments, projects, organisations) will be
  removed ~30 days after that post; "Vibe Kanban will transition to a fully local
  architecture" ([shutdown post](https://www.vibekanban.com/blog/shutdown)).
- Reason given: "the vast majority are free users and we couldn't find a business
  model" ([shutdown post](https://www.vibekanban.com/blog/shutdown)) — the
  monetization graveyard the MVP research already flagged.

## What it does

- **Plan on a kanban board** — create/prioritise/assign issues, privately or with
  a team ([L31](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L31)).
- **A workspace per task** — "each workspace gives an agent a branch, a terminal,
  and a dev server"
  ([L32](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L32)).
- **Review diffs with inline comments** that go "directly to the agent without
  leaving the UI"
  ([L33](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L33)).
- **Built-in browser preview** with devtools, inspect mode and device emulation
  ([L34](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L34)).
- **Switch between 10+ coding agents**: Claude Code, Codex, Gemini CLI, GitHub
  Copilot, Amp, Cursor, OpenCode, Droid, CCR, Qwen Code
  ([L35](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L35)).
- **Create PRs and merge** with AI-generated descriptions
  ([L36](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L36)).
- One-command start (`npx vibe-kanban`) and a self-hosting guide
  ([L43](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L43),
  [L61](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L61)).

## What it does better than Quorus

1. **The review surface.** Inline comments on a diff, routed back to the agent,
   is a genuine workflow primitive. Quorus's chat has no diff view and no way to
   comment on a specific changed line
   ([L33](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L33)).
2. **The task as the unit, with its own workspace/branch/dev-server.** Our rooms
   and tasks are chat-centric; a task here *is* an isolated environment
   ([L32](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L32)).
3. **Live app preview next to the agent** so a human can eyeball the running
   result ([L34](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L34)).

## Where it is weaker / different

- **No agent-to-agent communication.** The README describes a human planning and
  reviewing agents; agents do not message *each other* or share state
  ([L27-L36](https://github.com/BloopAI/vibe-kanban/blob/main/README.md?plain=1#L27-L36)).
  **assumption**: no rooms/relay — it is a board + workspaces for one user (or
  one team on a hosted board that is now being removed).
- The hosted/team layer is going away, leaving a local app
  ([shutdown post](https://www.vibekanban.com/blog/shutdown)).
- **assumption**: no auction/claims/locks between agents; parallelism is
  workspace isolation, not coordination.

## Takeaway for Quorus

Quorus's biggest visible gap against a 28k-star tool is *review*, not wake. A
minimal version — surface a claimed task's `git diff` in the room and let a human
or reviewer agent comment on lines — would be a small, high-signal feature. Note
the graveyard warning: stars without revenue is exactly how this one died
([shutdown post](https://www.vibekanban.com/blog/shutdown)).
