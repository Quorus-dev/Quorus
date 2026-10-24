# First run without a model or sign-in

Status: approved by Claude and assigned for implementation by the overseer,
targeted at 0.7.3 and excluded from 0.7.2. Implementation awaits code review.

## Problem and outcome

The PyPI 0.7.1 stranger pass reached #general with a composer, but could not get
an agent answer. Free models stayed busy, and a clean HOME had no signed-in
Claude Code or Codex fallback. The room already works without a model; the gap
is helping the person understand that state and recover toward a real agent
response without silently spending another account's usage.

Retain the existing room, human identity and composer. Do not add a test-message
action or treat an empty-room human message as proof of agent onboarding.
A room being available, an agent joining, and an agent answering are separate
observable states. Only a real response completes the agent-response step.

## Proposed changes

1. Keep agent setup available while the room has no agents. Leaving or
   interrupting the first-run prompt must not hide setup permanently. Reopening
   the room keeps an accessible add-agent action without repeatedly forcing the
   wizard or blocking the composer. This addresses stranger-path item 6, for
   0.7.3 only; it does not expand the 0.7.2 hotfix.
2. Distinguish "No agents connected", "Agent joined; waiting for a response",
   and "Agent answered" using real membership and message events. A successful
   daemon launch or membership update is not an answer. Errors, unavailable
   models and timeouts must leave the response step visibly incomplete, with
   retry/setup actions and the room still usable. Existing message delivery and
   deduplication remain unchanged.
3. Before a fallback starts, identify which signed-in CLI/account supplies the
   login and whose usage it consumes. Require an explicit choice before using
   that fallback; offer retry later or continuing without an agent. Do not probe
   vendors or consume a login merely because the room opens or reopens.

No model or login is required to enter the existing room. When the user chooses
agent setup, missing harnesses/logins get the existing setup instructions. Busy
free models are described as unavailable now, with no fabricated answer or
quota event counted as successful work. Preserve the person's conversation
through retries and setup cancellation.

`quorus demo` remains an optional throwaway-repo demonstration. Its no-model
result stays nonzero (9023eccb); reaching #general does not turn that failed
model demonstration into success.

## Scope and boundaries

Reuse the existing local relay, profile, human identity, membership, composer,
invite controls and history. Prefer small changes to the TUI setup/session flow
and the existing fallback confirmation surface; extract helpers rather than
growing hub.py. Audit existing disclosure first and retain adequate wording.

No new message action, relay schema, auth bypass, provider, background model
polling, automatic paid fallback, fabricated conversation or CLI installation.
The owned-loopback migration is a separate 0.7.2 prerequisite: never trust a
localhost alias when the relay does not own every address it may resolve to.
Keep invitee identities and remote-relay behavior unchanged.

The reported doctor/sign-in disagreement was a PATH mistake; it needs no patch.
This plan changes only the three gaps above, after approval.

## Acceptance and validation

Use isolated HOME, QUORUS_CONFIG_DIR and process state. Do not read real
credentials or affect an existing relay. Stop only test-owned processes.

- With no harness or login, #general remains accessible with its existing
  composer and optional setup action; no vendor/auth-status command blocks entry.
- Cancel or interrupt setup, then reopen: setup remains available, the room
  remains usable, and no unsolicited model probe or daemon restart occurs.
- After a real agent joins, show waiting status until a real response arrives.
  Check failure and timeout paths do not produce a completed-response state.
- Busy free models offer retry, continue-without-agent, or an explicitly chosen
  signed-in fallback. Test that no fallback runs before its usage disclosure and
  user choice, and cancellation consumes no fallback usage.
- Scripted runs require a non-interactive opt-in (for example,
  `--use-my-login`) before using a signed-in fallback. Without it, do not prompt
  or consume that login's usage; report the unavailable model and exit nonzero.
  Verify both opted-in and default behavior, including the usage disclosure.
- Existing messages survive retries/reopening without duplication. Existing
  invitee and returning-user identities/rooms remain unchanged; no unsolicited
  #general creation on a teammate's relay.
- Include the IPv4-only local-owner path using the separately reviewed migration.

Run focused setup, status, fallback-consent and existing message/invite regression
tests. Finish with a clean-HOME transcript showing the no-agent room, canceled
setup and reopening, joined/waiting state, disclosure/cancellation, and teardown.
Record an actual agent response separately when a provider is available; do not
claim success if none answers.

Claude reviews this revision; the overseer decides whether and when it is built.
No publication, outreach or release follows automatically.
