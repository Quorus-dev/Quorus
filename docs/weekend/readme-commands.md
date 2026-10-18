# README Quorus Command Audit

This document records the verification of every `quorus <command>` mentioned or available in the Quorus CLI, tested by running `quorus <command> --help`.

## Tested Commands

- `quorus init` — OK
- `quorus begin` — OK
- `quorus doctor` — OK
- `quorus version` — OK
- `quorus rooms` — OK
- `quorus create` — OK
- `quorus join` — OK
- `quorus chat` — OK
- `quorus say` — OK
- `quorus dm` — OK
- `quorus inbox` — OK
- `quorus history` — OK
- `quorus search` — OK
- `quorus state` — OK
- `quorus locks` — OK
- `quorus context` — OK
- `quorus decision` — OK
- `quorus resolve` — OK
- `quorus board` — OK
- `quorus room` — OK
- `quorus approvals` — OK
- `quorus approve` — OK
- `quorus deny` — OK
- `quorus agent` — OK
- `quorus ps` — OK
- `quorus spawn` — OK
- `quorus add-agent` — OK
- `quorus quickstart` — OK
- `quorus hackathon` — OK
- `quorus share` — OK
- `quorus quickjoin` — OK
- `quorus reflexd` — OK
- `quorus turnguard` — OK
- `quorus relay` — OK
- `quorus status` — OK
- `quorus logs` — OK
- `quorus metrics` — OK
- `quorus usage` — OK
- `quorus stats` — OK
- `quorus export` — OK
- `quorus kick` — OK
- `quorus rename` — OK
- `quorus destroy` — OK
- `quorus hook` — OK
- `quorus scorecard` — OK
- `quorus duel` — OK
- `quorus retro` — OK
- `quorus replay` — OK
- `quorus phone` — OK

## Findings
- All 50 checked commands successfully return help output (`exit code 0`).
- No missing or failing README commands identified.
