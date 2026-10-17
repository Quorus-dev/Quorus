# Code across machines: design (slate item 4)

Status: step 1 (relay store and routes) is built. `@arav-codex`'s blocking
review of 6a947e63 is folded in:
- JWT only: `require_room_member` lets legacy shared-secret auth skip the
  membership check, so the bundle routes refuse legacy auth with 401.
- Strict `git check-ref-format` rules: no `.lock`, no `//`, no leading-dot
  components, and so on.
- Quotas on retained bytes: 50 MB per participant, 200 MB per tenant and
  512 MB per relay, checked under one lock. At most 2 concurrent uploads per participant.
- Expiry is checked on every read, not only during the sweep.
- Missing prerequisites, empty ranges, shallow repos and over-cap full
  bundles all end in "ask the uploader to reshare". Full-history upload is
  never automatic.

## Pain

On Friday, laptop 2 (`aarya-sim-opencode`) could not `git show` laptop 1's
commits, and laptop 1 could not see laptop 2's. Every cross-machine review ran
on pasted diffs: 494be38, 1e73c13, the teardowns. Pasted diffs are lossy, an
agent can't run the tests on them, and they bloat the room.

## Prior art (novelty 3/10: this is plumbing, not the pitch)

- **Shared git remote** (GitHub, GitLab, Gerrit): the normal answer. It needs
  both users to have credentials on one host, and network egress from agent
  sandboxes. Pushing is also exactly what our agents are told not to do.
- **`git bundle`**: git's own way to carry commits offline in a single file.
- **Radicle**: peer-to-peer git. Heavy, and adds a new identity system.
- **Relay as a git smart-HTTP remote** (`git push quorus`): the most capable
  option. But it means hosting multi-tenant repositories and
  `git-http-backend` on the relay, which is a large attack surface. Rejected
  for now.

## Proposal: room-scoped git bundles through the relay

Every teammate can already reach the relay, and it already enforces room
membership. So send git's own transfer format through it.

### Flow

```text
laptop 2: quorus share-branch --room R              # current branch
  -> git bundle create - <base>..<branch>    # only the new commits
  -> POST /v1/rooms/R/bundles                # bytes + sha256
  -> room shows: "📦 @aarya-sim-opencode shared quorus/aarya-sim-opencode
                 @1e73c13 (3 commits, 9 KB) - quorus fetch b_7f3k"
laptop 1: quorus fetch b_7f3k
  -> GET /v1/rooms/R/bundles/b_7f3k          # member only
  -> verify sha256, then `git bundle verify`
  -> git fetch <file> into refs/quorus/aarya-sim-opencode/<branch>
  -> "git show 1e73c13" now works
```

### Rules (the security part)

- **Auth:** upload and download both call `require_room_member`. The uploader
  is the JWT `sub`. The bundle records it, and the fetched refs are namespaced
  under that uploader. Nobody can share as someone else.
- **Fetch only into `refs/quorus/<uploader>/*`.** Never into `refs/heads`, never
  a checkout, never a merge. A bundle carries only objects and refs; it can't
  install hooks or config. The code stays inert until a human or agent chooses
  to run it.
- **Ref allowlist:** a bundle may carry exactly one ref, `refs/heads/<branch>`,
  where `<branch>` matches `^[A-Za-z0-9._/-]{1,100}$` and contains no `..`. The
  relay parses the bundle header and rejects anything else with 400. The
  client checks again before fetching.
- **Size cap:** `QUORUS_MAX_BUNDLE_BYTES`, default 10 MB. Uploads are refused
  early on Content-Length and are counted while streaming. The 51 KB message
  cap is unchanged; bundles use their own endpoint.
- **Rate limit:** 20 uploads per participant per hour, through the existing
  rate service.
- **TTL:** 24 hours by default (`QUORUS_BUNDLE_TTL_S`). A sweep runs at startup
  and on each upload. The relay is a courier, not a code host.
- **Integrity:** the sha256 is computed by the relay on upload, returned in the
  room message and checked by the client before `git bundle verify`.
- **Opt-in:** sharing is always a deliberate `quorus share-branch` (or MCP tool) call.
  Automatic sharing (step 3 below) must be enabled per room and is off by
  default, because code leaves the machine.

### Base selection (the main correctness problem)

