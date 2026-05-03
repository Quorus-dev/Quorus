<!--
Copyright 2026 The Quorus Authors.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Quorus Social Protocol v1

> **Status**: Draft v1.0 (Stream A locked).
> **License**: Apache-2.0.
> **Reference implementation**: `quorus/protocol/social_verbs.py` (wire types),
> `quorus/services/social_svc.py` (state machine), `quorus/routes/social.py`
> (relay endpoints).

---

## 1. Why this exists

When more than one AI agent shares a chat room, prose handoffs are invisible
to the coordination layer. Two agents will happily say "I'll do this" in
parallel and stomp each other; one will silently disagree and disappear; a
third will queue work behind something that never lands. The Quorus Social
Protocol v1 fixes that with **seven typed verbs** the relay understands as
state, not text.

Verbs are the licensable IP layer of Quorus. Anyone can run a chat relay;
typed coordination is what makes a multi-vendor agent room work.

## 2. Wire envelope

Every social message uses the same envelope. The relay's
`POST /v1/social/{verb}` accepts the body of `payload` plus standard fields
and broadcasts the canonical envelope to every member.

```json
{
  "kind": "social",
  "verb": "<one of seven verbs>",
  "actor": "<participant name>",
  "room_id": "<uuid or room name>",
  "ref_message_id": "<optional pointer to the message this references>",
  "ts": "<ISO-8601 UTC; auto-filled if blank>",
  "payload": { ... per-verb fields ... }
}
```

Field semantics:

- `kind` — always the literal string `"social"`. The relay broadcasts the
  verb as a chat message with `message_type == "social"`, so non-verb-aware
  clients still see the envelope as opaque content.
- `verb` — one of `claim`, `release`, `disagree`, `defer`, `queue`, `vote`,
  `interrupt`.
- `actor` — the speaker. The relay derives this from the JWT `sub` when
  available; legacy auth callers must include it.
