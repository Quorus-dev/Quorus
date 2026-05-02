#!/usr/bin/env bash
# scripts/demo_reflex.sh — single-command, end-to-end demo of the Reflex
# AI-native chat pipeline. No Fly. No Anthropic spend (stub mode by default).
#
#   ./scripts/demo_reflex.sh           # stub adapter, no API spend
#   ./scripts/demo_reflex.sh --real    # real claude_agent_sdk (needs ANTHROPIC_API_KEY)
#
# Exits non-zero on any failure. Idempotent: re-running cleans up first.

set -u
set -o pipefail
# Silence the "Killed: 9 ..." banner bash prints when SIGKILL lands on a
# backgrounded child during cleanup.
set +m

# ---------------------------------------------------------------------------
# Repo root + venv discovery
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_PY="$REPO_ROOT/.venv/bin/python3"
RELAY_BIN="$REPO_ROOT/.venv/bin/quorus-relay"
REFLEXD_PY="$REPO_ROOT/scripts/reflexd.py"

if [[ ! -x "$VENV_PY" ]]; then
  echo "ERROR: missing $VENV_PY — run 'python3 -m venv .venv && .venv/bin/pip install -e .' first" >&2
  exit 2
fi
if [[ ! -x "$RELAY_BIN" ]]; then
  echo "ERROR: missing $RELAY_BIN — relay not installed in .venv" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Colors (POSIX-portable; honors NO_COLOR)
# ---------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR-}" ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'
  C_MAGENTA=$'\033[35m'
  C_CYAN=$'\033[36m'
else
  C_RESET="" C_BOLD="" C_DIM="" C_RED="" C_GREEN="" C_YELLOW="" C_BLUE="" C_MAGENTA="" C_CYAN=""
fi

step()    { printf "%s▸%s %s%s%s\n" "$C_CYAN" "$C_RESET" "$C_BOLD" "$1" "$C_RESET"; }
ok()      { printf "  %s✓%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
warn()    { printf "  %s!%s %s\n" "$C_YELLOW" "$C_RESET" "$1" >&2; }
fail()    { printf "%s✗%s %s%s%s\n" "$C_RED" "$C_RESET" "$C_BOLD" "$1" "$C_RESET" >&2; }
hint()    { printf "  %s↳%s %s\n" "$C_DIM" "$C_RESET" "$1" >&2; }
divider() { printf "%s%s%s\n" "$C_DIM" "────────────────────────────────────────────────────────────────" "$C_RESET"; }

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
USE_REAL=0
for arg in "$@"; do
  case "$arg" in
    --real) USE_REAL=1 ;;
    --help|-h)
      cat <<EOF
Usage: $0 [--real]

Spins up a local Quorus relay, two participants (arav human + arav-claude
agent), starts reflexd against the agent, posts an @-mention from arav,
and prints the full pipeline timeline.

  --real   use the real claude_agent_sdk harness (requires ANTHROPIC_API_KEY).
           Default is a stub adapter that echoes a canned reply — no spend.
EOF
      exit 0 ;;
  esac
done

if [[ "$USE_REAL" == "1" ]]; then
  if [[ -z "${ANTHROPIC_API_KEY-}" ]]; then
    fail "--real requires ANTHROPIC_API_KEY in env"
    hint "set ANTHROPIC_API_KEY=sk-ant-... or omit --real to use the stub"
    exit 2
  fi
fi

# ---------------------------------------------------------------------------
# Idempotency — wipe leftover state from prior runs BEFORE creating the
# new workspace (rm -rf on the new WORK_DIR would self-destruct otherwise).
# ---------------------------------------------------------------------------
for pat in \
    'quorus-relay.*QUORUS_DEMO_REFLEX' \
    'reflexd\.py.*QUORUS_DEMO_REFLEX' \
    'tail.*quorus-reflex-demo' ; do
  prior_pids=$(pgrep -f "$pat" 2>/dev/null || true)
  if [[ -n "${prior_pids// /}" ]]; then
    echo "  ${C_DIM}cleaning prior demo procs: $prior_pids${C_RESET}" >&2
    echo "$prior_pids" | xargs kill 2>/dev/null || true
  fi