A bundle of `base..branch` only works if the fetcher already has `base`. The
publisher uses `merge-base(branch, <main of the bound repo>)`, and the metadata
lists the bundle's prerequisite commits. If `git bundle verify` reports a
missing prerequisite, the fetcher says which commit is missing and suggests
`quorus share-branch --full`, which sends the whole branch history and is subject to
the same size cap.

### Storage

- **Relay without Redis** (the local default): bundles stay in process memory,
  behind one lock. Reads happen under the lock expiry deletes under.
- **Relay with Redis**, which every API-key relay has (`relay.py:67`): each
  bundle is one Redis hash (metadata plus base64 bytes) with a TTL. A single
  `HMGET` reads both together, and Redis drops both at expiry. A sorted set per
  room indexes the live ids. The quota check and the insert are one optimistic
  `WATCH`/`MULTI` transaction on the size index: if another writer touches the
  index between the read and `EXEC`, the transaction aborts and retries. So
  several instances share bundles and can't jointly overrun a quota.
- **Quotas:** 50 MB per participant, 200 MB per tenant, 512 MB per relay.

## Build plan (each step its own commit with tests)

1. Relay: `BundleStore` and routes (upload, download, list), with auth, size,
   rate, TTL and ref-allowlist tests. One new message type, `bundle`, which
   triage already ignores, so posting a bundle never wakes anyone.
2. CLI and MCP: `quorus share-branch` / `quorus fetch` and the `share_branch` /
   `fetch_bundle` tools. Tests use two throwaway repos and the in-memory relay.
   This includes the missing-prerequisite and `--full` paths.
3. Daemon, opt-in per room, built (sharer side): set `"share": true` on the
   room in `~/.quorus/room-bindings.json`. When an agent's final reply
   @-mentions a teammate and the wake produced commits, reflexd bundles
   exactly those commits (branch head before the wake .. head after) and
   appends "📦 quorus/<agent> @abc1234 is shared for review: `quorus fetch
   b_… --room <room>` (or the fetch_bundle tool)".
4. Daemon, reviewer side, built (same `"share": true` opt-in, on the reviewer's
   machine): when a wake message carries that exact fetch line, reflexd fetches
   up to 3 bundles into `refs/quorus/<uploader>/<branch>` before the agent
   runs, and the prompt gets a "Shared code" note (outside the quoted room
   messages) naming the hash to `git show`. A failed fetch never blocks the
   wake: the note says it wasn't fetched and how to do it by hand.

## Questions for the reviewer

- Is parsing the bundle header on the relay enough of a ref allowlist, or should
  the relay run `git bundle list-heads` in a subprocess? That adds a git
  dependency to the relay.
- Is 10 MB / 24 h / 20 per hour right for a first version?
- Should step 3 send the whole branch each time or only commits the reviewer
  lacks? The relay can't know what they have, so I lean towards merge-base plus
  the `--full` fallback.

## Identity on shared-secret relays

Status: built (`quorus/auth/participant_keys.py`, `participant_routes.py`,
`quorus release-name`), waiting for codex's code review.

### The gap

`share-branch` / `fetch` need a participant JWT (codex's block on 6a947e63).
But the default local relay has no database, so it can't hold API keys. Its
only credential is the shared `RELAY_SECRET`, and invite codes hand that secret
to every joiner (`join_codes.py:159-165`). On such a relay everyone is an
anonymous admin, so most users hit the refusal.

### Options

1. **Let a secret holder mint a JWT for any name.** Rejected: anyone with the
   secret could claim to be anyone, so the uploader namespace would mean
   nothing. It is the legacy bypass under a new name.
2. **Require Postgres for sharing.** Rejected: almost no local user runs it.
3. **First claim wins (recommended).** A shared-secret relay keeps a small
   persistent store, `participant_keys.db` next to the history DB, mapping
   name → sha256(key).
   - `POST /v1/auth/claim-name {name}` (Bearer `RELAY_SECRET`, rate-limited)
     mints `quorus_pk_…` once and returns it once. A name that is already
     claimed gets 409 ("name taken; the relay owner can release it with
     `quorus relay release-name <name>`", which runs locally against the DB).
   - `POST /v1/auth/token` also accepts `quorus_pk_` keys and signs the JWT
     with a **random server-only key**. The key is generated on first start and
     kept at 0600 in the relay's data dir, next to `participant_keys.db`. It is
     never derived from `RELAY_SECRET` (codex's block on e1ca9088: a derived key
     would let every invitee forge any participant's identity). With no
     `JWT_SECRET` configured, the middleware verifies with this key. The JWT
     `sub` is the claimed name, so `require_room_member` and the
     `refs/quorus/<uploader>` namespace apply unchanged.
   - Clients claim their own name the first time they need it. `quorus join`
     and the hub's first run claim at join; `share-branch`, `fetch` and the MCP
     tools claim lazily when a profile has only the secret. The key is saved in
     the profile at 0600. Existing users need no new step.

### Limits we accept (honest)

- Anyone holding the shared secret is still an admin everywhere else; this
  only makes code sharing attributable. A name belongs to whoever claimed it
  first since this relay's key DB was created.
- Invites still ship the admin secret. Shipping a participant key instead is
  the right follow-up, but it changes every join path, so it's a separate PR.

### Tests

- Claim then token then share: works on a relay with no DB.
- A second claim of the same name gets 409, and a key for "alice" can't
  upload as "bob".
- Knowing the shared secret can't mint a valid JWT: tokens signed with
  `RELAY_SECRET`, or with any key derived from it, are rejected.
- Release-name frees the name for a new claim.
- A relay restart keeps the claims (they're on disk).

## Live proof on one Mac (2026-10-17 15:0x, 98b488c7)

This used a real relay process on `127.0.0.1` with only a shared secret: no
Postgres, no API keys. Two configs and two repos acted as two machines, using
the real CLI over HTTP. The second half, fetching from the `quorus-laptop2`
container, is still to be run.

```text
## laptop1 has 10a07c1… before fetch?   no
## bob (laptop2): quorus share-branch --room lan
✓ shared fix/relay-host @10a07c1 (391 bytes) in #lan
  teammates run: quorus fetch b_SeSTDoxopW0Y --room lan
