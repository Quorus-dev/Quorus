# Release verification: v0.5.0 (PR #3 wheel)

Commit links point at `release-fixes` (the fixes cherry-picked onto
`release/0.5.0`); weekend/quorus holds the same changes under other hashes.

Every advertised agent and command, run for real on the release wheel
(`release/0.5.0`, [801fd5db](https://github.com/Quorus-dev/Quorus/commit/801fd5db)) on 2026-10-17. "Real" means the vendor's own CLI
answered through a private relay and a reflexd daemon. No stubs unless the row
says so.

## Agents (real CLIs)

| Agent | Model | Result | Time to reply | Notes |
|---|---|---|---|---|
| Claude Code 2.1.302 | default | PASS | 23s | |
| Codex CLI 0.160.1 | default | PASS | 17s | |
| Gemini CLI 0.64.0 | gemini-3.5-flash-lite | PASS | 15s | Free key from AI Studio. gemini-3.8-flash allows only 20 requests/day on the free tier. |
| opencode 2.0.30 | ollama/qwen3:4b (local Qwen) | PASS | 132s | Needs `ollama serve`; slow on a laptop but correct. |
| opencode 2.0.30 | opencode free tier | RATE-LIMITED | n/a | All free models but one answered "Rate limit exceeded" (per-IP, after a heavy day). The daemon tried each one, posted a clear room line, waited out the quota and sent a stuck alert. |
| opencode 2.0.30 | nemotron-3.5-lightning-free | SLOW (provider) | 111s to >300s | Plain `opencode run` takes 111s to its first token, without Quorus flags; beam-free takes 1.2s. Fix 9: watchdog plus rotation. |
| opencode 2.0.30 | beam-free (live catalog) | PASS | 1.2s raw | Demo builder: fix on main in 29s. |
| opencode | xai/grok-* (Grok) | NOT VERIFIED LIVE | n/a | No xAI key on this machine. With no key, opencode says `provider.no-route`, but the room only saw "exited 1 with no known cause" (fix 8). |

## Platforms

| Path | Result | Notes |
|---|---|---|
| macOS fresh HOME: init, relay, create, agent add (launchd), say, ps, remove | PASS | `ps` shows `degraded` when the free tier is limited. |
| Linux container (Debian, Node, opencode 2.x): init, relay, create, agent add (pid daemon), wake, ps, remove, doctor | PASS | The daemon stops on `agent remove`; doctor all green. |

## Release-critical features

| Feature | Result | Evidence |
|---|---|---|
| Vendor failover (`/defer`) | PASS | Handoff in 10s, `reason=vendor failover`, defer 200. |
| Needs-you / shipped / stuck alerts, all four vendors | PASS | Stub harnesses in real output formats; live alerts fired today for Claude, Codex and Gemini (shipped) and for all four (stuck). |
| `quorus duel` | PASS | Claude beat Codex, judged blind by Gemini, merged, provenance note recorded. |
| `quorus demo` | PASS after fixes | Failed when the free list was busy. With [476280b2](https://github.com/Quorus-dev/Quorus/commit/476280b2) plus [477c31bf](https://github.com/Quorus-dev/Quorus/commit/477c31bf) it picked beam-free from the live catalog and the fix was on main in 29s; falls back to your Claude or Codex login. |

## Failure paths (what a user sees)

| Case | Today | After fix |
|---|---|---|
| Unknown model on Claude, Codex, Gemini or opencode | "exited 1 with no known cause" (15-17s) | VERIFIED: vendor's own reason, ~13s ([f8656774](https://github.com/Quorus-dev/Quorus/commit/f8656774), [36c8d050](https://github.com/Quorus-dev/Quorus/commit/36c8d050)) |
| Grok/OpenRouter with no provider key | same | VERIFIED: "Model unavailable … set the provider's key" |
| `ollama/...` with Ollama stopped | same, after 176-182s | VERIFIED: "Ollama isn't running: start it with …" in 11s ([00e56704](https://github.com/Quorus-dev/Quorus/commit/00e56704)) |
| Every free model rate-limited | Clear line, 30-min quota wait, stuck alert | unchanged (good) |
| Name claimed but key missing locally | 15 claims in 75s; another name then gets 429 | VERIFIED: 1 claim, release-name hint, other names unaffected ([fcd0c8f2](https://github.com/Quorus-dev/Quorus/commit/fcd0c8f2)) |
| CI flake: UUID containing "409" | 1 failure in PR #3 | [836e248c](https://github.com/Quorus-dev/Quorus/commit/836e248c), PR #3 back to 24/24 |

## Bugs found and fixed (each with tests)

1. [476280b2](https://github.com/Quorus-dev/Quorus/commit/476280b2): the free-model rotation was a hardcoded list; it now reads the live `opencode models` catalog.
2. [477c31bf](https://github.com/Quorus-dev/Quorus/commit/477c31bf), [5974ea04](https://github.com/Quorus-dev/Quorus/commit/5974ea04): the demo falls back to your Claude or Codex login when the free tier is busy; it checks the login first and shares only the credential store.
3. [fab4340f](https://github.com/Quorus-dev/Quorus/commit/fab4340f): duel says "the test command did not run" separately from "tests fail".
4. [baf479c4](https://github.com/Quorus-dev/Quorus/commit/baf479c4): `--help` lists opencode under `agent add`.
5. [25a29093](https://github.com/Quorus-dev/Quorus/commit/25a29093): init registers the `quorus-mcp` next to the running install, not a stale one on PATH.
6. [6c0b7ab8](https://github.com/Quorus-dev/Quorus/commit/6c0b7ab8): `--help` lists demo, duel, scorecard, retro, replay, phone, connect and invite.
7. [fcd0c8f2](https://github.com/Quorus-dev/Quorus/commit/fcd0c8f2): Sign-in lockout: claim-name allows 10/hour per IP and counted failed claims. A daemon stuck on a 409 burned it in 30s and locked every local agent out for an hour.
8. [f8656774](https://github.com/Quorus-dev/Quorus/commit/f8656774), [36c8d050](https://github.com/Quorus-dev/Quorus/commit/36c8d050), [00e56704](https://github.com/Quorus-dev/Quorus/commit/00e56704): The vendor's own error text (e.g. a missing provider key) reaches the room.
9. [01fb9eaa](https://github.com/Quorus-dev/Quorus/commit/01fb9eaa), [cd170d8f](https://github.com/Quorus-dev/Quorus/commit/cd170d8f): Slow free models (nemotron): a first-event watchdog rotates on, and the demo skips them.
10. [836e248c](https://github.com/Quorus-dev/Quorus/commit/836e248c): flaky test matched '409' inside a random UUID.
11. [b9aa6f2f](https://github.com/Quorus-dev/Quorus/commit/b9aa6f2f): test-order regression from fix 1's conftest fixture (full suite 2672 passed on weekend/quorus).