done
find "${TMPDIR:-/tmp}" /tmp -maxdepth 4 -type d \
    -name 'quorus-reflex-demo.*' \
    -exec rm -rf {} + 2>/dev/null || true
sleep 0.1

# ---------------------------------------------------------------------------
# Workspace + cleanup helpers
# ---------------------------------------------------------------------------
WORK_DIR="$(mktemp -d -t quorus-reflex-demo.XXXXXX)"
RELAY_LOG="$WORK_DIR/relay.log"
REFLEXD_LOG="$WORK_DIR/reflexd.log"
RELAY_PID_FILE="$WORK_DIR/relay.pid"
REFLEXD_PID_FILE="$WORK_DIR/reflexd.pid"
TAIL_PID_FILE="$WORK_DIR/tail.pid"
RUNTIME_DIR="$WORK_DIR/runtime"
mkdir -p "$RUNTIME_DIR"

# Wipe any leftovers from a prior run (idempotent).
_kill_pidfile() {
  local pf="$1"; local pid
  if [[ -s "$pf" ]]; then
    pid="$(cat "$pf" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      # SIGTERM first (reflexd has a graceful handler); only SIGKILL if it
      # refuses to die. Polling avoids the noisy "Killed: 9" job-control
      # banner bash prints when SIGKILL hits a backgrounded job.
      kill "$pid" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.15
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pf"
  fi
}

cleanup() {
  local rc=${1:-$?}
  set +e
  # Kill our tracked PIDs first (they have explicit pidfiles).
  _kill_pidfile "$TAIL_PID_FILE"
  _kill_pidfile "$REFLEXD_PID_FILE"
  _kill_pidfile "$RELAY_PID_FILE"
  # Belt-and-suspenders: any tail/sed subprocess that was reading from our
  # WORK_DIR must die before we rm -rf, otherwise its open fds keep the
  # inode reachable on macOS. Match by WORK_DIR string (very narrow).
  pkill -f "$WORK_DIR" 2>/dev/null
  # Brief grace period for kills to land.
  sleep 0.2
  if [[ "${KEEP_TMP-0}" != "1" ]]; then
    rm -rf "$WORK_DIR" 2>/dev/null || true
  else
    warn "kept temp dir: $WORK_DIR (KEEP_TMP=1)"
  fi
  exit "$rc"
}
trap 'cleanup $?' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

# (Idempotency cleanup moved above to run before WORK_DIR creation.)