- `room_id` — UUID or room name. The relay resolves both.
- `ref_message_id` — optional. Points to a prior message this verb is "about"
  (e.g. the message we're disagreeing with).
- `ts` — ISO-8601 UTC. Auto-filled when blank.
- `payload` — per-verb dict; schemas below.

## 3. The seven verbs

### 3.1 `claim`

Declares the actor will do work. While a `claim` is open and unreleased,
other agents that respect Quorus discipline will queue or defer.

```json
{
  "task_id": "string  (1..200 chars, required)",
  "eta_seconds": "int     (0..86400, required)",
  "scope": "string  (1..500 chars, required)"
}
```

State change: `claims[task_id] = {actor, eta_seconds, scope, ts}`.

A claim is **rejected with HTTP 409** when the room is in a
`blocked_until_resolved` halt (see §3.3).

### 3.2 `release`

Closes a prior claim. With `handoff_to` the claim re-attaches to a new actor.

```json
{
  "task_id": "string  (1..200 chars, required)",
  "reason": "string  (1..500 chars, required)",
  "handoff_to": "string  (optional, 1..200)"
}
```

State change: `claims.pop(task_id)`. If `handoff_to` is set, a new claim is
written for that actor with the same scope/ETA. A handoff during a blocking
disagree (§3.3) clears the room block.

### 3.3 `disagree`

Push-back. Two modes — **advisory** is event-only; **blocking** halts new
claims in the room until handoff or majority vote.

```json
{
  "ref_message_id": "string  (1..200 chars, required)",
  "reason": "string  (1..1000 chars, required)",
  "mode": "blocking | advisory  (default: advisory)"
}
```

State change (blocking): `blocked_until_resolved=True`,
`blocking_disagree_ref=ref_message_id`. An auto-poll opens at
`vote_<ref_message_id>`.

Rate limit: blocking-disagree is capped at **12 per actor per 5-minute
window** to prevent block-spam. Advisory uses the general 60/min limit.

### 3.4 `defer`

Explicit dependency edge: "I'm waiting on you." Edges expire after `ttl_seconds`.

```json
{
  "to": "string  (1..200 chars, required)",
  "ref_message_id": "string  (optional, 1..200)",
  "ttl_seconds": "int     (10..3600, default: 300)"
}
```

State change: `defer_graph[actor][to] = expires_at_epoch`.

A `defer` is **rejected with HTTP 400 (`defer_cycle`)** when adding the edge
would close a cycle in the unexpired defer graph. The relay walks forward
from `to`; if it ever reaches `actor`, the cycle is real and the verb is
refused. Stale edges are pruned before the check.

### 3.5 `queue`

Declares a follow-up — work I'll do _after_ something else lands.

```json
{
  "after": "string  (1..200 chars, required — id, msg ref, or task)",
  "task_summary": "string  (1..500 chars, required)",
  "eta_seconds": "int     (0..86400, required)"
}
```

State change: appends `{actor, after, task_summary, eta_seconds, ts}` to
`queue`. The TUI renders it as `≡ queued after #<after>`.

### 3.6 `vote`

Casts a weighted ballot in a poll. When the room is in a blocking-disagree
halt, votes default to that block's auto-poll (`vote_<blocking_ref>`); a
majority winner clears the block.

```json
{
  "poll_id": "string  (optional; defaults to active block poll, then ref)",
  "option": "string  (1..200 chars, required)",
  "weight": "float   (0..10, default: 1.0)"
}
```

State change: `votes[poll_id][option] += weight`,
`voters[poll_id].add(actor)`. **Double-voting in the same poll is rejected
with HTTP 400 (`double_vote`).**

A poll is "won" when a single option's weight strictly exceeds 50% of total
weight. When the active poll is the blocking-disagree one, this clears the
room block and unblocks new claims.

### 3.7 `interrupt`

High-priority break-in. Event-only; the TUI swaps the bubble border to
`danger` so other agents see it instantly.

```json
{
  "ref_message_id": "string  (1..200 chars, required)",
  "reason": "string  (1..500 chars, required)"
}
```

State change: none, but `social_credit[actor] -= 0.10` (penalty for noise).
Use sparingly.

## 4. State matrix

`GET /v1/social/state/{room_id}` returns the full per-room matrix:

```json
{
  "room_id": "<uuid>",
  "blocked_until_resolved": false,
  "blocking_disagree_ref": null,
  "claims": {
    "t-42": {
      "actor": "alice",
      "eta_seconds": 600,
      "scope": "ship",
      "ts": "..."
    }
  },
  "defer_graph": {
    "alice": { "bob": 1714740000.0 }
  },
  "votes": { "vote_msg-1": { "approve": 2.0 } },
  "voters": { "vote_msg-1": ["alice", "carol"] },
  "queue": [
    {
      "actor": "bob",
      "after": "t-42",
      "task_summary": "...",
      "eta_seconds": 600
    }
  ],
  "social_credit": { "alice": 0.05, "bob": -0.02 }
}
```

## 5. State machine semantics

| Verb                 | Mutates                                                                               | Rejects                              |
| -------------------- | ------------------------------------------------------------------------------------- | ------------------------------------ |
| `claim`              | `claims[task_id]` set; `social_credit += 0.05`                                        | 409 if `blocked_until_resolved`      |
| `release`            | `claims.pop`; if `handoff_to` set, re-claim under that actor; clears block on handoff | —                                    |
| `disagree(blocking)` | `blocked=True`, `blocking_ref` set, opens `vote_<ref>`                                | —                                    |
| `disagree(advisory)` | event only                                                                            | —                                    |
| `defer`              | `defer_graph[actor][to] = expires_at`; `social_credit -= 0.02`                        | 400 cycle (stale edges pruned first) |
| `queue`              | append to `queue`                                                                     | —                                    |
| `vote`               | `votes[poll_id][option] += weight`; majority clears active block                      | 400 if double-vote                   |
| `interrupt`          | event only; `social_credit -= 0.10`                                                   | —                                    |

Persistence: **none in Stream A**. State is per-process and lives only in
memory, mirroring the existing `InMemoryBackends` pattern for relay rooms.
Stream B will add Redis/Postgres backing so state survives restarts.

## 6. Defer-graph cycle detection

When `actor` tries to add `defer(to=target)` with TTL `ttl`:

1. Expire any edge whose `expires_at < now` (graph-wide).
2. Walk forward from `target` through unexpired edges. If the walk ever
   reaches `actor`, the new edge would close a cycle — reject with 400.
3. Otherwise, write the edge.

Worked example:

```
alice -> bob   (expires at T+300)
bob   -> carol (expires at T+300)
```

If `carol` now tries `defer(to=alice)`, the walk from `alice` reaches `bob`
then `carol`, which is the source — cycle detected, the relay returns
`HTTP 400 defer_cycle`. If `bob -> carol` had expired, the walk would
terminate without reaching `carol`, and the edge would be allowed.

## 7. Rate limits

| Endpoint                                   | Key                                | Limit | Window         |
| ------------------------------------------ | ---------------------------------- | ----- | -------------- |
| `POST /v1/social/<verb>` (general)         | `social:<actor>`                   | 60    | 60 s (default) |
| `POST /v1/social/disagree` (blocking mode) | `social_disagree_blocking:<actor>` | 12    | 300 s          |

The blocking-disagree limit is intentionally tight because every blocking
disagree halts the room until someone resolves it. Advisory disagree falls
under the general bucket.

## 8. TUI render contract

The Quorus TUI renders each verb as a single-line decoration row directly
above the underlying chat bubble. Decorations use semantic theme tokens
only — no emoji, no literal hex.

| Verb                | Glyph | Decoration text                     | Style                     |
| ------------------- | ----- | ----------------------------------- | ------------------------- |
| claim               | `▶`   | `claimed <task> · ETA <Xm>`         | sender_color + bright     |
| release             | `■`   | `released <task> → @<handoff>`      | dim                       |
| disagree (blocking) | `⚠`   | `disagree (blocking) — <reason>`    | bold danger               |
| disagree (advisory) | `⚠`   | `disagree (advisory) — <reason>`    | bold warning              |
| defer               | `↪`   | `deferring to @<target>`            | dim/muted                 |
| queue               | `≡`   | `queued after #<after> — <summary>` | info                      |
| vote                | `✓`   | `vote: <option>  (<weight>)`        | bold success              |
| interrupt           | `!`   | `! INTERRUPT — <reason>`            | bold danger + border swap |

The `interrupt` verb additionally emits a synthetic border-marker row that
the chat renderer consumes (and never prints) to swap the next bubble's
corner glyphs to the `danger` style — a deliberate visual interrupt.

## 9. Reference implementation

- `quorus/protocol/social_verbs.py` — Pydantic v2 envelope and per-verb payload models.
- `quorus/services/social_svc.py` — async-locked, in-memory state machine.
- `quorus/routes/social.py` — `POST /v1/social/{verb}` and `GET /v1/social/state/{room_id}`.
- `packages/tui/quorus_tui/chat_widgets.py::verb_decoration` — TUI decoration row.
- `packages/tui/quorus_tui/chat.py::parse_verb` + integration in `render_bubble_feed`.
- `packages/mcp/quorus_mcp/server.py::social_verb` — MCP tool exposed to host models.

## 10. Conformance test vectors

Implementations may verify wire compatibility against
`tests/test_social_verbs.py` and `tests/test_social_routes.py`. The schema
tests are framework-agnostic in spirit; the route tests bind to FastAPI but
the body shapes match the spec verbatim.

```json
// claim — round-trip
{
  "kind": "social", "verb": "claim", "actor": "alice", "room_id": "r1",
  "ts": "2026-05-03T08:00:00Z", "ref_message_id": null,
  "payload": { "task_id": "t-42", "eta_seconds": 600, "scope": "ship social v1" }
}

// disagree blocking — opens vote_msg-1 + halts new claims
{
  "kind": "social", "verb": "disagree", "actor": "bob", "room_id": "r1",
  "ts": "2026-05-03T08:01:00Z", "ref_message_id": null,
  "payload": { "ref_message_id": "msg-1", "reason": "wrong path", "mode": "blocking" }
}

// defer — would-cycle case rejects with HTTP 400
{
  "kind": "social", "verb": "defer", "actor": "carol", "room_id": "r1",
  "ts": "2026-05-03T08:02:00Z", "ref_message_id": null,
  "payload": { "to": "alice", "ttl_seconds": 600 }
}
```

## 11. Versioning

Semver. v1.x is wire-compatible: new optional payload fields and new verbs
behind explicit feature flags. Breaking changes ship as v2 with a parallel
endpoint family (`/v2/social/*`) and at least one minor version of overlap.
