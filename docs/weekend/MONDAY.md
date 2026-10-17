# Monday memo: Quorus weekend (updated Sat 2026-10-17 evening)

From the room lead (`arav-claude`). The overseer's go/no-go is
[LAUNCH.md](LAUNCH.md). This page lists what needs you and what the room
shipped. `weekend/quorus` at 44409868 passes the full suite (2595) with ruff
clean. `feat/wake-rebuild-aug26` is fully merged into it.

## Needs you

1. **LAUNCH.md rows 9 and 10.** Set up the PyPI trusted publisher, and decide
   whether the business docs (YC/FINC applications, tweets, run cards) move out
   of the public repo before launch.
2. **Pull in `weekend/laptop2`.** These commits from the second-user container
   are on that branch only, not `weekend/quorus`:
   - d52acc52 + 1e73c13d + b615f6b7: pin the join installer to its relay and
     validate the relay host. 1e73c13d closes a shell injection in `curl | sh`;
     never take d52acc52 without it.
   - ff11cd9b (`ps` timestamps), 494be38a + cb3c03df (presence attribution),
     39f75e12 (killed agents release their claims).
   - 7abc7656 + df118c31: five teardowns with line-cited sources.
   All were reviewed in the room from pasted diffs. Expect conflicts in
   `quorus/routes/room_state.py` and `join_codes.py`.
3. **Drop five junk commits:** 455a0082, 0d62ffd0, 138a0e4e, 8bb2695d,
   3df10c21. Each is only a `graphify-out/` refresh committed with a chat line
   as its subject. cbfc6578 + a4e110c9 stop new ones.
4. **Restart the dogfood relay and the daemons on the weekend build**, and
   reinstall quorus on laptop2: it still has 0.5.0. Until then, none of
   Saturday's daemon or relay fixes are live. The relay still lacks the bundle
   routes, and the daemons still lack participant-key sign-in and failover.

## Shipped Saturday (proofs in LAUNCH.md)

- **Code across machines:** `quorus share-branch` / `quorus fetch` and the
  MCP tools `share_branch` / `fetch_bundle`. A branch travels through the relay
  as a git bundle and lands as a read-only `refs/quorus/<uploader>/<branch>`.
  Proven Mac → laptop2 container. Rooms can opt in (`"share": true`) to attach
  a bundle when an agent asks a teammate for review.
- **Participant keys on shared-secret relays.** First claim of a name wins.
  JWTs are signed with a random server-only key and checked against the claim's
  generation, so releasing a name retires its old tokens. Daemons sign in this
  way and fail closed rather than fall back to the admin secret.
- **Failover proven live,** including after restarting the failing daemon
  (`docs/weekend/proofs/failover_proof_isolated.py`, no quota spent).
  Autonomous rooms now fail over too.
- **Smaller fixes (not yet reviewed by another vendor):**
  - `quorus doctor` flags env vars that override a different config value.
  - Retro counts a review verdict as delivery.
  - The launch post leads with the scorecard and uses real numbers.

## Still open

- Codex is reviewing dc24ee6c (fail-closed sign-in) and 44409868 (review
  bundles).
- Reviewer-side auto-fetch before a wake is not built: the reviewer's agent
  fetches with the MCP tool.
- `reflexd.py` is about 3.9k lines; the 500-line rule says split it.
- `docs/weekend/research/paperclip.md` and `agent_mail.md` are still the
  rejected f19164a versions; `landscape.md` supersedes them. Delete them, or
  take laptop2's teardowns.
