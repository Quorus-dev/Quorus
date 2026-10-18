# Invites without the shared secret (step 1) and observer links (step 3)

Status: design, revision 3 (codex blocked rev 2's migration, 45e1f921). No code
merges before codex's ✅.

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

## Codex's block on revision 1, and what changed

| Codex | Revision 2 |
|---|---|
| Invite as bearer proof needs atomic expiry, revocation and bounded uses | Invites become server-side records; redeem is one SQLite transaction |
| Invite signing falls back to the distributed shared secret (`relay.py:404`) | Shared-secret relays issue opaque random invite codes, stored hashed; no JWT, no signing secret |
| Retire the old unauthenticated join path | `/invite/{room}` and `/invite/{room}/join` answer 410 on shared-secret relays |
| A name prefix is not ownership | Claims record an immutable `owner`, `owner_claim_id` (generation) and `kind`; per-request parent checks; agent cap |
| (rev 2) Migrating old rows to human gives daemon keys human authority | Old rows become `unclassified`: they can't approve or own agents until the relay owner classifies them |
| Agents must never gain approval authority | Approval decisions require `kind=human`, whatever the name |
| Observer limits must cover every mutation | Default-deny: observers get an allowlist, not a refusal list |
| Observer turns read-only even with `trust: members` | The daemon reads the sender's role from the relay, and `observer` forces read-only |
| No silent re-claim after a saved-key 401 | Dropped. Re-enrolment is an explicit owner command; the store location is pinned |

## Step 1: invites that redeem into a participant key

Invites (shared-secret relays). A new `invites` table sits in the claims DB
(`participant_keys.db`, 0600): `id, code_hash, room_id, role, issuer, created_at,
expires_at, max_uses, uses, revoked`. The code is `secrets.token_urlsafe(24)`,
shown once and stored only as sha256. `max_uses` defaults to 1 (`quorus share
--uses N`, at most 20), and the TTL is at most 7 days. `quorus invites` lists
them (never their codes) and `quorus invites --revoke <id>` revokes one, both
owner-only (`GET /v1/join/invites`, `POST /v1/join/invites/{id}/revoke`). Nothing is signed, so no secret can
leak through the invite path.

Mint. `POST /v1/join/mint` (legacy admin) creates the record. The payload holds
`{"r", "n", "c": code, "v": 2}`, with no `"s"` and no `"i"`.

Redeem. `POST /v1/join/redeem {code, name}`, unauthenticated: the code is the
bearer proof. Rate limit 10/h per IP. In one `BEGIN IMMEDIATE` transaction:
look up the hash; refuse if revoked, expired or `uses >= max_uses`; increment
`uses`; insert the claim (`kind=human`, `owner=NULL`, new claim generation).
409 if the name is taken (the use is not counted). Then add the name to the
room with the invite's role. If that fails, a compensating transaction deletes
the claim and gives back the use. Response: `{participant_key, room, role}`, once.

Signing material. The `InviteService` fallback to `RELAY_SECRET` (and to
`"dev-only-insecure-secret"`) goes away for shared-secret relays. They use
opaque codes, and the JWT invite path (API-key relays) keeps requiring
`INVITE_SECRET`/`JWT_SECRET` as today.

Old paths. On shared-secret relays, `/invite/{room}` and `/invite/{room}/join`
answer 410 "use `quorus join <code>`". Old clients given a v2 code fail closed
with "upgrade quorus". Codes minted before the change still contain the secret
until they expire (7 days at most); rotating `RELAY_SECRET` revokes them.

Client. `quorus join <code>` redeems and stores the key as the config's
`api_key` (0600). The CLI, MCP and daemon already exchange an api_key at
`/v1/auth/token`. `quorus share` mints. `quorus share --with-secret` keeps
today's behaviour for the owner's own machines and says it hands over admin.

## Agents belong to a person, not to a prefix

- Claims gain `kind` (`human` | `agent` | `unclassified`), `owner` and
  `owner_claim_id`, all set at insert and never updated by any route.
- Migration (codex, rev 2 block): existing rows include daemon keys, so they
  become `kind=unclassified`, not human. An unclassified claim can sign in and
  act as a plain member, but it can't decide approvals, own agents, or mint
  invites. Only the relay owner can classify it, on the relay machine (like
  `release-name`): `quorus classify-name <name> human` or `quorus classify-name
  <name> agent --owner <human>`. Each change is logged.
- New claims through the shared-secret `claim-name` must say what they are.
  The caller holds admin, so the declaration is trusted: reflexd sends
  `kind=agent` with its owner (`REFLEXD_OWNER`). `quorus phone`, `share-branch`
  and `join` send `kind=human`. A request with no kind stores `unclassified`.
- `POST /v1/auth/claim-name` with a participant JWT of `kind=human` claims an
  agent: `kind=agent`, `owner=<sub>`, `owner_claim_id=<the owner's current
  claim generation>`. At most 8 agents per owner generation. The name is
  validated as usual. `<owner>-<suffix>` is required as a readability
  convention, not as proof: proof is the owner columns.
- An agent's JWT is honoured only while its owner's claim still has the same
  generation (`owner_claim_id`). If the owner's name is released and claimed
  again, by them or by anyone else, every agent from the old generation is dead.
  The owner must also be a member of the room being touched. The agent's room
  role is the lower of its own and its owner's (`observer` < `member`). Kicking
  or downgrading the owner covers their agents on the next request.
- Owners add their own agents (`owner == caller`) to rooms they belong to
  (change at `rooms.py:132`). They can't add anyone else.
- Approval decisions (`POST /v1/approvals/{id}/decision`) require `kind=human`
  and the server-side approval authority for that approval. That rules out
  agents, daemon credentials and a decider named in the request body.

## Step 3: observers, default-deny

The membership row's role is read on every request. An `observer` (or an agent
whose effective role is `observer`) can only:

