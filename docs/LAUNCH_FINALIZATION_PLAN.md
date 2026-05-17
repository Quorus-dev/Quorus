# Quorus — Launch Finalization Plan (v9)

**Owner:** Arav · **Drafted:** 2026-05-16 · **Status:** active execution
**Goal:** Quorus is publicly publishable + advertisable. Show-HN, Twitter launch, YC S26 submission, paid customers.

---

## 0. Verified state today

| Surface                             | State                                                                                   | Evidence                                                                     |
| ----------------------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Local dev imports                   | **FIXED today**                                                                         | `python -c "import quorus,..."` succeeds after `scripts/fix_editable_pth.sh` |
| Test baseline                       | 529 pass / 0 fail in 7.92s on changed surface; ~1924 total in repo                      | `pytest -q`                                                                  |
| Ruff                                | Clean (per 2026-05-11 audit)                                                            | `ruff check .`                                                               |
| Prod relay                          | **Behind by one deploy** — 64 endpoints, 0 of {memory, tools, capabilities}             | `curl https://quorus-relay.fly.dev/openapi.json`                             |
| PyPI `pip install quorus`           | **Actively broken** — serves `0.1.0` (Sept 2025), current local is `0.4.0`              | `curl https://pypi.org/pypi/quorus/json`                                     |
| quorus.dev                          | Vite SPA live, title + OG decent, content unverified via curl (client-rendered)         | `curl -sL https://quorus.dev/`                                               |
| Asciinema cast                      | 388-byte placeholder, "(placeholder)" caption hardcoded                                 | `ls -la website/public/casts/demo_reflex.cast`                               |
| Last real commit                    | `850dbf9` (May 7 — Phase 1 OS primitives)                                               | `git log`                                                                    |
| In-flight today (uncommitted local) | Dashboard XSS fix (16 tests), HSTS+Referrer-Policy (5 tests), .pth fix script (2 tests) | `git status`                                                                 |

**Bottom line:** Quorus is not yet publishable. The product works locally for a developer who runs the fix script, but every prospective user hitting `pip install quorus` today gets the wrong package, and every visitor to quorus.dev sees a placeholder demo.

---

## 1. Launch gates

There are three gates. Each gate is binary — every item in the gate must be true before the gate is "open."

### Gate A — **SHIP GATE** (must close before any public link)

Failing any of these means a real user gets hacked, embarrassed, or sent down a broken path on first contact.

| #   | Item                                                                                                                     | File / surface                                                                                                    | Owner                            | Status                         |
| --- | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- | -------------------------------- | ------------------------------ |
| A1  | Stored XSS via `reply_to` closed end-to-end                                                                              | `quorus/routes/models.py`, `quorus/dashboard.py`, `tests/test_xss_reply_to.py`                                    | claude-1m                        | ✅ shipped today (uncommitted) |
| A2  | HSTS + Referrer-Policy + existing X-Frame/CSP headers                                                                    | `quorus/relay.py:582`, `tests/test_security_headers.py`                                                           | claude-1m                        | ✅ shipped today (uncommitted) |
| A3  | Editable-install `.pth` fix permanent + regression-tested                                                                | `scripts/fix_editable_pth.sh`, `tests/test_editable_pth_fix.py`, `CONTRIBUTING.md`                                | claude-1m                        | ✅ shipped today (uncommitted) |
| A4  | JWT cookie flow replaces `?token=` on `/stream/*` SSE                                                                    | `quorus/dashboard.py:543-545`, `quorus/routes/stream.py` (or wherever SSE auth lives)                             | claude-1m                        | 🟡 in progress                 |
| A5  | Audit-BEFORE-mutation invariant on Phase 1 routes; remove silent `except: pass`                                          | `quorus/routes/persistent_memory.py:167`, `tool_catalog.py:159`, `capabilities.py:147`                            | claude-1m                        | ⬜                             |
| A6  | Outbox retry-storm fix — Alembic migration + `_claim_entries` filter + `_handle_failure` writes `next_attempt_at`        | `quorus/services/outbox_svc.py:375-395`, new Alembic migration                                                    | codex                            | ⬜                             |
| A7  | `fly deploy` of Phase 1 routes — prod `openapi.json` shows `/v1/memory/*`, `/v1/rooms/.../tools/*`, `/v1/capabilities/*` | Fly.io app `quorus-relay`                                                                                         | codex (has prod)                 | ⬜                             |
| A8  | PyPI: either yank `0.1.0` and publish current `0.4.0`, or remove `pip install quorus` from all docs/CTAs                 | `pyproject.toml`, `website/src/components/CTADark.tsx:18`, `QuickstartBand.tsx:31`, `README.md`                   | arav                             | ⬜                             |
| A9  | "6 harnesses verified" → "4 verified + 2 argv-pinned" everywhere                                                         | `docs/QUORUS_OS_SPEC.md:32-40`, `website/src/data/cross_harness_copy.ts:16`, `README.md`                          | claude-1m (Path A)               | ⬜                             |
| A10 | Real asciinema cast recorded (≥20s, real cross-vendor demo) + remove `"(placeholder)"` caption                           | `website/public/casts/demo_reflex.cast`, `website/src/components/AsciinemaPlayer.tsx:52`                          | arav (the recording is hands-on) | ⬜                             |
| A11 | License contradiction resolved — pick Apache-2.0, sweep MIT references                                                   | `index.html:11`, `website/src/components/FooterV2.tsx:215`, `website/src/components/PricingFaq.tsx:25`, `LICENSE` | claude-1m                        | ⬜                             |
| A12 | Fake model names in HeroRoom replaced with real ones (or removed)                                                        | `website/src/components/HeroRoom.tsx:32-35`                                                                       | claude-1m                        | ⬜                             |
| A13 | ComparisonBand mounted on `/`; `id="waitlist"` + `id="features"` anchors land                                            | `website/src/pages/Home.tsx`, `HeroLight.tsx`, `BentoStitch.tsx`                                                  | claude-1m                        | ⬜                             |

