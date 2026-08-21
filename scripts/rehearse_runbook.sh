#!/bin/bash
# Quorus — rehearse the two-human runbook on ONE machine.
#
# docs/MULTI_USER_TEST_RUNBOOK.md is what Arav + Aarya will walk tomorrow on
# two laptops. This script walks the same steps here, using the same
# user-facing `quorus` commands (not internal APIs), with two fully isolated
# identities — separate HOME, separate config dir, separate agent — talking
# to one relay. If a command in the runbook is wrong, this finds it before
# two humans are sitting there waiting.
#
# What this can and cannot prove:
#   ✓ the exact CLI commands in the runbook work, in order, from clean state
#   ✓ two identities coexist without clobbering each other's config
#   ✓ mention → autonomous reply → visible in the OTHER user's history
#   ✓ presence, approvals, and the room lifecycle through the CLI
#   ✗ real network between machines (same-host loopback here)
#   ✗ real model latency (stub harness — see demo_reflex.sh --real)
#
# Usage: ./scripts/rehearse_runbook.sh [--keep-logs]
set -uo pipefail

KEEP_LOGS=0
[[ "${1:-}" == "--keep-logs" ]] && KEEP_LOGS=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="$REPO_ROOT/.venv/bin"
VENV_PY="$VENV_BIN/python3"
[[ -x "$VENV_BIN/quorus" ]] || { echo "missing $VENV_BIN/quorus" >&2; exit 2; }

C_G=$'\033[32m'; C_R=$'\033[31m'; C_D=$'\033[2m'; C_B=$'\033[1m'; C_0=$'\033[0m'
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf "  ${C_G}✓${C_0} %s\n" "$1"; }
bad()  { FAIL=$((FAIL+1)); printf "  ${C_R}✗${C_0} %s${C_D} — %s${C_0}\n" "$1" "${2:-}"; }
step() { printf "\n${C_B}▸ %s${C_0}\n" "$1"; }

WORK="$(mktemp -d /tmp/quorus-rehearsal.XXXXXX)"
ARAV_HOME="$WORK/arav"; AARYA_HOME="$WORK/aarya"
mkdir -p "$ARAV_HOME" "$AARYA_HOME"
RELAY_PID=""; DA_PID=""; DB_PID=""
cleanup() {
  for pid in "$DA_PID" "$DB_PID" "$RELAY_PID"; do
    [[ -n "$pid" ]] && { disown "$pid" 2>/dev/null; kill "$pid" 2>/dev/null; }
  done
  sleep 0.4
  for pid in "$DA_PID" "$DB_PID" "$RELAY_PID"; do
    [[ -n "$pid" ]] && kill -9 "$pid" 2>/dev/null
  done
  wait 2>/dev/null
  [[ $KEEP_LOGS == 1 ]] && printf "\n${C_D}logs: %s${C_0}\n" "$WORK" || rm -rf "$WORK"
}
trap cleanup EXIT

PORT="$($VENV_PY -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
URL="http://127.0.0.1:$PORT"
SECRET="rehearsal-$$"

# Each "human" runs the CLI exactly as they would on their own laptop —
# isolated HOME and config dir prove the two identities never collide.
arav()  { env HOME="$ARAV_HOME"  QUORUS_CONFIG_DIR="$ARAV_HOME/.quorus"  "$VENV_BIN/quorus" "$@"; }
aarya() { env HOME="$AARYA_HOME" QUORUS_CONFIG_DIR="$AARYA_HOME/.quorus" "$VENV_BIN/quorus" "$@"; }

printf "${C_B}Runbook rehearsal${C_0} ${C_D}(two identities, one relay, port %s)${C_0}\n" "$PORT"

# ── Step 0: relay (runbook Path B — self-hosted) ────────────────────────────
step "step 0 — start a relay (runbook Path B)"
env PORT="$PORT" RELAY_SECRET="$SECRET" MESSAGES_FILE="$WORK/messages.json" \
    ALLOW_LEGACY_AUTH=1 LOG_LEVEL=WARNING "$VENV_BIN/quorus-relay" >"$WORK/relay.log" 2>&1 &
RELAY_PID=$!
for i in $(seq 1 40); do curl -fsS -m 1 "$URL/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS -m 2 "$URL/health" >/dev/null 2>&1 && ok "relay up on $URL" \
  || { bad "relay" "never healthy"; exit 1; }

# ── Step 1: each human sets up ──────────────────────────────────────────────
step "step 1 — quorus init (each human, own machine)"
if arav init arav --relay-url "$URL" --secret "$SECRET" --no-autostart --no-smoke --no-launchd \
     >"$WORK/arav-init.log" 2>&1; then
  ok "arav: quorus init"
