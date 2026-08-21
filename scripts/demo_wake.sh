#!/bin/bash
# Quorus — Wake Rebuild acceptance gate (WAKE_REBUILD_SPEC Stream T).
#
# One command that rehearses everything Streams D/R/L shipped, N times in a
# row, against a real relay and real daemons. Stub harness by default: no API
# spend, deterministic timing, so a red run means OUR code broke — not that a
# model was slow.
#
# Checks, per round:
#   1. wake-on-mention           — mention → exactly one autonomous reply
#   2. single-winner auction     — @open broadcast → exactly ONE agent answers
#   3. no fake replies           — an honest error never masquerades as work
#   4. presence honesty          — heartbeats mark agents active
# Once per run (state-changing, order matters):
#   5. offline drain             — daemon down during a mention still answers
#   6. approvals                 — request appears, approve unblocks, denies stick
#   7. workspace binding         — wakes carry the bound repo
#
# Usage: ./scripts/demo_wake.sh [--rounds N] [--keep-logs]
set -uo pipefail

ROUNDS=5
KEEP_LOGS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rounds) ROUNDS="${2:-5}"; shift 2 ;;
    --keep-logs) KEEP_LOGS=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python3"
RELAY_BIN="$REPO_ROOT/.venv/bin/quorus-relay"
[[ -x "$VENV_PY"   ]] || { echo "missing $VENV_PY — run: uv venv && uv pip install -e '.[test]'" >&2; exit 2; }
[[ -x "$RELAY_BIN" ]] || { echo "missing $RELAY_BIN" >&2; exit 2; }

C_G=$'\033[32m'; C_R=$'\033[31m'; C_Y=$'\033[33m'; C_D=$'\033[2m'; C_B=$'\033[1m'; C_0=$'\033[0m'
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf "  ${C_G}✓${C_0} %s\n" "$1"; }
bad()  { FAIL=$((FAIL+1)); printf "  ${C_R}✗${C_0} %s${C_D} — %s${C_0}\n" "$1" "${2:-}"; }
step() { printf "\n${C_B}▸ %s${C_0}\n" "$1"; }