- `GET` anything room-scoped they're a member of: history, presence, state,
  members, scorecard, bundles list and download;
- post `chat` messages to the room (no other message types, so no claim,
  status, approval or decision messages).

Everything else room-scoped answers 403. That covers bundles upload, approvals,
every `/v1/social/*` write, all work-queue mutations, locks and releases,
goals, decisions and room state, membership, settings, invites, tool and
webhook registration, and memory writes. This is one dependency
(`require_room_writer`) on every non-GET room route, plus a test that walks
the app's route table and asserts that every mutating room-scoped route has it
(or is on the explicit chat allowlist). A new route can't skip it silently.

Daemon. Before a wake, reflexd reads the sender's role from the relay's member
list. A sender whose effective role is `observer` gets a read-only turn, even
in `trust: members` rooms, and the session isn't saved or resumed. A sender
missing from the member list is also read-only.

## Re-enrolment after a key store moves (no silent re-claim)

The store's location is pinned once: 32c97a6e's rule, plus `QUORUS_DATA_DIR`.
At startup the relay logs the full store path at INFO. If a saved key gets 401,
the daemon still fails closed. Its error now names the fix: `quorus agent
reenroll <name>`, run by the owner. That deletes the saved key so the next
start claims again, and it refuses (409) if someone else holds the name. It is
never automatic.

## Tests (each fails on today's code)

- No v2 mint response, resolve payload or install script contains `RELAY_SECRET`.
- Redeem: a single-use code works once, then 403. Revoked and expired codes get
  403. Two redeems racing for the last use: exactly one wins. 409 on a taken name
  doesn't spend the use.
- `/invite/{room}/join` answers 410 on a shared-secret relay.
- Agents: `alice` claims `alice-codex`; a 9th agent is refused; `bob` can't add
  `alice-codex` to a room. Kicking `alice` makes `alice-codex` 403 on the next
  request.
- An agent named like a human (`kind=agent`) gets 403 on an approval decision,
  and so does a migrated (`unclassified`) daemon key.
- Releasing `alice` and claiming her again (a new generation) kills
  `alice-codex` from the old generation, even though the owner name is the same.
- The route-table walk: every mutating room-scoped route refuses an observer.
- Daemon: an observer's @-mention in a `trust: members` autonomous room gets a
  read-only turn.
- `reenroll` is the only path back after a 401. The daemon never claims again by
  itself.
