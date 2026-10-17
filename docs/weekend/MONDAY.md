# Monday memo: Quorus weekend (2026-10-17)

From the room lead (`arav-claude`). Every commit below was reviewed by an agent
from a different vendor than its author. Each was checked with the full suite
outside the agent sandbox: 2422 passed, ruff clean at e31efd10.

## Decide first

1. **Merge `feat/wake-rebuild-aug26` and `weekend/quorus`.** The live daemons
   run `feat` from `~/dev/Quorus`, so none of the weekend's daemon fixes are
   live. That includes failover, commit subjects, graph exclusion and exit
   reasons. `feat` also overlaps with weekend work: 7c66130 (quota pause)
   vs 8e41e88 (failover), 3973786 (silence receipts) vs ideas.md #2, and
   315f575 vs the installer fix. Codex is queued to fold failover's `degraded`
   state into 7c66130's `limited_until` once both branches are merged.
2. **Pull in `weekend/laptop2`.** The second-user container's commits only
   exist there, not in this object store. They are: d52acc5 + 1e73c13 + b615f6b
   (installer and signup relay-URL injection), ff11cd9 (`ps` timestamps),
   494be38 + cb3c03d (presence attribution), 39f75e1 (killed agents release
   their social claims), and df118c3 (five teardowns with line-cited sources).
   I approved them from pasted diffs and checked every teardown link.
3. **Drop the junk commits** by `arav-claude`: 455a0082, 0d62ffd0, 138a0e4e,
   8bb2695d and 3df10c21. Each is only a `graphify-out/` refresh committed
   by the daemon with a chat line as its subject. 01f92b3 + a4e110c stop this,
   once they are live.
4. **Replace f19164a's paperclip.md and agent_mail.md.** These were rejected:
   no sources, and an invented Ed25519 claim. Gemini is rewriting them with
   citations. Until then, treat those two files as unreviewed.

## Fix on `feat` (only you can; the code isn't on this branch)

- **Silence receipt spam.** "read it, nothing to add" posted 4-6 times in a row.
  Post it only for a direct mention or DM, at most once per agent per human message.
- **Repeat wakes.** Agents were re-woken for mentions they had already
  answered, and each repeat produced another receipt.
- **The graphify SessionStart hook** rewrites `graphify-out/` in agent
  worktrees after the agent's last check. Disable it for agent sessions.
- **The Quorus MCP returned 401** "Invalid API key format" for `arav-claude` and
  `arav-opencode` from about 02:34; it worked again by 04:45.

## What landed on `weekend/quorus`

- **Reliability:** tests are isolated from a woken agent's env (7296017).
  Crashes and timeouts (33ad026) and unknown exits (e31efd10) now say why in the
  room. Vendor failover hands the task to another harness via typed `/defer`,
  with a two-hop limit (8e41e88). Claude's `is_error` results reach that path
  (a484f40). Daemon commits get conventional subjects and skip graph files
  (01f92b3, a4e110c).
- **First run:** relay errors name the cause (61b3446, b3a2bc5, 04c83df).
  `init` keeps the hub's profile layout and checks the secret or API key before
  writing. It keeps a `.bak`, and its key exchange never follows redirects
  (0c9a5e2, 8726ee5, 4b46e18, e40c951). Six confusing first-run lines are fixed
  (be0acacc). Findings are in `readme-quickstart-run.md`.
- **Docs:** `ideas.md` has 3 ideas with prior art and honest scores (3-6/10).
  `failover-spec.md` is the spec for 8e41e88. `marketing/` holds drafts only,
  every claim tied to evidence in `review-notes.md`. `demo.md` tells the installer
  injection story jointly (laptop 2 found and fixed the bug, review found the
  injection).

## Not done or not proven

- Failover is proven by tests only. A live vendor outage never reached it,
  because the daemons run `feat`.
- A fresh install through to an agent answering, in one try, is unproven. The
  container run is assigned to `aarya-sim-opencode`.
- `work_queue_svc.expire()` is still never called, so a killed agent's task stays
  `in_progress` on the board. Assigned to `aarya-sim-opencode`.
- `reflexd.py` is still about 3.2k lines. I did not split it this weekend: with
  `feat` diverged, a split would turn Monday's merge into a rewrite.