WORK="$(mktemp -d /tmp/quorus-wake-gate.XXXXXX)"
WS="$WORK/workspace"; mkdir -p "$WS"
RELAY_PID=""; D1_PID=""; D2_PID=""
cleanup() {
  # SIGTERM first (reflexd has a graceful handler), then SIGKILL stragglers.
  # `disown` keeps bash from printing "Killed: 9" job notices at exit.
  for pid in "$D1_PID" "$D2_PID" "$RELAY_PID"; do
    [[ -n "$pid" ]] && { disown "$pid" 2>/dev/null; kill "$pid" 2>/dev/null; }
  done
  sleep 0.4
  for pid in "$D1_PID" "$D2_PID" "$RELAY_PID"; do
    [[ -n "$pid" ]] && kill -9 "$pid" 2>/dev/null
  done
  wait 2>/dev/null
  if [[ $KEEP_LOGS == 1 ]]; then
    printf "\n${C_D}logs kept in %s${C_0}\n" "$WORK"
  else
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

PORT="$($VENV_PY -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
URL="http://127.0.0.1:$PORT"
SECRET="wake-gate-$$"
H="Authorization: Bearer $SECRET"
AGENT_A="arav-claude"; AGENT_B="aarya-claude"

api() { # api METHOD PATH [JSON]
  if [[ -n "${3:-}" ]]; then
    curl -fsS -X "$1" -H "$H" -H 'Content-Type: application/json' -d "$3" "$URL$2"
  else
    curl -fsS -X "$1" -H "$H" "$URL$2"
  fi
}
jqp() { "$VENV_PY" -c "import sys,json;d=json.load(sys.stdin);$1"; }

start_daemon() { # start_daemon PARTICIPANT VARNAME
  env RELAY_URL="$URL" API_KEY="$SECRET" REFLEXD_PARTICIPANT="$1" \
      REFLEXD_LEGACY_BEARER=1 REFLEXD_STUB_REPLY=1 REFLEXD_HEARTBEAT_S=2 \
      HOME="$WORK" \
      "$VENV_PY" "$REPO_ROOT/scripts/reflexd.py" start --debug \
      --participant "$1" --relay-url "$URL" >"$WORK/$1.log" 2>&1 &
  eval "$2=$!"
}

wait_for() { # wait_for SECONDS COMMAND...
  local deadline=$(( $(date +%s) + $1 )); shift
  while [[ $(date +%s) -lt $deadline ]]; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 0.25
  done
  return 1
}

replies_from_agents() { # count agent replies in room history
  api GET "/rooms/$RID/history?limit=50" | "$VENV_PY" -c '
import sys, json
msgs = json.load(sys.stdin)
print(sum(1 for m in msgs
          if m.get("from_name","").endswith("-claude")
          and "reflexd-stub" in (m.get("content") or "")))'
}

printf "${C_B}Quorus wake-rebuild acceptance gate${C_0} ${C_D}(port %s · %s rounds · stub harness)${C_0}\n" "$PORT" "$ROUNDS"

# ── boot ────────────────────────────────────────────────────────────────────
step "boot: relay + two agent daemons"
env PORT="$PORT" RELAY_SECRET="$SECRET" MESSAGES_FILE="$WORK/messages.json" \
    ALLOW_LEGACY_AUTH=1 LOG_LEVEL=WARNING "$RELAY_BIN" >"$WORK/relay.log" 2>&1 &
RELAY_PID=$!
wait_for 20 curl -fsS -m 1 "$URL/health" || { bad "relay healthy" "never came up"; exit 1; }
ok "relay healthy"

RID="$(api POST /rooms '{"name":"wake-gate","created_by":"arav"}' | jqp 'print(d["id"])')"
for who in "$AGENT_A" "$AGENT_B"; do
  api POST "/rooms/$RID/join" "{\"participant\":\"$who\"}" >/dev/null
done
ok "room + two agents joined"

# D1: bind the room to a workspace on this host.
mkdir -p "$WORK/.quorus"
printf '{"wake-gate": "%s"}' "$WS" > "$WORK/.quorus/room-bindings.json"
ok "workspace bound (D1)"

start_daemon "$AGENT_A" D1_PID
start_daemon "$AGENT_B" D2_PID
for who in "$AGENT_A" "$AGENT_B"; do
  wait_for 25 grep -q "sse connected" "$WORK/$who.log" \
    || { bad "$who connected" "no SSE within 25s"; exit 1; }
done
ok "both daemons subscribed"

# ── rounds ──────────────────────────────────────────────────────────────────
for round in $(seq 1 "$ROUNDS"); do
  step "round $round/$ROUNDS"

  before="$(replies_from_agents)"
  api POST "/rooms/$RID/messages" \
      "{\"from_name\":\"arav\",\"content\":\"@$AGENT_A round $round status?\"}" >/dev/null
  deadline=$(( $(date +%s) + 20 )); got=$before
  while [[ $(date +%s) -lt $deadline ]]; do
    got="$(replies_from_agents)"
    [[ "$got" -gt "$before" ]] && break
    sleep 0.25
  done
  if [[ "$got" -eq $((before + 1)) ]]; then
    ok "mention → exactly one autonomous reply"
  else
    bad "mention → one reply" "expected $((before+1)), got $got"
  fi

  before="$got"
  api POST "/rooms/$RID/messages" \
      "{\"from_name\":\"arav\",\"content\":\"@open round $round: fix the failing tests\"}" >/dev/null
  deadline=$(( $(date +%s) + 20 )); got=$before
  while [[ $(date +%s) -lt $deadline ]]; do
    got="$(replies_from_agents)"
    [[ "$got" -gt "$before" ]] && break
    sleep 0.25
  done
  sleep 3   # give a second (wrong) winner time to appear if the auction is broken
  got="$(replies_from_agents)"
  if [[ "$got" -eq $((before + 1)) ]]; then
    ok "@open broadcast → exactly one winner (no echo storm)"
  else
    bad "@open single winner" "expected $((before+1)), got $got"
  fi
done

# ── honesty + presence ──────────────────────────────────────────────────────
step "honesty + presence"
if api GET "/rooms/$RID/history?limit=50" | grep -q "not installed on this host"; then
  bad "no honest-error leakage" "an install error surfaced in a stub run"
else
  ok "no fake/error replies mixed into the transcript"
fi

presence="$(api GET "/rooms/$RID" | "$VENV_PY" -c '
import sys, json
d = json.load(sys.stdin).get("member_presence", {})
print(sum(1 for v in d.values() if v.get("presence") == "active"))')"
if [[ "${presence:-0}" -ge 2 ]]; then
  ok "presence: both agents heartbeat as active (R2)"
else
  bad "presence active" "only ${presence:-0} of 2 agents active"
fi

# ── offline drain (R1) ──────────────────────────────────────────────────────
step "offline drain (R1)"
kill "$D2_PID" 2>/dev/null; sleep 1
before="$(replies_from_agents)"
api POST "/rooms/$RID/messages" \
    "{\"from_name\":\"arav\",\"content\":\"@$AGENT_B you were offline — reply now\"}" >/dev/null
sleep 2
start_daemon "$AGENT_B" D2_PID
deadline=$(( $(date +%s) + 30 )); got=$before
while [[ $(date +%s) -lt $deadline ]]; do
  got="$(replies_from_agents)"
  [[ "$got" -gt "$before" ]] && break
  sleep 0.5
done
if [[ "$got" -gt "$before" ]]; then
  ok "mention sent while daemon was DOWN was answered on restart"
else
  bad "offline drain" "no reply within 30s of restart"
fi

# ── approvals (L3) ──────────────────────────────────────────────────────────
step "approvals (L3)"
APR="$(api POST /v1/approvals \
  "{\"room_id\":\"$RID\",\"agent\":\"$AGENT_A\",\"tool_name\":\"Bash\",\"tool_input\":\"pytest -q\"}" \
  | jqp 'print(d["id"])')"
if [[ "$APR" == apr_* ]]; then ok "approval requested ($APR)"; else bad "approval request" "got '$APR'"; fi

if api GET "/v1/approvals" | grep -q "$APR"; then
  ok "approval visible to humans (quorus approvals)"
else
  bad "approval listed" "not in pending list"
fi
if api GET "/rooms/$RID/history?limit=50" | grep -q "approval needed"; then
  ok "approval posted into the room chat"
else
  bad "approval chat line" "not found in history"
fi
st="$(api POST "/v1/approvals/$APR/decision" '{"approve":true,"reason":"gate"}' | jqp 'print(d["status"])')"
[[ "$st" == "approved" ]] && ok "approve unblocks" || bad "approve" "status=$st"
st2="$(api POST "/v1/approvals/$APR/decision" '{"approve":false}' | jqp 'print(d["status"])')"
[[ "$st2" == "approved" ]] && ok "settled decisions are idempotent" || bad "idempotent decision" "flipped to $st2"

# ── workspace binding (D1) ──────────────────────────────────────────────────
step "workspace binding (D1)"
if grep -q "workspace=$WS" "$WORK/$AGENT_A.log"; then
  ok "wakes ran inside the bound workspace"
else
  bad "workspace in wake" "no 'workspace=$WS' line in daemon log"
fi

# ── verdict ─────────────────────────────────────────────────────────────────
printf "\n${C_B}%s${C_0}\n" "────────────────────────────────────────────"
if [[ $FAIL -eq 0 ]]; then
  printf "${C_G}${C_B}GATE PASSED${C_0} — %s checks, %s rounds, zero manual prompting\n" "$PASS" "$ROUNDS"
  exit 0
fi
printf "${C_R}${C_B}GATE FAILED${C_0} — %s passed, ${C_R}%s failed${C_0}\n" "$PASS" "$FAIL"
printf "${C_Y}logs:${C_0} %s\n" "$WORK"
KEEP_LOGS=1
exit 1
