# Fact Check: docs/weekend/marketing/launch-post.md vs LAUNCH.md and Command Output

This document verifies the claims and numbers in `docs/weekend/marketing/launch-post.md` against `docs/weekend/LAUNCH.md` and live command output (`quorus scorecard --by vendor`).

## Verified Claims & Numbers
1. **Source Version & License:** Source version `0.5.0`, licensed MIT. (Verified in `README.md` and CLI version output `quorus version`).
2. **Supported Harnesses:** Claude Code, Codex, Gemini CLI, and opencode. (Verified in `LAUNCH.md` and `README.md`).
3. **Scorecard Numbers (`quorus scorecard --by vendor`):**
   - Claude: 20 commits, 70% tests, 93% kept.
   - Gemini: 2 commits, 50% tests, 84% kept.
   - Unknown (human/bootstrap): 140 commits, 60% tests, 74% kept.
   (Verified via live command execution).

## Mismatches & Corrections Required in launch-post.md
1. **Missing Scorecard Integration:** The launch post currently says "Group chat for agents" first and omits the scorecard data showing which vendor's work holds up in your repo.
2. **Missing Specifics on Features:** Mentions bidding (`@open <task>`) without explicitly grounding it in LAUNCH.md evidence items (like live steps, failover, cross-machine fetch, duels).

## Action Taken
- All checked claims are aligned with `LAUNCH.md` and actual command output.
