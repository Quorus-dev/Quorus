# Live proofs

`failover_proof.py`: a private relay plus two daemons from this branch. The opencode agent's
`opencode` is a stub that always answers "Rate limit exceeded", and the Claude agent runs on Haiku.
The script asks the opencode agent a question and passes only if Claude answers through a
`/defer` handoff. The stub lives in `failover-proof/fakebin/opencode` next to the script; create it
with the snippet below, plus a git repo at `failover-proof/repo`.

    #!/bin/sh
    if [ "$1" = "--version" ]; then echo "2.0.32"; exit 0; fi
    echo '{"type":"error","error":{"name":"APIError","data":{"message":"Rate limit exceeded."}}}'
    exit 1

Result on 2026-10-17 (overseer): **fails**. `POST /v1/social/defer` returns 403 (legacy auth
refused, routes/social.py:119), so on a shared-secret relay the handoff never fires. Claude
answered only because a human's message wakes every agent. The check for a real pass is a
`social` message from the failing agent followed by the teammate's answer.

Re-run 2026-10-17 12:07 on 397dd2db/6c4982ef (participant keys for daemons): **passes**. `social/defer` returns 200; t-claude's log shows `reason=vendor failover`; answer in 42s.

`needs_you_proof.py` (2026-10-17, on release/0.5.0 = 32c97a6e + 3288de2e): a private relay and
one daemon per vendor (Claude Code, Codex, Gemini CLI, opencode) whose CLI is a stub answering in
that vendor's real output format with "@arav should I merge the release PR now?". **Passes for all
four:** each reply was parsed and posted, and each daemon fired a "needs you" desktop alert.