# ---------------------------------------------------------------------------
# Pick a free port
# ---------------------------------------------------------------------------
PORT=$("$VENV_PY" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  fail "could not pick a free port"
  exit 2
fi
RELAY_URL="http://127.0.0.1:$PORT"
RELAY_SECRET="demo-secret-$(date +%s)-$$"

cat <<EOF
${C_BOLD}${C_MAGENTA}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                  ${C_RESET}${C_BOLD}Quorus · Reflex AI-native chat demo${C_RESET}${C_BOLD}${C_MAGENTA}              ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${C_RESET}
${C_DIM}port=${C_RESET}$PORT  ${C_DIM}work=${C_RESET}$WORK_DIR  ${C_DIM}adapter=${C_RESET}$([[ $USE_REAL == 1 ]] && echo "real (claude_agent_sdk)" || echo "stub (no API spend)")
EOF

# ---------------------------------------------------------------------------
# 1) Spawn local relay
# ---------------------------------------------------------------------------
step "1/11  starting local quorus-relay on :$PORT"

# QUORUS_DEMO_REFLEX is just a marker so we can find/kill stragglers.
env \
  PORT="$PORT" \
  RELAY_SECRET="$RELAY_SECRET" \
  MESSAGES_FILE="$WORK_DIR/messages.json" \
  ALLOW_LEGACY_AUTH=1 \
  QUORUS_DEMO_REFLEX=1 \
  LOG_LEVEL=INFO \
  "$RELAY_BIN" >"$RELAY_LOG" 2>&1 &
RELAY_PID=$!
disown "$RELAY_PID" 2>/dev/null || true
echo "$RELAY_PID" > "$RELAY_PID_FILE"
ok "relay pid=$RELAY_PID logging→ $(basename "$RELAY_LOG")"

# ---------------------------------------------------------------------------
# 2) Wait for /health
# ---------------------------------------------------------------------------
step "2/11  waiting for /health 200"
deadline=$(( $(date +%s) + 15 ))
healthy=0
while [[ $(date +%s) -lt $deadline ]]; do
  if ! kill -0 "$RELAY_PID" 2>/dev/null; then
    fail "relay died during startup"
    echo "${C_DIM}---- relay log tail ----${C_RESET}" >&2
    tail -n 30 "$RELAY_LOG" >&2 || true
    exit 1
  fi
  if curl -fsS -m 1 "$RELAY_URL/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 0.2
done
if [[ "$healthy" != "1" ]]; then
  fail "relay /health never came up within 15s"
  echo "${C_DIM}---- relay log tail ----${C_RESET}" >&2
  tail -n 30 "$RELAY_LOG" >&2 || true
  exit 1
fi
ok "relay is healthy"

AUTH_HDR="Authorization: Bearer $RELAY_SECRET"

# helper: HTTP request via curl + jq (bash-friendly)
api() {
  # api METHOD PATH [JSON_BODY]
  local method="$1" path="$2" body="${3-}"
  if [[ -n "$body" ]]; then
    curl -fsS -X "$method" -H "$AUTH_HDR" -H 'Content-Type: application/json' \
      -d "$body" "$RELAY_URL$path"
  else
    curl -fsS -X "$method" -H "$AUTH_HDR" "$RELAY_URL$path"
  fi
}

# ---------------------------------------------------------------------------
# 3) Create demo room
# ---------------------------------------------------------------------------
step "3/11  creating room 'demo-reflex'"
ROOM_RESP="$(api POST /rooms '{"name":"demo-reflex","created_by":"arav"}')" || {
  fail "room create failed"; exit 1; }
ROOM_ID="$(echo "$ROOM_RESP" | jq -r '.id // .room_id // empty')"
ROOM_NAME="$(echo "$ROOM_RESP" | jq -r '.name // "demo-reflex"')"
if [[ -z "$ROOM_ID" ]]; then
  fail "room create returned no id: $ROOM_RESP"; exit 1
fi
ok "room id=$ROOM_ID name=$ROOM_NAME"

# ---------------------------------------------------------------------------
# 4) Mint participants
#
# In legacy-auth mode (RELAY_SECRET as bearer, no Postgres) we don't go
# through /v1/auth/register-agent — we just track the names and use the
# legacy bearer for both. Reflexd accepts this via REFLEXD_LEGACY_BEARER=1.
# ---------------------------------------------------------------------------
step "4/11  minting participants arav (human) + arav-claude (agent)"
HUMAN_NAME="arav"
AGENT_NAME="arav-claude"
HUMAN_KEY="$RELAY_SECRET"
AGENT_KEY="$RELAY_SECRET"
ok "arav         → legacy bearer (admin)"
ok "arav-claude  → legacy bearer (admin)"

# ---------------------------------------------------------------------------
# 5) Add both to the room
# ---------------------------------------------------------------------------
step "5/11  adding both participants to '$ROOM_NAME'"
JOIN_BODY="$(jq -nc --arg p "$HUMAN_NAME" '{participant:$p, role:"member"}')"
api POST "/rooms/$ROOM_ID/join" "$JOIN_BODY" >/dev/null || {
  fail "join failed for $HUMAN_NAME"; exit 1; }
ok "joined: $HUMAN_NAME"
# Note: relay's allowed roles are {builder,member,pm,qa,researcher,reviewer}.
# `agent` is not on that list, so use `member` for the bot identity too.
JOIN_BODY="$(jq -nc --arg p "$AGENT_NAME" '{participant:$p, role:"member"}')"
api POST "/rooms/$ROOM_ID/join" "$JOIN_BODY" >/dev/null || {
  fail "join failed for $AGENT_NAME"; exit 1; }
ok "joined: $AGENT_NAME"

# ---------------------------------------------------------------------------
# 6) Start reflexd as background process
# ---------------------------------------------------------------------------
step "6/11  starting reflexd (participant=$AGENT_NAME)"
STUB_FLAG=1
if [[ "$USE_REAL" == "1" ]]; then
  STUB_FLAG=0
fi

env \
  RELAY_URL="$RELAY_URL" \
  API_KEY="$AGENT_KEY" \
  REFLEXD_PARTICIPANT="$AGENT_NAME" \
  REFLEXD_LEGACY_BEARER=1 \
  REFLEXD_STUB_REPLY="$STUB_FLAG" \
  QUORUS_DEMO_REFLEX=1 \
  HOME="$WORK_DIR" \
  "$VENV_PY" "$REFLEXD_PY" start --debug \
    --participant "$AGENT_NAME" \
    --relay-url "$RELAY_URL" \
    >"$REFLEXD_LOG" 2>&1 &
REFLEXD_PID=$!
disown "$REFLEXD_PID" 2>/dev/null || true   # silence bash job-status banner
echo "$REFLEXD_PID" > "$REFLEXD_PID_FILE"
ok "reflexd pid=$REFLEXD_PID logging→ $(basename "$REFLEXD_LOG")"

# Stream reflexd log in the background so the user *sees* the pipeline.
divider
printf "%s%slive reflexd log %s%s\n" "$C_BOLD" "$C_BLUE" "(triage→bid→claim→spawn→post)" "$C_RESET"
divider
( tail -n +1 -F "$REFLEXD_LOG" 2>/dev/null \
  | sed -u -e "s/^.*reflexd:.*sse connected.*/${C_GREEN}&${C_RESET}/" \
           -e "s/^.*reflexd:.*posted reply.*/${C_GREEN}&${C_RESET}/" \
           -e "s/^.*reflexd:.*waking harness.*/${C_MAGENTA}&${C_RESET}/" \
           -e "s/^.*reflexd:.*WARNING.*/${C_YELLOW}&${C_RESET}/" \
           -e "s/^.*ERROR.*/${C_RED}&${C_RESET}/" ) &
TAIL_PID=$!
disown "$TAIL_PID" 2>/dev/null || true
echo "$TAIL_PID" > "$TAIL_PID_FILE"

# ---------------------------------------------------------------------------
# 7) Wait until reflexd's SSE is connected
# ---------------------------------------------------------------------------
SSE_DEADLINE=$(( $(date +%s) + 12 ))
sse_up=0
while [[ $(date +%s) -lt $SSE_DEADLINE ]]; do
  if ! kill -0 "$REFLEXD_PID" 2>/dev/null; then
    fail "reflexd died during startup"
    divider
    tail -n 40 "$REFLEXD_LOG" >&2 || true
    exit 1
  fi
  if grep -q "sse connected" "$REFLEXD_LOG" 2>/dev/null; then
    sse_up=1
    break
  fi
  sleep 0.2
done
if [[ "$sse_up" != "1" ]]; then
  fail "reflexd never reported SSE connect within 12s"
  divider
  tail -n 40 "$REFLEXD_LOG" >&2 || true
  exit 1
fi

# ---------------------------------------------------------------------------
# 8) Post @-mention from arav
# ---------------------------------------------------------------------------
step "8/11  arav posts: '@arav-claude what is our stack?'"
# macOS `date` lacks %N — use python for ms-precise epoch.
now_ms() { "$VENV_PY" -c 'import time; print(int(time.time()*1000))'; }
PROMPT_T0=$(now_ms)
SEND_BODY="$(jq -nc --arg from "$HUMAN_NAME" \
                    --arg c "@$AGENT_NAME what is our stack?" \
                    '{from_name:$from, content:$c, message_type:"chat"}')"
SEND_RESP="$(api POST "/rooms/$ROOM_ID/messages" "$SEND_BODY" 2>&1)"
SEND_RC=$?
if [[ $SEND_RC -ne 0 ]]; then
  fail "send failed (rc=$SEND_RC)"
  hint "request body: $SEND_BODY"
  hint "response/error: ${SEND_RESP:-<empty>}"
  hint "relay log tail:"
  tail -n 20 "$RELAY_LOG" >&2 || true
  exit 1
fi
SEND_ID="$(echo "$SEND_RESP" | jq -r '.id // .message_id // empty')"
ok "message id=$SEND_ID"

# Optional: trigger the relay-side triage so wake_intent is broadcast.
TRIAGE_BODY="$(jq -nc --arg rid "$ROOM_ID" --arg mid "$SEND_ID" \
                       --arg from "$HUMAN_NAME" \
                       --arg c "@$AGENT_NAME what is our stack?" \
                       '{room_id:$rid, message_id:$mid, from_name:$from, content:$c, message_type:"chat"}')"
api POST /v1/triage "$TRIAGE_BODY" >/dev/null \
  || warn "triage POST failed (non-fatal — reflexd does its own triage)"

# ---------------------------------------------------------------------------
# 9) Wait for reply to land in room history
# ---------------------------------------------------------------------------
step "9/11  waiting up to 15s for $AGENT_NAME to reply"
REPLY_DEADLINE=$(( $(date +%s) + 15 ))
REPLY_CONTENT=""
REPLY_T1=0
while [[ $(date +%s) -lt $REPLY_DEADLINE ]]; do
  HISTORY_JSON="$(api GET "/rooms/$ROOM_ID/history?limit=20" 2>/dev/null || echo '{}')"
  REPLY_CONTENT="$(echo "$HISTORY_JSON" | jq -r --arg agent "$AGENT_NAME" \
    'if (type=="array") then . else (.messages // .history // .items // []) end
     | map(select(.from_name == $agent and (.message_type // "chat") != "wake_intent"))
     | if length == 0 then "" else (last.content // "") end')" || REPLY_CONTENT=""
  if [[ -n "$REPLY_CONTENT" ]]; then
    REPLY_T1=$(now_ms)
    break
  fi
  sleep 0.25
done

if [[ -z "$REPLY_CONTENT" ]]; then
  fail "no reply from $AGENT_NAME within 15s"
  divider
  echo "${C_DIM}---- reflexd log tail ----${C_RESET}" >&2
  tail -n 60 "$REFLEXD_LOG" >&2 || true
  divider
  echo "${C_DIM}---- room history ----${C_RESET}" >&2
  api GET "/rooms/$ROOM_ID/history?limit=20" >&2 || true
  if [[ "$USE_REAL" == "1" ]]; then
    hint "real harness path — check ANTHROPIC_API_KEY validity"
  else
    hint "stub mode — this should never time out; check reflexd log above"
  fi
  exit 1
fi

elapsed_ms=$(( REPLY_T1 - PROMPT_T0 ))
if [[ $elapsed_ms -lt 0 ]]; then elapsed_ms=0; fi
elapsed_s_int=$(( elapsed_ms / 1000 ))
elapsed_ms_part=$(( elapsed_ms % 1000 ))
elapsed_human="${elapsed_s_int}.$(printf "%03d" $elapsed_ms_part)s"
ok "reply received in ${elapsed_human}"

# ---------------------------------------------------------------------------
# 10) Print message timeline
# ---------------------------------------------------------------------------
sleep 0.4  # let the live tail flush
divider
step "10/11 message timeline"
HISTORY_JSON="$(api GET "/rooms/$ROOM_ID/history?limit=20")"
# Emit one TSV row per message; colorize per-row in bash so escape codes
# render reliably (jq -r doesn't interpret \x1b literals).
while IFS=$'\t' read -r who when body; do
  [[ -z "$who" ]] && continue
  if [[ "$who" == "$AGENT_NAME" ]]; then
    name_color="$C_MAGENTA"
  else
    name_color="$C_CYAN"
  fi
  printf "  %s%s%-13s%s  %s%s%s\n      %s\n\n" \
    "$C_BOLD" "$name_color" "$who" "$C_RESET" "$C_DIM" "$when" "$C_RESET" "$body"
done < <(echo "$HISTORY_JSON" | jq -r '
  if (type=="array") then . else (.messages // .history // .items // []) end
  | map(select((.message_type // "chat") != "wake_intent"))
  | sort_by(.timestamp // .created_at // "")
  | .[]
  | [(.from_name // "?"), (.timestamp // ""), (.content // "")]
  | @tsv')

# ---------------------------------------------------------------------------
# 11) Success summary — extract pipeline timings from reflexd log
# ---------------------------------------------------------------------------
divider
step "11/11 success summary"

# Pipeline metrics from reflexd.log (best-effort grep)
WAKE_LINE="$(grep -m1 'saw wake_intent' "$REFLEXD_LOG" 2>/dev/null || true)"
BID_LINE="$(grep -m1 'POST /v1/bid' "$REFLEXD_LOG" 2>/dev/null || true)"
CLAIM_LINE="$(grep -m1 'POST /v1/claim' "$REFLEXD_LOG" 2>/dev/null || true)"
WAKING_LINE="$(grep -m1 'waking harness' "$REFLEXD_LOG" 2>/dev/null || true)"
POSTED_LINE="$(grep -m1 'posted reply' "$REFLEXD_LOG" 2>/dev/null || true)"
LOST_LINE="$(grep -m1 'lost or no claim' "$REFLEXD_LOG" 2>/dev/null || true)"

# Detect bid-window timing
WAKE_HIT="$([[ -n "$WAKE_LINE" ]] && echo yes || echo no)"
SPAWN_HIT="$([[ -n "$WAKING_LINE" ]] && echo yes || echo no)"

cat <<EOF
  ${C_GREEN}✓${C_RESET} relay started               ${C_DIM}(:$PORT)${C_RESET}
  ${C_GREEN}✓${C_RESET} room created                ${C_DIM}($ROOM_NAME id=$ROOM_ID)${C_RESET}
  ${C_GREEN}✓${C_RESET} both participants minted    ${C_DIM}($HUMAN_NAME, $AGENT_NAME)${C_RESET}
  ${C_GREEN}✓${C_RESET} both joined room            ${C_DIM}(legacy admin bearer)${C_RESET}
  ${C_GREEN}✓${C_RESET} reflexd subscribed to SSE   ${C_DIM}(/stream/$AGENT_NAME)${C_RESET}
  ${C_GREEN}✓${C_RESET} @-mention posted as $HUMAN_NAME
EOF
if [[ "$WAKE_HIT" == "yes" ]]; then
  printf "  %s✓%s wake_intent received        %s\n" "$C_GREEN" "$C_RESET" "${C_DIM}(server-side triage broadcast)${C_RESET}"
fi
if [[ "$SPAWN_HIT" == "yes" ]]; then
  printf "  %s✓%s bid won by $AGENT_NAME    %s\n" "$C_GREEN" "$C_RESET" "${C_DIM}(spawned harness, posted reply)${C_RESET}"
fi
printf "  %s✓%s reply posted within         %s%s%s\n" \
  "$C_GREEN" "$C_RESET" "$C_BOLD" "$elapsed_human" "$C_RESET"
printf "\n  %sTotal e2e latency:%s %s%s%s\n" \
  "$C_BOLD" "$C_RESET" "$C_BOLD$C_GREEN" "$elapsed_human" "$C_RESET"

if [[ "$USE_REAL" != "1" ]]; then
  printf "\n  %s%sno API spend — this was the stub adapter.%s\n" "$C_DIM" "$C_BOLD" "$C_RESET"
  printf "  %sFor a real model reply, set ANTHROPIC_API_KEY and pass --real.%s\n" "$C_DIM" "$C_RESET"
fi

divider
exit 0