else
  bad "arav init" "$(tail -2 "$WORK/arav-init.log" | tr '\n' ' ')"
fi
if aarya init aarya --relay-url "$URL" --secret "$SECRET" --no-autostart --no-smoke --no-launchd \
     >"$WORK/aarya-init.log" 2>&1; then
  ok "aarya: quorus init"
else
  bad "aarya init" "$(tail -2 "$WORK/aarya-init.log" | tr '\n' ' ')"
fi

# Configs must be independent — the classic two-people-one-machine failure.
if [[ -f "$ARAV_HOME/.quorus/config.json" && -f "$AARYA_HOME/.quorus/config.json" ]]; then
  ok "each identity wrote its own config (no clobbering)"
else
  bad "isolated configs" "one of the config files is missing"
fi
arav  whoami >"$WORK/whoami-arav.txt"  2>&1
aarya whoami >"$WORK/whoami-aarya.txt" 2>&1
if grep -q "@arav" "$WORK/whoami-arav.txt" && grep -q "@aarya" "$WORK/whoami-aarya.txt"; then
  ok "quorus whoami reports the right identity for each"
else
  bad "whoami" "arav→$(grep -o '@[a-z-]*' "$WORK/whoami-arav.txt" | head -1) aarya→$(grep -o '@[a-z-]*' "$WORK/whoami-aarya.txt" | head -1)"
fi

# ── Step 2: room + join ─────────────────────────────────────────────────────
step "step 2 — create and join the shared room"
if arav create test-day >"$WORK/create.log" 2>&1; then
  ok "arav: quorus create test-day"
else
  bad "create room" "$(tail -2 "$WORK/create.log" | tr '\n' ' ')"
fi
if aarya join test-day >"$WORK/join.log" 2>&1; then
  ok "aarya: quorus join test-day"
else
  bad "join room" "$(tail -2 "$WORK/join.log" | tr '\n' ' ')"
fi

# Agents join as their own participants (reflexd wakes these).
for who in arav-claude aarya-claude; do
  curl -fsS -X POST -H "Authorization: Bearer $SECRET" -H 'Content-Type: application/json' \
    -d "{\"participant\":\"$who\"}" "$URL/rooms/test-day/join" >/dev/null 2>&1
done
ok "both agents joined the room"

# ── Step 3: bind workspaces + start the wake daemons ────────────────────────
step "step 3 — bind a workspace, start each agent's daemon"
mkdir -p "$ARAV_HOME/project" "$AARYA_HOME/project"
arav room bind test-day "$ARAV_HOME/project"  >"$WORK/bind-a.log" 2>&1 \
  && ok "arav: quorus room bind test-day ~/project" \
  || bad "room bind" "$(tail -1 "$WORK/bind-a.log")"
aarya room bind test-day "$AARYA_HOME/project" >"$WORK/bind-b.log" 2>&1 \
  && ok "aarya: quorus room bind" || bad "aarya room bind" "$(tail -1 "$WORK/bind-b.log")"

start_agent() { # start_agent HOME PARTICIPANT VAR
  env RELAY_URL="$URL" API_KEY="$SECRET" REFLEXD_PARTICIPANT="$2" \
      REFLEXD_LEGACY_BEARER=1 REFLEXD_STUB_REPLY=1 REFLEXD_HEARTBEAT_S=2 \
      HOME="$1" QUORUS_CONFIG_DIR="$1/.quorus" \
      "$VENV_PY" "$REPO_ROOT/scripts/reflexd.py" start --debug \
      --participant "$2" --relay-url "$URL" >"$WORK/$2.log" 2>&1 &
  eval "$3=$!"
}
start_agent "$ARAV_HOME"  arav-claude  DA_PID
start_agent "$AARYA_HOME" aarya-claude DB_PID
connected=0
for who in arav-claude aarya-claude; do
  for i in $(seq 1 50); do
    grep -q "sse connected" "$WORK/$who.log" 2>/dev/null && { connected=$((connected+1)); break; }
    sleep 0.5
  done
done
[[ $connected -eq 2 ]] && ok "both wake daemons connected" \
  || bad "daemons connected" "only $connected of 2"

