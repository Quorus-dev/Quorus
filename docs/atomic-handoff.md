# Relay compatibility and handoff ownership

Capsule handoffs require both a client and a relay supporting the atomic handoff
protocol. Schema support alone is insufficient: the earlier 0.7.0 candidate
accepted capsules but still used separate release/defer calls.

The client reads the relay's existing `/health` endpoint and requires
`atomic_capsule_handoff: 1`. A missing capability produces **relay too old for
handoff**, alerts the owner and leaves the claim unchanged. Unsupported results
are cached for the connection's lifetime, with no fallback to release/defer.
Discovery generates no requests to missing routes on older relays. A 400, 404,
405 or 422 from the handoff operation also fails closed with that notice.

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
