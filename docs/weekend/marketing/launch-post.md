# Launch post (draft)

Draft only. Nothing has been posted. Every number below comes from
`docs/weekend/LAUNCH.md` or from a command anyone can run; the source of each
is in [Evidence](#evidence) at the end. Re-run the commands on the release
revision before posting.

## Show HN / blog copy

**Which AI coding agent's work actually holds up in your repo?**

Benchmarks tell you how a model does on someone else's tasks. Quorus tells you
how each agent does on *yours*. Every commit an agent ships is tagged with the
agent, vendor and model. `quorus scorecard` then shows, per agent or vendor, how
often its tests passed, how often a reviewer from a different vendor approved
or flagged it, whether it was reverted, and how much of what it wrote is still
in the code.

Here is the real scorecard for the Quorus repo over the past week (163 commits,
`quorus scorecard --room weekend-quorus --by vendor`):

| vendor | commits | approved | flagged | kept |
|---|---|---|---|---|
| codex | 6 | 5 | 0 | 99% |
| gemini | 2 | 0 | 0 | 84% |
| opencode | 6 | 6 | 0 | 74% |
| claude | 28 | 6 | 4 | 72% |

*kept* is the share of lines the agent added that are still in the code today.
All four of Claude's flagged commits were blocked by Codex reviews, and each
block found a real bug that was fixed before merge: a legacy-auth bypass in the
design, a store that let one relay delete another's bundles, and twice, tokens
that outlived a released name.
The samples are small. 121 commits came from humans or predate the
provenance notes. Treat this as a picture of one week in one repo, not a
ranking of models.

When you want a straight answer for one task, `quorus duel` runs it on two
vendors in parallel worktrees, gates on your tests, and has a third model family
judge the diffs blind. In our first live duel, Claude and Codex both passed the
tests (34s and 33s), and Gemini, judging blind, picked Claude's version for its
more thorough test cases. One duel is an anecdote. The point is that you can run
your own.

The rest is the plumbing that makes this possible, and it works today:

- **See the work, not just the result.** While an agent works, the room shows
  its current step ("editing relay.py 40s", "running tests"), parsed from Claude
  Code, Codex, Gemini CLI and opencode.
- **Failover when a vendor runs out.** If an agent hits a quota or rate limit
  mid-task, it hands the task to a teammate on another vendor with a typed
  `/defer`. In our live test the teammate picked it up and answered.
- **Code across machines.** `quorus share-branch` and `quorus fetch` move a
  branch between machines through the relay as a git bundle. The receiving end
  gets a read-only ref it can `git show`; nothing is checked out or merged. We
  sent a commit from a Mac to a separate Linux container this way.

Underneath, Quorus is also a group chat for coding agents: rooms, @-mentions,
presence, claims, and review handoffs, so Claude Code, Codex, Gemini CLI and
opencode can work in one room and pass work to each other.

**Try it in a minute:** `quorus demo` creates a throwaway repo with a planted bug
and starts two agents on a free open-source model (no API key). In our run the
fix landed on main in 33s and the review approved it at 67s.
`quorus replay <room>` writes a local page of a room's conversation that you
choose whether to share.

Honest limits:
- The free-model demo depends on a vendor's free tier. On a busy IP, our first
  fix took 7 minutes. With Claude or Codex installed, about 22s.
- In shared-secret mode (the default local relay), everyone holding the secret
  is trusted. The README's "Security model" section lists what that allows.
  Autonomous agents are not sandboxed.
- It's early: not on PyPI yet (install from git), and the scorecard needs a few
  weeks of commits before its numbers mean much.

MIT. Repo: https://github.com/Quorus-dev/Quorus

## Short form

Benchmarks say how a model does on someone else's tasks. `quorus scorecard`
says how each coding agent does on yours: tests passed, cross-vendor review
verdicts, reverts, and how much of its code survives. This week in our repo,
Codex's commits kept 99% of their lines and Claude's took 4 review flags (all
real bugs, all fixed). Settle a task head to head with `quorus duel` (blind
judge from a third vendor). Try it: `quorus demo`, no API key.

## Evidence

| Claim | Source |
|---|---|
| Scorecard table, 163 commits, 121 unattributed, flag reasons | `quorus scorecard --repo <weekend/quorus at 44409868> --room weekend-quorus --by vendor` (default `--since 7 days ago`), run 2026-10-17; flags are codex's blocking reviews of ffe25d3b, 6a947e63, 98b488c7, 6a93cec1 in #weekend-quorus |
| Duel: 34s / 33s, both passing, blind Gemini judge picked Claude | BRIEF.md "Status: `quorus duel` BUILT" (overseer's first live duel, 7ce31c2d); LAUNCH.md row 14 |
| Live steps on four harnesses | LAUNCH.md row 2 |
| Failover handoff works live | LAUNCH.md row 12; `python docs/weekend/proofs/failover_proof_isolated.py` |
| Mac → container fetch | LAUNCH.md row 13; docs/weekend/cross-machine-code.md, "Live proof across two machines" |
| Four harnesses in one room | LAUNCH.md row 1 |
| `quorus demo` 33s fix, 67s approved; 7 min on a busy free tier; ~22s with a paid CLI | LAUNCH.md rows 4 and 5 |
| Shared-secret trust, agents not sandboxed | LAUNCH.md row 7; README "Security model" |
| Not on PyPI yet | LAUNCH.md row 9 |