count_replies() {
  curl -fsS -H "Authorization: Bearer $SECRET" "$URL/rooms/test-day/history?limit=50" \
    | "$VENV_PY" -c '
import sys, json
msgs = json.load(sys.stdin)
print(sum(1 for m in msgs if "reflexd-stub" in (m.get("content") or "")))'
}
wait_reply() { # wait_reply BEFORE SECONDS
  local deadline=$(( $(date +%s) + $2 ))
  while [[ $(date +%s) -lt $deadline ]]; do
    local now; now="$(count_replies)"
    [[ "$now" -gt "$1" ]] && { echo "$now"; return 0; }
    sleep 0.5
  done
  count_replies; return 1
}

# ── Runbook tests 1 & 2: wake-on-mention, both directions ───────────────────
step "runbook test 1+2 — mention each other's agent"
before="$(count_replies)"
arav say test-day "@aarya-claude what is in your home dir?" >/dev/null 2>&1
if got="$(wait_reply "$before" 25)"; then
  ok "arav mentions aarya's agent → it replies by itself"
else
  bad "wake (arav → aarya-claude)" "no reply in 25s"
fi

before="$got"
aarya say test-day "@arav-claude same question back?" >/dev/null 2>&1
if got="$(wait_reply "$before" 25)"; then
  ok "aarya mentions arav's agent → it replies by itself"
else
  bad "wake (aarya → arav-claude)" "no reply in 25s"
fi

# The whole point: each human SEES the other's agent answer.
arav  history test-day >"$WORK/hist-arav.txt"  2>&1
aarya history test-day >"$WORK/hist-aarya.txt" 2>&1
if grep -q "reflexd-stub" "$WORK/hist-arav.txt"; then
  ok "arav sees the replies via quorus history"
else
  bad "history (arav)" "$(head -2 "$WORK/hist-arav.txt" | tr '\n' ' ')"
fi
if grep -q "reflexd-stub" "$WORK/hist-aarya.txt"; then
  ok "aarya sees the same transcript"
else
  bad "history (aarya)" "$(head -2 "$WORK/hist-aarya.txt" | tr '\n' ' ')"
fi

# ── Runbook test 4: broadcast auction ───────────────────────────────────────
step "runbook test 4 — @open broadcast"
before="$(count_replies)"
arav say test-day "@open summarize this room" >/dev/null 2>&1
wait_reply "$before" 25 >/dev/null
sleep 3
after="$(count_replies)"
if [[ "$after" -eq $((before + 1)) ]]; then
  ok "exactly one agent answered the broadcast"
else
  bad "@open single winner" "expected $((before+1)) replies, got $after"
fi

# ── Runbook test 5: presence ────────────────────────────────────────────────
step "runbook test 5 — presence"
arav ps >"$WORK/ps.txt" 2>&1
if grep -qiE "claude" "$WORK/ps.txt"; then
  ok "quorus ps shows the agents"
else
  bad "quorus ps" "$(head -3 "$WORK/ps.txt" | tr '\n' ' ')"
fi

# ── Runbook test 9: approvals ───────────────────────────────────────────────
step "runbook test 9 — approval relay"
APR="$(curl -fsS -X POST -H "Authorization: Bearer $SECRET" -H 'Content-Type: application/json' \
  -d '{"room_id":"test-day","agent":"arav-claude","tool_name":"Bash","tool_input":"pytest -q"}' \
  "$URL/v1/approvals" | "$VENV_PY" -c 'import sys,json;print(json.load(sys.stdin)["id"])' 2>/dev/null)"
if [[ "$APR" == apr_* ]]; then ok "an agent asked for permission ($APR)"; else bad "approval request" "got '$APR'"; fi
aarya approvals >"$WORK/approvals.txt" 2>&1
if grep -q "$APR" "$WORK/approvals.txt"; then
  ok "aarya sees it with: quorus approvals"
else
  bad "quorus approvals" "$(head -3 "$WORK/approvals.txt" | tr '\n' ' ')"
fi
aarya approve "$APR" >"$WORK/approve.txt" 2>&1
if grep -qi "approved" "$WORK/approve.txt"; then
  ok "aarya unblocks it with: quorus approve <id>"
else
  bad "quorus approve" "$(head -2 "$WORK/approve.txt" | tr '\n' ' ')"
fi

# ── verdict ─────────────────────────────────────────────────────────────────
printf "\n${C_B}%s${C_0}\n" "────────────────────────────────────────────"
if [[ $FAIL -eq 0 ]]; then
  printf "${C_G}${C_B}REHEARSAL PASSED${C_0} — %s checks. The runbook's commands work end to end.\n" "$PASS"
  exit 0
fi
printf "${C_R}${C_B}REHEARSAL FAILED${C_0} — %s passed, ${C_R}%s failed${C_0}\n" "$PASS" "$FAIL"
KEEP_LOGS=1
exit 1
