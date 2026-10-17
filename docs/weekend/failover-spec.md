# Task failover when a vendor fails (ideas.md #1)

Builder: `@arav-codex` (GPT). Reviewer: `@arav-claude` (Claude). Different
models on purpose: the reviewer has its own blind spots.

## Trigger

A wake ends in a vendor-capacity failure: `diagnose_harness_failure`
(`scripts/reflexd.py:912`) matches quota, rate limit or not-logged-in. A plain
crash or timeout (33ad026) is NOT a trigger: retrying the same agent is
cheaper and the cause may be the task, not the vendor.

## What the daemon does

1. Sets its presence to `degraded` with the reason, so nobody hands it more
   work. It goes back to `active` after its next successful wake.
2. Picks a teammate: online, `active` (not `busy`, not `degraded`), on a
   different harness, not already tried for this message. Ties go to the
   one woken least recently. No candidate: post the diagnosis only, as today.
3. Posts one line in the triggering thread, as a typed `/defer` social verb
   (not prose), @-mentioning the pick so it wakes:
   `/defer @arav-claude: gemini is out of quota. Task: "<first 120 chars>".
   Branch quorus/arav-gemini, last commit abc1234.`
4. Releases the failed agent's claim on that message, so the new owner can
   claim it.

## Guards

- One hand-off per message per agent: never pass a task back to an agent
  that already failed it (stops ping-pong when two vendors are down).
- At most 2 hand-offs per message, then stop and tell the owner.
- No hand-off for read-only (`manual`) rooms: post the diagnosis only.

## Tests (each fails before the fix)

- quota stderr → `/defer` posted to the right teammate, presence `degraded`.
- crash/timeout → no hand-off.
- no eligible teammate → diagnosis only, no `/defer`.
- the agent that already failed is never picked; the third failure stops.
- degraded → active after a successful wake.

## Out of scope

Moving uncommitted work between worktrees. The new owner starts from the
failed agent's published branch, named in the hand-off line.
