# Teardown: Claude-Code-Workflow (CCW)

Repo: <https://github.com/catlog22/Claude-Code-Workflow>
· README fetched: <https://raw.githubusercontent.com/catlog22/Claude-Code-Workflow/main/README.md>
· Meta: <https://api.github.com/repos/catlog22/Claude-Code-Workflow>
· License: MIT · Language: TypeScript · Stars 2,123 · created 2025-09-07
· last push 2026-06-18 · **archived: true** (API)

CCW is a JSON-driven, skill-based workflow framework for Claude Code that
orchestrates several CLIs (Gemini/Codex/Qwen/Claude) through an event-driven
coordinator/worker model. **It is archived**: the README redirects active
development to Maestro Flow.

## Status first (matters for the decision)

The README's top banner says: *"Claude Code Workflow is now archived. All active
development continues at https://github.com/catlog22/maestro-flow"*, and the
GitHub API returns `"archived": true`, `"open_issues_count": 0`
([banner, L4](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L4),
[API](https://api.github.com/repos/catlog22/Claude-Code-Workflow)). Treat the
teardown below as prior art, not a live competitor. **assumption**: Maestro Flow
carries these features forward unchanged; we did not audit it in this pass.

## What it does

- **Semantic CLI orchestration**: the *prompt* chooses the tool — "Use Gemini to
  analyze the auth module" auto-invokes `gemini`, "Let Codex review this code"
  invokes `codex`, and it supports collaborative/parallel/iterative/pipeline
  patterns across Gemini, Codex, Qwen, Claude
  ([L175](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L175),
  [L192-L194](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L192-L194)).
- **Skill-based workflows**: `workflow-lite-plan`, `workflow-plan`,
  `workflow-tdd-plan`, `brainstorm`, etc., plus slash commands for session
  lifecycle (`/workflow/session:start|resume|complete`)
  ([L353](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L353)).
- **Team Architecture v2** — role-based `team-worker` agents driven by an
  event-driven **"beat model"**: the coordinator only wakes on a callback/resume,
  finds ready tasks, spawns workers (parallel allowed), and stops when idle
  ([beat-model section,
  L374](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L374),
  [L398](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L398)).
- **Queue scheduler** background service, a multi-terminal dashboard, a visual
  orchestrator editor, and **A2UI** (Agent-to-User interactive interface)
  ([L69](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L69),
  [L84](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L84)).
- Storage of skills: 22 agents and 37 modular skills under `.claude/`
  ([L345](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L345),
  [L353](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L353)).

## What it does better than Quorus

1. **The prompt picks the harness.** Quorus makes the *human* `@`-mention an
   agent; CCW lets a natural-language request route to the fittest CLI
   automatically ([L192-L194](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L192-L194)).
   A "delegate this to whichever agent fits" primitive is a plausible UX win.
2. **A coordinator that sleeps until a callback.** The beat model is close to
   Quorus's triage/bid flow but framed as something that costs nothing while
   idle ([L398](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L398)).
3. **Skill packs organized as plan → execute → test-fix workflows** rather than
   one flat skills dir — relevant to how we present QOD to new users
   ([L353](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L353)).

## Where it is weaker / different

- **Archived**; no open issues, last push 2026-06-18
  ([API](https://api.github.com/repos/catlog22/Claude-Code-Workflow),
  [L4](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L4)).
- It is a **local, single-user** framework layered on Claude Code skills; no
  relay, no rooms, no multi-machine coordination
  ([README](https://github.com/catlog22/Claude-Code-Workflow)).
- It requires **Codex feature flags** (`multi_agent`, `multi_agent_v2`,
  `enable_fanout`, `default_mode_request_user_input`) in `~/.codex/config.toml`
  for its `.codex/skills` — a per-vendor config burden
  ([L112-L114](https://github.com/catlog22/Claude-Code-Workflow/blob/main/README.md?plain=1#L112-L114)).

## Takeaway for Quorus

Prior art for "let the request route to the best agent" and for a lazy
coordinator. Both are cheap to demo on top of what we already have. Nothing to
copy verbatim; note it as background in our novelty scoring.
