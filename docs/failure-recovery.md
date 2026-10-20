# How agents recover from failure

What Quorus 0.6.3 does when an agent's turn goes wrong. Every statement below cites the
code at the `v0.6.3` tag; nothing here describes planned behaviour. The wake daemon is
`scripts/reflexd.py` (shipped as `quorus/_reflexd/reflexd.py`), with helpers next to it.

## A failed or stale session is retried once, fresh

Each wake runs the agent's harness (Claude Code, Codex, opencode, Gemini) as a
subprocess, usually resuming the agent's previous session. If a resumed session fails,
the daemon retries once without the session id:

- Claude Code: `ClaudeAdapter` in `scripts/reflexd.py` re-runs `claude --print` with no
  `--resume` when the resumed run returns `[reflexd] harness errored` ("claude resume
  failed for session … — retrying fresh").
- Codex: the same pattern for `codex exec resume` ("codex resume failed for thread …").
- opencode: one fresh retry after a 1–3 s jitter when the run failed or a resume produced
  no reply, which also covers two agents racing opencode's first-run setup.

There is exactly one retry. A second failure is reported, not hidden (next section).

## Failures are diagnosed and said out loud, never swallowed

`diagnose_harness_failure()` in `scripts/reflexd.py` maps a harness's error output to a
room message (`⚠ <harness>: <cause>`) from a fixed table of known causes; unrecognised
output keeps the generic sentinel rather than inventing a cause. Its input is the
harness's stderr (`_run_subprocess`) and, for Claude Code, the explicit error fields of a
`stream-json` result envelope flagged `is_error: true` (`parse_claude_stream()`), which can
carry an API error even at exit status 0. Quota, usage-limit, rate-limit and login
failures are typed as `VendorFailure` (`quorus/runtime/failover.py`) so the daemon can act
on them. The model's own prose is never an input, so an answer that talks about quota
cannot trigger recovery.

A mission turn that hits its time limit (`REFLEXD_MISSION_TIMEOUT_S`, default 3600 s;
chat turns are capped at `REFLEXD_CHAT_MAX_TURNS`, default 60) posts: "still holding the
claim on '<task>' but my last work session hit the Nm limit. Mention me to continue, send
/interrupt to stop me, or release the task." Other failures end with "mention me to
retry". Owners are alerted through `Reflexd._alert_owner()`.

## Vendor quota: pause, say so once, resume on the vendor's clock

When stderr says the vendor's quota or rate limit is out, `_retry_after_s()` parses the
vendor's own "retry in 21h39m46s" or "try again at 1:46 AM" into a wait (default 30 min,
cap 24 h) and the adapter sets `limited_until`. While that holds, `Reflexd._limited()`
skips wakes and posts once per room: "@agent is out of quota for about <wait> and will
pick up again then." No wake is attempted until the time passes.

opencode agents on free models rotate instead: `OpencodeAdapter` tries the next free model
from the live catalog (`quorus/runtime/opencode_free.py`) when one is rate-limited, slow
to start, or unroutable (each sidelined model gets a 15-minute cooldown), and returns to
its home model to wait out the limit when every free model is busy.

## Vendor failover: the task is handed to a teammate, at most twice

For a typed `VendorFailure` on a writable turn, `quorus/runtime/failover.py::attempt()`
picks another room member that is online and active, has not already failed on this task,
and runs a different vendor's harness (`choose_candidate()` ranks them by capability
bid), transfers the claimed task, and posts a typed `/defer` carrying the original message id,
the branch, the last commit and the list of agents that already failed. A per-agent ledger
(`failover-<agent>.json`) stops the same agent handing off the same task twice, across
restarts. After two hand-offs the chain stops and the owner is alerted:
"⚠ stuck: vendor failover limit reached; needs you". Read-only and manual rooms post the
diagnosis only and never hand off.

## Pacing: an agent stays inside its owner's plan

`quorus agent pace` records a turns-per-hour cap (`pace_for()` in `scripts/reflexd.py`).
Past the cap, `Reflexd._paced()` parks the newest agent-to-agent message per room and
posts once an hour: "@agent is pacing itself (N turns/hour to stay inside its plan); next
turn in about <wait>". A human's message always goes through. Turns count only when the
harness actually ran.

## Unfinished turns never publish

Every writable turn runs under a hold (`scripts/reflexd_unfinished.py::hold()`), taken
before the harness starts. A turn that times out, errors or hits a vendor failure keeps the
hold: nothing from that worktree is published, by this wake, later wakes or the startup
sweep. `park()` copies the branch to `quorus/<agent>-unfinished` for review and the room
hears: "⚠ ran out of time … before finishing. Its commits are on <branch>, not published:
review and merge, or mention me to finish." Only a clean finish runs `clear()` and
publishes. The marker is written before the turn and survives restarts; a turn whose
marker cannot be written does not run.

## Held windows: a redirect mid-turn stops a publish

If the owner or the room's lead @-mentions the agent while a turn is running, the daemon
does not publish that turn's commits. `scripts/reflexd_held.py` records when the oldest
unpublished turn started (`mark_window()`), reads the room before publishing
(`find_stop()`), and on a hit moves the commits to a `<branch>-held-<stamp>` ref
(`hold()`), resetting the agent's branch to main. The next wake is told what was held and
must answer `reapply held` or `drop held` on a line of its own (`answer()`,
`resolve()`); a turn that does neither gets a ⚠ in the room. If the room history cannot
be read, the daemon holds rather than publishing blind.

## Read-only turns are reverted, not trusted

A non-owner's request, an answer-only turn, or a manual room's Codex/opencode/Gemini runs
read-only. `scripts/reflexd_readonly.py::begin()` snapshots the worktree before the turn
and `settle()` compares afterwards; anything changed is saved to
`refs/quorus/discarded/<agent>/<ts>` and the worktree is restored exactly. A marker that
cannot be read or settled blocks publishing until a human looks.

## Startup sweep

On start, `Reflexd._publish_sweep()` walks every bound repo: it settles leftover
read-only markers, skips worktrees with an unfinished hold, runs the held-window check,
and publishes only what passes; a merge conflict found with no agent awake is aborted and
left for the agent's next wake.

## What 0.6.3 does not do

- No automatic retry beyond the single fresh retry above; a second failure waits for a
  mention.
- No sandbox or rollback of side effects outside git (see `docs/sandboxing.md`).
- Failover only for typed vendor failures (quota, rate limit, login), never for a model
  that simply did the task badly.