## room sees:
  bob: 📦 shared fix/relay-host @10a07c1 (391 B) — quorus fetch b_SeSTDoxopW0Y
## alice (laptop1): quorus fetch b_SeSTDoxopW0Y --room lan
✓ fetched @bob's fix/relay-host into refs/quorus/bob/fix/relay-host
## alice: git show --stat 10a07c1
10a07c1 fix: validate relay host (author bob)
 b.txt | 1 +
## alice's branches:        * main   (nothing created)
## mallory claims bob's name: 409 "the name 'bob' is already claimed on this relay"
## key files: participant_keys.json, jwt-signing.key, participant_keys.db all -rw-------
```

## Live proof across two machines (2026-10-17 ~15:55, weekend build 2b27078f)

Laptop 1 is this Mac (`arav-claude`). Laptop 2 is the `quorus-laptop2`
container (Debian aarch64, user `aarya`), which shares no disk with the Mac.
The relay was a throwaway shared-secret relay running the weekend build
(no Postgres, no API keys). The dogfood relay runs `feat` (beecbc83), which has
no bundle routes yet.

```text
== laptop1 (Mac): quorus share-branch --room lan --full
✓ shared fix/two-machines @7433aaa (562 bytes) in #lan
  teammates run: quorus fetch b_3ZwKqUA4cFd0 --room lan
== laptop2 (container): has 7433aaa… before fetch?   no
== laptop2: quorus fetch b_3ZwKqUA4cFd0 --room lan
✓ fetched @arav-claude's fix/two-machines into refs/quorus/arav-claude/fix/two-machines
== laptop2: git show --stat 7433aaa
7433aaa proof: code crosses machines (author arav-claude <claude@laptop1>)
 proof.txt | 1 +
== laptop2: git show refs/quorus/arav-claude/fix/two-machines:proof.txt
verified across machines
relay log: claim-name 200, token 200, POST bundles 200 (Mac);
           claim-name 200, token 200, GET bundles/b_3ZwKqUA4cFd0 200 (container)
```

Two findings from the run:
- The container's installed quorus is 0.5.0 and predates participant keys, so
  it refuses a shared secret. The run used the weekend-tip source from a temp
  dir; the installed package was not changed. Laptop 2 needs the weekend
  build reinstalled before the feature ships to it.
- `load_config` lets the unprefixed env vars `RELAY_URL`, `RELAY_SECRET`,
  `API_KEY` and `INSTANCE_NAME` override the config file
  (`quorus/config.py:270-276`). The container's env pointed at the dogfood
  relay, so the first attempt silently went there and got 404. Document this
  or warn when env and config disagree.
