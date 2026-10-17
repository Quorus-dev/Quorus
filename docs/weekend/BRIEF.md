# Weekend brief: make Quorus better, using Quorus (2026-10-17 → 10-19)

Arav is away. Claude (the overseer, posting as `arav` and signing "— overseer")
runs the room and checks in about every 90 minutes. Everything lands on the
`weekend/quorus` branch; Arav reviews and merges what's good on Monday.

## Team

- `@arav-claude` (Claude Code), **lead**: splits work, assigns it, reviews, reports.
- `@arav-codex` (Codex): builder and reviewer.
- `@arav-gemini` (Gemini CLI): researcher and reviewer.
- `@arav-opencode` (opencode, free DeepSeek): builder and second opinion.
- `@aarya-sim-opencode`: an agent on a second "laptop" (a Docker container),
  owned by a second simulated user. It shares this room but not your disk,
  so paste diffs into the chat when it reviews.

## Goal

Make Quorus the best way to run a team of coding agents: more reliable, clearer
to use, more useful, more novel. Be honest: if an idea already exists, say who has
it and score its novelty from 1 to 10.

## Work, in priority order

1. **Bugs.** Run the suite, try real flows, and fix what breaks. Every fix gets a
   regression test.
2. **UX that removes confusion.** Examples: a user can't tell whether a silent
   agent is thinking, chose not to reply, or is dead. First-run steps, error
   messages, the chat layout.
3. **Research.** Write teardowns of Paperclip, claude_codex_bridge, codeg,
   Claude-Code-Workflow, Vibe Kanban, claude-squad and Agent Mail: what each one
   does better than we do, and what nobody does. Put them in `docs/weekend/research/`.
4. **Ideas.** Add each to `docs/weekend/ideas.md` with prior art, a novelty score,
   the user pain it fixes, and its cost. The lead picks the top one or two to build.
5. **Marketing drafts.** Put launch posts, a list of communities and people worth
   reaching, and demo scripts in `docs/weekend/marketing/`. Drafts only.

## Rules (non-negotiable)

- Never push, publish, post, email, or contact anyone outside this room. A hook
  blocks pushes anyway; don't try to get around it.
- Work only in your own worktree. Never touch `main`, `~/.quorus`, other repos,
  or anyone's secrets.
- Run the tests before every commit:
  `PYTHONPATH=.:packages/cli:packages/mcp:packages/sdk:packages/tui /Users/aravkekane/dev/Quorus/.venv/bin/python -m pytest -q`
  and `/Users/aravkekane/dev/Quorus/.venv/bin/ruff check .`
- Make small conventional commits (`fix:`, `feat:`, `docs:`, `test:`), one change
  each, with subjects under 50 characters.
- Builders: when an assigned task is done, @-mention the lead with the commit and
  the test result. Lead: review it, then post a summary for `arav` when a batch
  is done.
- If you're blocked, say exactly what's blocking you. Don't guess.
