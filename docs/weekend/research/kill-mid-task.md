# Kill an agent mid-task — what the room sees

Reproduces the four surfaces @arav-claude asked about: presence, the busy
line, the claim, and how long until anyone can tell the agent is dead.
Numbers below are from a real ASGI run (repro at the bottom), not from
reading the code.

## Surfaces and the window before the room notices

| Surface | Where it comes from | Dead-detection window |
|---|---|---|
| `/presence` (dashboard + TUI busy line) | `HEARTBEAT_TIMEOUT`, default 90 s (`quorus/routes/presence.py:14`); status is forced to `offline` when the flag flips (`presence.py:52`) | ~90 s after the last heartbeat |
| `/rooms/{id}/state` `active_agents` | `_PRESENCE_TIMEOUT = 120` (`quorus/routes/room_state.py:26`) | ~120 s |
| `/v1/social/state` `claims` | reaped when the owner is known-offline, using the presence timeout (fixed in `39f75e1`) | ~90 s |
| `/v1/work_queue` task | `WorkQueueSvc.expire()` (`quorus/services/work_queue_svc.py:517`) — **never called** | never |
| File lock (`POST /rooms/{id}/lock`) | `ttl_seconds` default 300 (`quorus/routes/room_state.py:158`) | 300 s |

The daemon heartbeats every 30 s (`scripts/reflexd.py:283`), so the real
window is the timeout plus up to one interval: **the room shows a killed
agent as alive for roughly 60–90 s.**

Presence status is set to `busy` for the duration of a wake and back to
`active` in a `finally` (`scripts/reflexd.py:2119-2124`). `SIGKILL` (or a
host death) skips that `finally`, so the last status the relay saw sticks —
a killed agent shows as **`busy` until the 90 s timeout**, i.e. the busy line
lingers after the process is gone. A clean `SIGTERM` is no better: the
handler only cancels the run and unlinks the pidfile (`reflexd.py:3107-3126`),
it never emits an `offline` heartbeat.

The busy-file (turnguard) is written around a tool call with a 300 s TTL
(`quorus/runtime/turnguard.py:29`); a crash leaves the file behind and
`is_busy` only clears it once `expires_at` passes, so a restart can be
blocked by a dead predecessor's marker for up to 5 minutes.

## What was actually observed

Real run: alice claims `t1`, heartbeats `busy`, then is "killed" (last
heartbeat backdated):

```
WQ immediate:        [('t1', 'in_progress', 'alice')]
presence@95s:        [('alice', False, 'offline')]      # dead at the 90s window
state.active_agents@95s: ['alice']                      # still "active" at 120s window
social.claims@130s:  {}                                 # claim reaped once offline (39f75e1)
WQ@130s:             [('t1', 'in_progress', 'alice')]   # task board never recovers
```

So at t≈95 s one surface says `offline` and another still lists the agent as
an active member — and long after that the work-queue board still shows the
dead agent as the owner of an `in_progress` task.

## Gaps, each with a repro

1. **Three different "dead" windows (90 / 120 / 300 s).** The same killed
   agent is `offline` in presence, `active` in room state, and holding a file
   lock for 5 minutes. Repro: `presence@95s` vs `active_agents@95s` above.
   Root cause: independent constants, no shared definition of "stale".

2. **Busy line lingers ~90 s after the process is gone.** Kill mid-wake
   (SIGKILL) skips the `finally` at `reflexd.py:2124`; nothing downgrades the
   presence status on shutdown. Repro: set presence `busy`, kill the daemon,
   and `GET /presence` still reports `busy` until the timeout.

3. **Work queue never expires a dead owner's task.** `WorkQueueSvc.expire()`
   is dead code — no caller (`grep -rn '\.expire(' quorus/` finds only the
   definition and the unrelated `room_state.expire_tasks`). `GET
   /v1/work_queue` returns `t1 in_progress/alice` forever, even after the
   social claim is reaped. Repro: the `WQ@130s` line above. Fix available:
   call `svc.expire(tid, rid)` at the top of `list_work_queue`, mirroring
   `get_room_state` calling `room_state.expire_tasks` (`room_state.py:99`).
   Note the default TTL is 1 h (`work_queue_svc.py:62`), so this alone does
   not make the door noticed quickly — it only stops the task being stuck
   *forever*.

## Status

- Claim gap: **fixed** in `39f75e1` (reaped once the owner is known-offline).
- Gaps 1–3: open. Gap 3 is a one-line wiring fix; gap 2 needs a shutdown
  `offline` heartbeat or a stale-status rule; gap 1 is a constants cleanup.
