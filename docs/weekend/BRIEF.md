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

## Saturday: the usefulness bar (from Arav, 2026-10-17)

Only build things a real user would notice and use. Every task must name:
1. **The pain:** a real moment it fixes, ideally one this weekend actually hit. Examples: the
   installer injection, an agent's own `quorus init` breaking its identity, quota storms, "is that
   agent dead or thinking?", Arav unable to see the rooms while away.
2. **The proof:** a regression test plus a live run in a real room, or it isn't done.
3. **The effect:** what the user sees now, in the TUI, the web page or the CLI, written in one line.

No speculative refactors, no docs that just restate code, no idea without prior art and an honest score.

### Headline feature for Saturday: your agent team on your phone
Arav spent this weekend away from the laptop with no way to watch or steer the rooms. That pain is
real and it's ours. The relay already serves a web chat at `/`.
- Make it work well on a phone: readable bubbles, "working…" status, presence, a send box, and
  approve/deny for manual rooms.
- Add `quorus phone`: print a URL and a QR code for that page. The default must be safe: local
  network or Tailscale only, never the open internet without explicit opt-in, and auth must never
  leak into logs. Any internet tunnel needs a written threat model and a review by a second model.
- Prior art: Claude Code Remote Control, the Codex app, Happy, Omnara. Say what's different: one
  room for every model and every person.

### The full Saturday slate (each item names its pain, prior art and proof)
1. **Phone companion** (above).
2. **Stay inside my plan.** Last night agents burned Codex's limit in ~3h, opencode's free tier in
   ~2h and Gemini's free tier in minutes. Add per-room pacing: daily wake caps per agent, cheap
   models for chatter and strong ones for builds, and a `quorus usage` view. Prior art: Harvey
   budgets against your subscription; Paperclip has budgets.
3. **See what an agent is doing, live.** The "working 1m 20s" line should show the step ("editing
   reflexd.py", "running tests") parsed from the harness JSON streams (claude stream-json, codex
   --json, opencode --format json). It fixes "can I see the work or just trust it?".
4. **Code across machines.** Laptop 2 couldn't `git show` laptop 1's commits and had to paste
   diffs. Design and build a way for a teammate's agent to fetch a branch through the relay (git
   bundles or a shared remote), with size limits and auth.
5. **First value in under 2 minutes.** `quorus` detects the installed CLIs (claude/codex/gemini/
   opencode), offers to add each with one keypress, and `quorus doctor` checks logins and quota.
   Measure it with a fresh-HOME run.
6. **Failover, proven live.** Kill or rate-limit a working agent mid-task in a real room and show
   the handoff completing. Fix whatever breaks.
7. **A replay people share (the viral loop).** `quorus replay <room> --since 1h --html` writes a
   local, self-contained page of the agents' conversation, commits and tests for one task. Nothing
   gets uploaded; the user chooses to share it. Prior art: Loom, asciinema.
8. **Launch readiness:** `docs/weekend/LAUNCH.md`, a go/no-go checklist with evidence for each line.

### Saturday PRIORITY ORDER (overrides the slate order above)
Read `docs/weekend/research/landscape.md` first. Its verdict: "group chat for coding agents" alone is
~4/10 novelty (Paperclip ~103k stars, codeg, Oryxa). The opening nobody fills: **measure which
vendor's work actually survives in THIS repo, and learn from it.**
1. **Outcome ledger + repo scorecard.** Tag every task and commit with agent, vendor and owner. Track
   tests, cross-vendor review verdicts, reverts, and whether the commit is still there after 7 and
   14 days. Show it with `quorus scorecard` (which agent and vendor is best at what, here). Everything
   else builds on this.
2. **`quorus duel <task>`.** The same task goes to 2 vendors in parallel worktrees. Tests are the
   gate, and the judge is blind and from a different model family than each candidate. The winner
   merges and the result feeds the ledger. Run it only on risky tasks, because quota is tight.
3. **Phone companion** (Arav's real weekend pain).
4. Live step visibility, then code across machines, then first value in under 2 minutes.
Codex is paced at 3 turns/hour until Sunday 23:59, so give it the reviews and the hard parts.

### Added: `quorus demo` (zero-key trial), rank 2b after the duel
Pain: trying Quorus today needs Claude or Codex accounts, so most visitors bounce before they see the
magic. `quorus demo` creates a throwaway room and a sample repo with a planted bug, starts two agents on a
free open-source model (opencode free tier, no API key), posts one message, and lets the visitor watch
the fix → review → main in under 2 minutes, then cleans up. Proof: a fresh-HOME run on Linux and macOS
that ends with the fix on the sample repo's main. Handle the free tier's rate limit gracefully (the
quota cooldown already exists). Pre-launch security audit is running separately; don't duplicate it.

### From the pre-launch security audit (fixes are on feat/wake-rebuild-aug26: 9d0fc0e5, 430cb716, c5740e80, b8473f6c, 51cf8f59)
- **Phone companion prerequisite:** the `/` web chat (`quorus/dashboard.py`) does not run at all,
  because the relay's own CSP (`script-src 'self'`) blocks its inline script. Serve the script as a
  same-origin static file; do not loosen the CSP. Fix the latent XSS before it runs: `esc()` doesn't
  escape quotes, and `location.hash` is concatenated into fetch paths. Add tests that render hostile
  chat content.
- Owner-only write turns are now the default (others get read-only answers; `--trust members` opts
  a shared room in). Respect it in new features.

### How the weekend runs: a continuous loop with a stop rule (Arav, 2026-10-17)
Keep going while it's genuinely impactful: build → verify (tests + a live room run) → measure → pick the
next highest-value item → repeat. The overseer measures each room every ~2h (`impact.py`: code with
tests, plus accepted research with sources, versus churn: docs that restate, rejected work, merge
noise). **Two thin windows in a row → the room rests** and the overseer says why. Resting isn't
failure; producing churn is.

### Added: `quorus retro`, the self-improvement loop
Evidence: most of this weekend's product fixes came from the overseer reading room transcripts and
daemon logs for failures: double wakes, silence spam, quota storms, an agent breaking its own
identity, wrong time zones, a wrong notice wording. `quorus retro <room> --since 6h` hands that job to
an agent. It mines the room's transcript, status lines and the agents' logs for failure patterns and
posts a ranked list (symptom, evidence, likely cause, proposed fix and test). With `--file`, it adds
them as open tasks. Prior art: Devin's knowledge suggestions, incident post-mortems. What's different:
it runs on a multi-vendor team's own conversation. Proof: run it on this weekend's rooms and check it
rediscovers at least 5 of the issues the overseer fixed by hand.

### Roles from Saturday (overseer's call, per Arav)
- **Overseer (Claude, posting as arav):** builds the outcome ledger and `quorus scorecard` on
  feat/wake-rebuild-aug26. Reviews and verifies everything else before it counts.
- **@arav-claude (lead):** coordinates, builds `quorus duel`, and reviews every builder's work hard.
- **@arav-codex (paced at 3/h):** security-sensitive reviews, the dashboard XSS/CSP fix, and hard bugs.
  Spend its turns on what only it can do.
- **@arav-opencode / @aarya-sim-opencode (DeepSeek):** bounded tasks with a clear test to pass: the
  `quorus demo` sample repo and script, the phone page layout, fresh-user and second-machine runs.
- **@arav-gemini:** easy, checkable tasks: link checks, doc fixes, issue triage. No unsourced research.
