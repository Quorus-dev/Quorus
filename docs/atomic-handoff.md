# Relay compatibility and handoff ownership

Capsule handoffs require both a client and a relay supporting the atomic handoff
protocol. Schema support alone is insufficient: the earlier 0.7.0 candidate
accepted capsules but still used separate release/defer calls.

The client reads the relay's existing `/health` endpoint and requires
`atomic_capsule_handoff: 1`. A missing capability produces **relay too old for
handoff**, alerts the owner and leaves the claim unchanged. Unsupported results
are cached for the connection's lifetime, with no fallback to release/defer.
Discovery generates no requests to missing routes on older relays. A 404 or 405
from the handoff operation also fails closed with that notice. A 400 or 422
from a compatible relay is reported as `handoff_rejected`, with a bounded textual
reason; it leaves the capability enabled for later attempts. Validation arrays
are not displayed because they may echo capsule input.

`POST /v1/handoff` validates the typed capsule or busy decline, authenticates its
actor, checks room membership/policy and verifies the currently owned task. Under
the social-state and work-queue locks it durably accepts the defer, then moves
the claim directly from sender to receiver. There is no unowned intermediate
state. Receiver ownership checks and concurrent chat claims wait for that same
queue lock. Repeating an accepted operation returns its receipt without sending
or transferring again, including after a busy receiver returned the claim.

Delivery failure or target queue backpressure leaves ownership with the sender.
When Redis is configured, a failed persistence acknowledgement is not success.
Message acceptance and the queue mirror use separate stores: a crash can leave
an accepted message without a completed claim transfer. The receiver's proof
gate must still confirm ownership before adopting or executing anything. An
ambiguous outcome keeps the sender fence and requires owner resolution.

For dogfood, use the reviewed client **and relay** build with its own port and
data directory. Do not upgrade or repoint the team's relay as a rehearsal step.

A proof receipt records reproduction, not publication. On a successful receiver
turn the daemon commits any dirty edits as that receiver, while retaining its
handoff hold, then authorizes completion using the strict clean-tree/ancestry and
claim checks. Only then may it publish. Completion evidence records `published`
only after checking integration; a failed/held publication records
`kept_unpublished` with a fixed cause. The report's `completion` column shows
`not recorded` for older or interrupted runs with no completion evidence.

## Discard and exact task ownership

Handoff gates request the capsule's exact task ID. Ordinary turn budgets may use
the participant's first active task, but that heuristic never grants handoff
write authority. Queue reads wait for the atomic transfer lock, including reads
started while the defer is being delivered.

An owner discard now rolls back the reviewed local head under the stopped-daemon
and journal locks, resolves only that task's relay claim, records `discarded`,
and only then lifts the hold. The task returns to `pending`, unclaimed; its social
claim is removed too. Failed/unknown relay acknowledgements retain the local hold
and backup. The exact same owner decision can be retried after a crash without
releasing someone else's newer claim. Publish decisions keep their claim.

`POST /v1/handoff/discard` requires the receiver, its verified owner, or a relay
admin, with room write/policy checks. It matches a protected capsule receipt and
uses the same social/queue locks and strict persistence acknowledgement as the
handoff. Older receipts without fingerprints are identified by their exact task
and receiver under the same owner authority. Client metadata cannot forge either
transfer receipts or discard acknowledgements. Both client and relay must be
upgraded for this discard path; an old relay fails closed.

Use the **owner's relay configuration**, with `--runtime-dir` pointing at the
receiver's journal, rather than changing the CLI config to the receiver's state:

```sh
CFG="$X/owner" qd handoff release --runtime-dir "$X/codex/runtime" \
  --room weekend-handoff-rehearsal --agent arav-handoff-codex \
  --capsule <reviewed-capsule> --head <reviewed-full-head> --discard
```

Existing stale claims that already lost their local hold are not silently
changed. The owner can resolve their exact task/receiver through the reviewed
relay endpoint; these fixes perform no live cleanup.
