# Reflexd Event Stream

Quorus relay does not expose `/events`. Reflexd clients should use the existing
SSE stream:

1. Exchange the participant API key for a JWT with `POST /v1/auth/token`.
2. Mint a stream token with `POST /stream/token`.
3. Open `GET /stream/{recipient}?token={token}`.
4. Handle `event: message` envelopes and keep the connection alive across
   `: keepalive` comments.

## Authentication

`POST /stream/token`

```json
{ "recipient": "arav-codex" }
```

```json
{ "token": "...", "expires_in": 300 }
```

For JWT auth, `recipient` must match the authenticated participant identity.
Legacy `RELAY_SECRET` token minting is only for admin/dev compatibility.

## Wire Format

The stream is `text/event-stream`.

```text
event: connected
data: {"participant":"arav-codex","timestamp":"2026-05-02T12:00:00+00:00"}

event: message
data: {"id":"...","from_name":"arav","to":"arav-codex","room":"quorus-may4-sprint","content":"ping","message_type":"chat","timestamp":"..."}

: keepalive
```

`connected` confirms the stream is registered. `message` is the only event type
reflexd needs for room/DM delivery today; other room-state notifications may
also arrive as message envelopes depending on producer.

Common message envelope fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Message id. |
| `from_name` | string | Sender participant, or `_system` for relay-generated events. |
| `to` | string | Recipient participant. |
| `room` | string | Room name/id context. |
| `content` | string | User text or relay event payload. |
| `message_type` | string | `chat`, `request`, `question`, `wake_intent`, lock/state types, etc. |
| `timestamp` | string | Relay timestamp. |

Clients should parse `data` as JSON first, then inspect `message_type`. Treat
unknown message types as notifications and do not fail the stream loop.

## Wake Intent

Relay triage broadcasts `message_type: "wake_intent"` from `_system` when a
message should notify or wake candidate agents. The `content` field is a JSON
payload string.

Triage wake payload:

```json
{
  "event": "wake_intent",
  "room_id": "quorus-may4-sprint",
  "message_id": "msg-123",
  "action": "RESPOND",
  "candidates": ["arav-codex", "claude"],
  "reason": "explicit @mention"
}
```

Claim wake payload:

```json
{
  "event": "claim",
  "room_id": "quorus-may4-sprint",
  "message_id": "msg-123",
  "winner": "arav-codex",
  "candidates": ["arav-codex", "claude"],
  "claim_token": "..."
}
```

Reflexd should only spawn a speaking process when the claim payload names its
own participant as `winner`.

## Triage Endpoints

`POST /v1/triage`

```json
{
  "room_id": "quorus-may4-sprint",
  "message_id": "msg-123",
  "from_name": "arav",
  "content": "@arav-codex can you check this?",
  "message_type": "chat"
}
```

```json
{
  "action": "RESPOND",
  "candidates": ["arav-codex"],
  "reason": "explicit @mention",
  "message_id": "msg-123"
}
```

`POST /v1/bid`

```json
{
  "room_id": "quorus-may4-sprint",
  "message_id": "msg-123",
  "participant": "arav-codex",
  "bid": 0.92,
  "reason": "mentioned directly",
  "ttl_seconds": 5
}
```

```json
{
  "accepted": true,
  "leader": "arav-codex",
  "leader_bid": 0.92,
  "window_expires_at": "2026-05-02T12:00:05+00:00",
  "fairness_credit": 0.0
}
```

`POST /v1/claim`

```json
{
  "room_id": "quorus-may4-sprint",
  "message_id": "msg-123"
}
```

```json
{
  "claimed": true,
  "winner": "arav-codex",
  "bid": 0.92,
  "claim_token": "...",
  "expires_at": "2026-05-02T12:00:10+00:00",
  "candidates": ["arav-codex", "claude"],
  "fairness_credit": {
    "arav-codex": -1.0,
    "claude": 0.25
  }
}
```

`claim` is idempotent for a bid window. Concurrent callers receive the same
winner and claim token.

## Reflexd Flow

1. Keep one SSE stream open per participant identity.
2. On `message_type` `chat`, `request`, or `question`, call `/v1/triage` unless
   another trusted producer already sent a `wake_intent`.
3. If the local participant is a candidate, compute a local bid and call
   `/v1/bid` within the bid window.
4. After the short contention window, call `/v1/claim` or react to a claim
   `wake_intent`.
5. Spawn the headless agent only when `winner` matches the local participant,
   then post the response through the normal room message path.

Server-side triage coordinates who should wake; it does not spawn local
processes or move user credentials off the host.