**Acceptance:** Run `bash scripts/launch_smoke.sh` (to be written) — passes only when A1-A13 are all true. Until that passes, no public link goes out.

### Gate B — **POLISH GATE** (must close before HN front page)

A1-A13 close → Quorus is _safe_ to share. B1-B10 close → Quorus is _good_ enough to win HN.

| #   | Item                                                                         | Why it matters                                                             |
| --- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| B1  | 4-agent autonomous-PR demo recording (90-120s)                               | Show-HN headline asset; the single artifact that makes "agent OS" tangible |
| B2  | `quorus reflex status` CLI command (Codex audit task #21)                    | Reviewers test the daemon — needs a "is it alive?" command                 |
| B3  | Codex CLI real-LLM smoke test passing in CI weekly                           | Sustains the marketing claim under scrutiny                                |
| B4  | DM `/messages` rate-limit added                                              | Trivial DoS surface flagged by audit                                       |
| B5  | `_get_client_ip` used in auth-failure logs (currently `request.client.host`) | Forensics gap — log shows proxy IP not real client                         |
| B6  | Dashboard `noindex` meta + un-auth route removed from public reach           | Dashboard is admin surface; Google should not crawl it                     |
| B7  | Quickstart copy aligned with single working install command                  | Reviewers won't tolerate 2 inconsistent install lines                      |
| B8  | OG image is a real 1200x630 PNG render of HeroRoom                           | Twitter/LinkedIn share preview                                             |
| B9  | TUI 200-message cap + SIGWINCH handler (audit MEDIUM)                        | Live demo will resize windows                                              |
| B10 | Footer dead-link sweep (Discord, blog, about)                                | Either populate or delete                                                  |

### Gate C — **POST-LAUNCH** (weeks +1 to +4)

Real product debt and roadmap that doesn't block ship but matters for survival.

| #   | Item                                                                                                                                               |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| C1  | File-size refactors: `packages/cli/quorus_cli/cli.py` (9,140 → <500 each), `packages/tui/quorus_tui/hub.py` (3,826), `tests/test_relay.py` (3,006) |
| C2  | Lift `_request_with_refresh` from `phase1_tools.py` to shared `tools.py`; remove `# noqa: E402` on imports                                         |
| C3  | Cursor + Cline real-LLM CI lane to convert "argv-pinned" to "verified"                                                                             |
| C4  | Phase 0 LiveBench seed — SWE-bench Verified runs for 4 vendors with public dashboard                                                               |
| C5  | Phase 1 — per-tenant data pipeline + private router                                                                                                |
| C6  | Phase 2 — Atomic Action Capsules + `quorus undo`                                                                                                   |
| C7  | YC S26 application submitted (deadline: confirm with arav)                                                                                         |
| C8  | O-1 visa evidence-building — Quorus traction artifacts captured monthly                                                                            |
| C9  | Show-HN post + Twitter launch thread                                                                                                               |
| C10 | Cold outreach to OpenAgents, TAP, AutoGen maintainers — pitch QSP v1 as a standard                                                                 |

---

## 2. Owner map (who does what)

| Lane                                   | Owner            | Responsibility                                                                                 |
| -------------------------------------- | ---------------- | ---------------------------------------------------------------------------------------------- |
| Security PR (A1, A2, A4, A5, A12, A13) | claude-1m        | Already in flight. PR contains audit-before-mutation rewrite + JWT cookie flow + CSP holdover. |
| Correctness PR (A6) + prod deploy (A7) | codex            | Outbox retry-storm fix (lane he already shipped) + Fly deploy auth he holds.                   |
| Marketing truth (A8, A9, A10)          | arav             | PyPI publish decision is yours (legal/billing); asciinema recording is hands-on.               |
| Website polish (A11, A13, B7, B8, B10) | claude-1m        | Trivial copy + JSX edits.                                                                      |
| Demo recording (B1)                    | arav + claude-1m | Arav drives the actual 4 agents in a room; claude scripts the demo flow.                       |
| Productization (B2, B3, B9)            | codex            | Cedar/CLI lane he already owns.                                                                |
| Audit forensics (B4, B5, B6)           | claude-1m        | One-line fixes per the audit.                                                                  |

---

## 3. Sequence — 5 working days to Gate A closed

Assumes Arav, claude-1m, and codex working in parallel. Cache-warm sessions; <90 min context between commits.

### Day 1 (today, 2026-05-16) — what just happened

- ✅ A1 dashboard XSS fix shipped locally + 14 regression tests
- ✅ A2 HSTS + Referrer-Policy shipped locally + 5 regression tests
- ✅ A3 `.pth` fix script + 2 regression tests + CONTRIBUTING.md update
- 🟡 A4 JWT cookie work started

### Day 2

- claude-1m: finish A4 (JWT cookie + remove `?token=` on SSE) and A5 (audit-before-mutation rewrite of Phase 1 routes, single-transaction)
- claude-1m: ship A9 + A11 + A12 + A13 in same security PR (one commit per fix, atomic)
- arav: relay codex message (verbatim below)
- codex (in parallel): take A6 — outbox migration + `_claim_entries` filter + regression test
- **End of day: open PR titled `feat(launch): close 10 launch blockers`**

### Day 3

- codex: merge A6, deploy A7 (`fly deploy`), verify Phase 1 routes live on prod
- arav: decide A8 (PyPI yank-and-republish OR remove from copy) + execute
- arav: record A10 asciinema cast (target 30s, claude-1m will write the demo flow script)
- **End of day: Gate A closed. Run `bash scripts/launch_smoke.sh` → all green.**

### Day 4

- claude-1m + arav: record B1 4-agent autonomous-PR demo (Loom + asciinema backup, 5 takes)
- codex: B2 (`quorus reflex status`) + B3 (Codex CLI real-LLM smoke in CI)
- claude-1m: B4-B10 (audit forensics + footer + OG image + TUI)

### Day 5

- arav: write Show-HN draft + Twitter thread (use `product-engineer` agent for narrative)
- claude-1m: final dry run of demo + smoke tests + visual QA
- **End of day: launch-ready. Post to HN Day 6 morning (07:00 PT).**

---

## 4. Codex relay message (paste verbatim into the room)

```
@arav-codex — pre-launch audit confirms 10 blockers across security + design.
Splitting the work as follows; your lane (you own outbox + have prod deploy):

1. CORRECTNESS: outbox retry-storm at quorus/services/outbox_svc.py:375-395.
   _handle_failure computes ``delay`` but never persists it; _claim_entries
   doesn't filter next_attempt_at. Need Alembic migration adding
   ``next_attempt_at TIMESTAMP`` + index on (status, next_attempt_at),
   then _claim_entries filters ``next_attempt_at <= now()`` and
   _handle_failure sets ``next_attempt_at = now() + delay``. Regression
   test: simulate 503 downstream, assert retries follow exp-backoff curve.

2. DEPLOY: once my security PR lands on feat/may4-sprint, please run
   ``fly deploy``. Verify with
   ``curl https://quorus-relay.fly.dev/openapi.json | jq '.paths|keys[]' |
     grep -E 'memory|capabilities|tools/'``
   — must list the Phase 1 routes. Post the new image version in the room.

3. AUDIT LEDGER: please confirm whether
   ``REVOKE UPDATE, DELETE ON audit_ledger`` exists at the DB level.
   Audit found no such migration. If absent, please add (this is your
   Task #32 — hash-chained audit ledger).

4. CURSOR + CLINE: do you have either CLI auth'd on your box? If yes,
   run ``pytest -m real_harness -k cursor or cline`` and paste results.
   Otherwise I'm changing marketing copy from "6 verified" to
   "4 verified + 2 argv-pinned" on Day 2.

5. RELEASE: ``quorus reflex status`` CLI subcommand from your earlier
   audit — please ship by Day 4.

claude-1m owning: dashboard XSS (shipped), security headers (shipped),
.pth fix script (shipped), JWT cookie flow, audit-before-mutation on
Phase 1 routes, all website copy/visual fixes. Will tag you for review
before merging the security PR.

Target: Gate A closed by EOD Day 3; HN launch Day 6.
```

---

## 5. Acceptance criteria — Gate A smoke test (to be written)

`scripts/launch_smoke.sh` (new, will write on Day 3 before closing Gate A):

```bash
#!/bin/bash
# Gate A acceptance — every assertion must pass before any public link.
set -e

# A1-A3 — local hygiene
pytest tests/test_xss_reply_to.py tests/test_security_headers.py \
       tests/test_editable_pth_fix.py -q

# A4 — JWT cookie flow
pytest tests/test_dashboard_auth_cookie.py -q

# A5 — audit-before-mutation on Phase 1 routes
pytest tests/test_phase1_audit_invariant.py -q

# A6 — outbox retry-storm
pytest tests/test_outbox_retry_storm.py -q

# A7 — prod deploy parity
curl -sf https://quorus-relay.fly.dev/openapi.json \
  | python3 -c "import sys,json; p=json.load(sys.stdin)['paths']; \
    assert any('/v1/memory' in k for k in p), 'prod missing memory routes'; \
    assert any('/v1/capabilities' in k for k in p), 'prod missing capabilities'; \
    assert any('/tools/' in k for k in p), 'prod missing tool_catalog'; \
    print('prod parity OK')"

# A8 — PyPI consistency (either yanked or current)
python3 -c "import urllib.request, json; \
  d = json.load(urllib.request.urlopen('https://pypi.org/pypi/quorus/json')); \
  v = d['info']['version']; \
  assert v.startswith('0.4') or 'yanked' in str(d['releases']), \
    f'PyPI serving stale {v}'; print(f'PyPI version OK: {v}')"

# A9 + A11 + A12 — marketing copy honest
! grep -rni "6 verified\|six.*harness.*verified" \
    website/src docs/QUORUS_OS_SPEC.md README.md \
  || (echo "FAIL: '6 verified' claim still present"; exit 1)
! grep -rni "MIT.*licen[sc]e\|licen[sc]ed under MIT" \
    website/index.html website/src \
  || (echo "FAIL: MIT mention still present"; exit 1)
! grep -rn "claude-sonnet-4-6\|gpt-5" website/src/components/HeroRoom.tsx \
  || (echo "FAIL: fake model names in HeroRoom"; exit 1)

# A10 — asciinema cast is real
test "$(wc -c < website/public/casts/demo_reflex.cast)" -gt 5000 \
  || (echo "FAIL: asciinema cast is still placeholder size"; exit 1)
! grep -q "placeholder" website/src/components/AsciinemaPlayer.tsx \
  || (echo "FAIL: 'placeholder' caption still in AsciinemaPlayer"; exit 1)

echo "Gate A: all 13 blockers closed. SHIP."
```

---

## 6. Risk register

| Risk                                                       | Mitigation                                                                                                |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Codex doesn't pick up message → Day 3 slip                 | claude-1m takes A6 + A7 as fallback on Day 3 morning if no codex commit by 09:00                          |
| PyPI `0.4.0` publish reveals hidden import-path issues     | Run `scripts/cold_install_smoke.sh` on fresh Mac + Linux VM before publishing                             |
| Asciinema recording fails mid-take                         | 5 takes minimum; Loom backup; pre-write the demo flow script (claude-1m)                                  |
| HN front page → relay overload                             | Fly autoscale already set; verify by `flyctl scale show` before launch; have a circuit-breaker post ready |
| Show-HN community latches onto "QSP novelty=6/10" critique | Pre-rebuttal: emphasize 4-verified real-LLM end-to-end demo; QSP is the bet, not the proof                |

---

## 7. What "perfected" means here

This plan is "perfected" when every row in Gate A has either ✅ or a named owner with a committed ETA, when codex has acknowledged the relay message, when the smoke script exists and passes, and when the asciinema recording is a real cross-vendor 4-agent demo (not a sales pitch). That's the bar — not zero bugs, not zero technical debt, not feature-complete. It's "no user, customer, investor, or HN commenter will catch us lying or shipping broken."
