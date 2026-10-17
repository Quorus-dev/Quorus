# Code across machines: design (slate item 4)

Status: design for review by `@arav-codex`. Nothing is built yet.

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
laptop 2: quorus share --room R              # current branch
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
- **Opt-in:** sharing is always a deliberate `quorus share` (or MCP tool) call.
  Automatic sharing (step 3 below) must be enabled per room and is off by
  default, because code leaves the machine.

### Base selection (the main correctness problem)

A bundle of `base..branch` only works if the fetcher already has `base`. The
publisher uses `merge-base(branch, <main of the bound repo>)`, and the metadata
lists the bundle's prerequisite commits. If `git bundle verify` reports a
missing prerequisite, the fetcher says which commit is missing and suggests
`quorus share --full`, which sends the whole branch history and is subject to
the same size cap.

### Storage

There is a `BundleStore` protocol with two implementations:
- in-memory, for tests;
- file-backed, next to the SQLite history DB as
  `<data_dir>/bundles/<tenant>/<id>`, written atomically with mode 0600.

Known limit: a multi-instance relay without a shared disk (Fly with Postgres)
needs object storage later. Until then the relay returns 501 there instead of
losing bundles silently.

## Build plan (each step its own commit with tests)

1. Relay: `BundleStore` and routes (upload, download, list), with auth, size,
   rate, TTL and ref-allowlist tests. One new message type, `bundle`, which
   triage already ignores, so posting a bundle never wakes anyone.
2. CLI and MCP: `quorus share` / `quorus fetch` and the `share_branch` /
   `fetch_bundle` tools. Tests use two throwaway repos and the in-memory relay.
   This includes the missing-prerequisite and `--full` paths.
3. Daemon (opt-in per room): when an agent's final reply names a commit and
   @-mentions a reviewer on another machine, attach a bundle. The reviewer's
   daemon fetches it before the wake, so `git show <hash>` works in the prompt.

## Questions for the reviewer

- Is parsing the bundle header on the relay enough of a ref allowlist, or should
  the relay run `git bundle list-heads` in a subprocess? That adds a git
  dependency to the relay.
- Is 10 MB / 24 h / 20 per hour right for a first version?
- Should step 3 send the whole branch each time or only commits the reviewer
  lacks? The relay can't know what they have, so I lean towards merge-base plus
  the `--full` fallback.
