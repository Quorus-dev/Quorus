# Invites without the shared secret (step 1) and observer links (step 3)

Status: design, waiting for codex's review. No code merges before codex's ✅.

## The gap

On a shared-secret relay (the default local relay), `POST /v1/join/mint` puts
`RELAY_SECRET` into the join payload (`quorus/routes/join_codes.py:162`), and
`quorus share` encodes it offline (`_encode_join_token`, `cli.py:5554`). The
secret is admin: whoever joins can read every room, forge any sender, approve
anything and mint more invites. README "Security model" and LAUNCH row 7 list
this as a residual risk.

What already exists and is reused:

- Participant keys (`quorus/auth/participant_keys.py`): a `quorus_pk_` key per
  name, exchanged at `/v1/auth/token` for a JWT with `sub=<name>`, tenant
  `_legacy`, role `user`, and a claim generation checked on every request, so
  `quorus release-name` retires the tokens.
- A `user`-role JWT is not `is_legacy` and not admin, so the room routes
  already enforce membership for it (`room_auth.require_room_member`,
  `rooms.py:132`): it can't list, read or join rooms it wasn't added to.
- The scoped invite token (`invite_svc.create_token(..., role="member")`) that
  mint already embeds as `"i"`, and `room_svc.join(..., role)` that stores a
  role per member.
- Every client (CLI, MCP server, daemon) already handles an API key: exchange
  it at `/v1/auth/token`, use the JWT. A participant key goes through the same
  path, so clients need no new auth mode.

## Step 1: redeem an invite into a participant key

1. `POST /v1/join/mint` stops adding `"s"`. The payload carries the invite
   token `"i"` (room, tenant, role, expiry) and a new `"v": 2`.
2. New `POST /v1/join/redeem {invite_token, name}`. Unauthenticated: the
   invite token is the proof, as with `/invite/{room}/join` today. It:
   - verifies the token (signature, expiry, tenant `_legacy`, room exists);
   - claims `name` (409 if taken, same message as claim-name);
   - adds `name` to the room with the token's role;
   - returns `{participant_key, room, role}` once.
   Rate limit: 10 per hour per IP, like claim-name. Redirects are never followed
   by the client (96b1cb76).
3. `quorus join <code>`: with `"v": 2` it calls redeem and writes the key as the
   config's `api_key` (0600). No secret ever lands on the joiner's disk.
4. The joiner's agents: `claim-name` today needs the shared secret. It will also
   accept a participant JWT, but only for names `<sub>-*` (for example
   `alice-codex` for `alice`). The claim records its owner. The owner can add their own agents to
   rooms they belong to, with a role no higher than their own.
5. `quorus share` on a shared-secret relay calls mint instead of encoding the
   secret. `quorus share --with-secret` keeps today's behaviour for the owner's
   own second machine, and says plainly that it hands over admin.
6. Old clients given a v2 code fail with "upgrade quorus to join this room".
   That is fail-closed, not a silent fallback to signup. Codes minted before
   the change still contain the secret until they expire (7 days at most).
   Rotating `RELAY_SECRET` is how to revoke those.

Revocation: kick the member from the room (immediate, membership is checked on
every request), or `quorus release-name <name>` on the relay machine (retires
every token for the name).

## Step 3: observer links

`quorus share --observer` mints with role `observer`. The relay is the source
of truth for a role: the membership row, read on every request. The role is
not baked into the JWT, so a downgrade or kick takes effect on the next call.

| Observers can | Observers are refused (403) |
|---|---|
| read room history, presence, state, scorecard | `POST /v1/rooms/{room}/bundles` (upload) |
| post chat messages | `POST /v1/approvals/{id}/decision` |
| `GET` bundles and fetch them | social claim, defer, block (`/v1/social/*` writes) |
| add their own agents as observers | work-queue claims, room settings, invites, kick |

The daemon side: a message from an observer, or from an observer's agent, only
ever gets a read-only turn. That reuses the existing non-owner downgrade
(`trusted_sender`), and the role is checked on the relay member list so a name
can't claim to be a member.

## Questions for codex

1. Is an unauthenticated redeem, with the invite token as proof, acceptable? Or
   should redeem also need something the joiner can't replay from a leaked
   code, like a one-time use per token?
2. The `<sub>-*` rule for agent names: is a prefix enough, or should agent
   claims be listed per owner and capped (say 8)?
3. Is the refusal list complete? I'd also refuse `POST /rooms` (creating rooms)
   for observers.
4. Membership row only, or also stamp the role in the JWT for defence in depth?
   I lean towards row only, because a stamped role outlives a downgrade.
5. The existing `POST /invite/{room}/join` adds any name it is given to the
   room, with only the invite token as proof and no claim. Once redeem exists,
   should it refuse names that are already claimed on shared-secret relays, or
   be retired there entirely?
6. Adding your own agents needs a change in `rooms.py:132`. Today a non-admin
   JWT can only add members to a room it created. I'd allow `<sub>-*` names
   when the caller is a member, with a role no higher than the caller's.

## Tests (per step, each fails on today's code)

- A v2 code never contains `RELAY_SECRET` (mint response and resolve payload).
- Redeem: a key works for its room and gets 403 for another room; a reused name
  gets 409; an expired token gets 403.
- The joiner's agent can claim `alice-codex` but not `bob-codex` or `alice`.
- Each refused endpoint answers 403 for an observer and works for a member. A
  kick takes effect on the observer's next request.
- Daemon: an observer's @-mention gets a read-only turn in an autonomous room.
