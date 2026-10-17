# Launch go/no-go (overseer, Sat 2026-10-17)

Every line has evidence. "GO" means verified this weekend, not believed.

| # | Check | Status | Evidence |
|---|---|---|---|
| 1 | Auto-wake, replies and handoffs work on Claude, Codex, Gemini and opencode | GO | All four replied within 22s; `@open` claimed and shipped in 37s; review handoff; multi-room (Fri night run). |
| 2 | Proactive and visible: presence, live steps, visible silence, alerts | GO | Live steps on all four harnesses (tests/test_live_steps.py; live claude run 22s, demo run). Silence and pause notices; desktop alerts on shipped, needs you, stuck. |
| 3 | Loops stay inside the owner's plan | GO | Pacing, quota cooldowns (parses "retry in" and "try again at"), wake attempt limit, opencode free-model failover. |
| 4 | Fresh install on a clean machine | GO, with a gap | Wheel installs on clean Debian (laptop2); `quorus` setup plus agent add in 12s. **Gap:** with no paid AI CLI, the first fix depends on opencode's free tier (7 min on our IP, which our own agents had used up). With Claude or Codex installed: ~22s. |
| 5 | Zero-key trial | GO, flaky by vendor | `quorus demo`: fix on main 33s, approved review 67s (06:15 run). It probes the free models and says so when all are busy. |
| 6 | CI green on the whole change set | GO | Draft PR #1 (feat → main): all 24 checks pass (tests on Python 3.10-3.13, Redis and Postgres integration, cold install on macOS, Ubuntu and Windows). The first run caught a real bug: ledger notes failed without a git identity (fixed b967ede6). |
| 7 | Security review before launch | GO | Audit fixes 9d0fc0e5, 430cb716, c5740e80, b8473f6c, 51cf8f59. README "Security model" states the residual risks: shared-secret mode can forge approvals, a join code is the secret, autonomous agents aren't sandboxed. |
| 8 | README is accurate | GO | README text is under test; updated 5ba442df for live steps, scorecard, duel, retro, replay, phone. |
| 9 | PyPI name and publishing | NEEDS ARAV | `quorus-ai` is free on PyPI (404). `.github/workflows/publish.yml` publishes on `v*` tags via trusted publishing, but the trusted publisher on pypi.org is **not** set up. Arav must add it (PyPI → Publishing → GitHub: Quorus-dev/Quorus, workflow publish.yml) and say go. |
| 10 | Public repo hygiene | NO-GO until decided | The public repo has docs/YC_S26_APPLICATION_v8.md, FINC_APPLICATION_v8.md, LAUNCH_TWEET_v8.md, MARKETING_LAUNCH_TWEET.md and KLIMENT_DEMO_RUNCARD.md. Business plans shouldn't ship with a launch: move them to a private repo (Arav's call). |
| 12 | Vendor failover (slate item 6) | GO pending Codex review | Live proof re-run by the overseer at 12:07 on 397dd2db/6c4982ef: the rate-limited agent's `/defer` returns 200 and the teammate wakes with reason=vendor failover and answers in 42s. Restart case and `default`-only gate fixed (1152f5c9, 96e4f946, dc24ee6c; Codex approved 2b27078f and the gate); `docs/weekend/proofs/failover_proof_isolated.py` passes twice, including after restarting the failing daemon, with no 409 and no shared-secret fallback. dc24ee6c awaits Codex. |
| 13 | Code across machines (`quorus share-branch` / `fetch`, MCP tools) | GO pending final review | Two-machine proof (43aebd9b, docs/weekend/cross-machine-code.md): this Mac shared a branch through a throwaway shared-secret relay on the weekend build (the dogfood relay runs `feat`, which lacks the routes); the quorus-laptop2 container (no shared disk) lacked the commit, fetched it into refs/quorus/arav-claude/*, and showed it. Codex approved 8803e747, 89529081, d8b41697, 2b27078f (reviewed as 7ebd213d/3eb4f615 before rebase); dc24ee6c and 44409868 (opt-in review bundles) under review. |
| 14 | Blind cross-vendor duel (`quorus duel`) | GO, one live run | First live duel (sample repo, 7ce31c2d): Claude 34s and Codex 33s, both with passing tests; Gemini judged blind and picked Claude; Claude merged, Codex kept on its branch; recorded in the ledger. One run is an anecdote, not a benchmark. |
| 11 | Traction | NOT STARTED | 0 stars. Drafts in docs/weekend/marketing/ (launch post, demo script, communities list). Posting needs Arav logged in; agents never post. |

## The pitch that survives the competition
"Group chat for agents" alone is ~4/10 novel (Paperclip ~103k stars). What no one else does:
**measure which vendor's work holds up in YOUR repo** (`quorus scorecard`), settle it head to head
(`quorus duel`), and see every agent's step live. Lead with that, demo with `quorus demo` and a
`quorus replay` page.

## Order of operations once Arav says go
1. Arav reviews and merges PR #1 (CI green), and decides line 10.
2. Arav sets up the PyPI trusted publisher.
3. Tag `v0.5.0` on the merged main commit; `publish.yml` uploads; verify `pipx install quorus-ai`
   on a clean machine and run `quorus demo`.
4. Switch the README install line to `pipx install quorus-ai`.
5. Post from the drafts (Arav's accounts).
