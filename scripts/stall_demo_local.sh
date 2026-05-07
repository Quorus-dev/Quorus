#!/usr/bin/env bash
# scripts/stall_demo_local.sh - pre-warm 4 cross-vendor agents for the stall.
#
#   bash scripts/stall_demo_local.sh {start|status|stop|reset}
#
# State:
#   /tmp/stall-pids.json                relay + daemon PIDs
#   /tmp/stall-relay.{log,port,secret}  local relay state
#   /tmp/stall-<agent>.log              per-daemon log
#
# Strategy: spawn a LOCAL quorus-relay + 4 reflexd daemons (claude, codex,
# gemini, opencode) using REAL vendor CLIs (no stub). Visitor types into
# the TUI; daemons catch @-mentions and the harness OAuth handles auth.

set -u
set -o pipefail
set +m

# --- Config ----------------------------------------------------------------
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ROOM_NAME="${QUORUS_STALL_ROOM:-stall-may7}"
PARENT_NAME="${QUORUS_STALL_HUMAN:-arav}"
PIDS_FILE="/tmp/stall-pids.json"
RELAY_LOG="/tmp/stall-relay.log"
RELAY_PORT_FILE="/tmp/stall-relay.port"
RELAY_SECRET_FILE="/tmp/stall-relay.secret"
RELAY_MSGS_FILE="/tmp/stall-relay.msgs.json"
VENV_PY="$REPO_ROOT/.venv/bin/python3"
RELAY_BIN="$REPO_ROOT/.venv/bin/quorus-relay"
REFLEXD_PY="$REPO_ROOT/scripts/reflexd.py"
ALL_AGENTS=("claude" "codex" "gemini" "opencode")

# --- Output ----------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR-}" ]]; then
  C0=$'\033[0m'; CB=$'\033[1m'; CD=$'\033[2m'
  CR=$'\033[31m'; CG=$'\033[32m'; CY=$'\033[33m'; CC=$'\033[36m'
else
  C0=""; CB=""; CD=""; CR=""; CG=""; CY=""; CC=""
fi
say()  { printf "%s>%s %s\n" "$CC" "$C0" "$1"; }
ok()   { printf "  %s+%s %s\n" "$CG" "$C0" "$1"; }
warn() { printf "  %s!%s %s\n" "$CY" "$C0" "$1" >&2; }
fail() { printf "%sx%s %s%s%s\n" "$CR" "$C0" "$CB" "$1" "$C0" >&2; }

# --- Prereqs ---------------------------------------------------------------
for b in curl jq; do command -v "$b" >/dev/null 2>&1 || { fail "missing $b"; exit 2; }; done
[[ -x "$VENV_PY"    ]] || { fail "missing $VENV_PY (run: python3 -m venv .venv && .venv/bin/pip install -e .)"; exit 2; }
[[ -x "$RELAY_BIN"  ]] || { fail "missing $RELAY_BIN (relay not installed in .venv)"; exit 2; }
[[ -f "$REFLEXD_PY" ]] || { fail "missing $REFLEXD_PY"; exit 2; }

# --- Helpers ---------------------------------------------------------------
read_pids() { [[ -f "$PIDS_FILE" ]] && cat "$PIDS_FILE" || echo '{}'; }
write_pids(){ printf '%s\n' "$1" > "$PIDS_FILE"; }
agent_log() { echo "/tmp/stall-${1}.log"; }
is_alive()  { kill -0 "$1" 2>/dev/null; }

pick_free_port() {
  "$VENV_PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

start_local_relay() {
  local port secret pid
  port="$(pick_free_port)"
  secret="stall-secret-$(date +%s)-$$"
  printf '%s\n' "$port"   > "$RELAY_PORT_FILE"
  printf '%s\n' "$secret" > "$RELAY_SECRET_FILE"
  : > "$RELAY_LOG"
  env PORT="$port" RELAY_SECRET="$secret" MESSAGES_FILE="$RELAY_MSGS_FILE" \
      ALLOW_LEGACY_AUTH=1 QUORUS_STALL_DEMO=1 LOG_LEVEL=INFO \
      "$RELAY_BIN" >"$RELAY_LOG" 2>&1 &
  pid=$!
  disown "$pid" 2>/dev/null || true
  printf '%s' "$pid"
}

wait_for_relay_health() {
  local port="$1" pid="$2" deadline=$(( $(date +%s) + 15 ))
  while [[ $(date +%s) -lt $deadline ]]; do
    if ! is_alive "$pid"; then
      fail "local relay died during startup"; tail -n 30 "$RELAY_LOG" >&2 || true; return 1
    fi
    curl -fsS -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && return 0
    sleep 0.2
  done
  fail "local relay /health did not come up within 15s"
  tail -n 30 "$RELAY_LOG" >&2 || true
  return 1
}

# api METHOD PATH BEARER [BODY] URL
api_call() {
  local m="$1" p="$2" b="$3" body="${4-}" u="$5"
  if [[ -n "$body" ]]; then
    curl -sS --max-time 10 -X "$m" -H "Authorization: Bearer $b" \
      -H 'Content-Type: application/json' -d "$body" "$u$p"
  else
    curl -sS --max-time 10 -X "$m" -H "Authorization: Bearer $b" "$u$p"
  fi
}

ensure_room() {
  local b="$1" u="$2" rooms existing resp
  rooms="$(api_call GET /rooms "$b" "" "$u")"
  existing="$(echo "$rooms" | jq -r --arg n "$ROOM_NAME" '.[]? | select(.name==$n) | .id' | head -1)"
  [[ -n "$existing" ]] && { printf '%s' "$existing"; return 0; }
  resp="$(api_call POST /rooms "$b" \
    "$(jq -nc --arg n "$ROOM_NAME" --arg c "$PARENT_NAME" '{name:$n,created_by:$c}')" "$u")"
  echo "$resp" | jq -r '.id // empty'
}

join_member() {
  local b="$1" u="$2" rid="$3" who="$4"
  api_call POST "/rooms/$rid/join" "$b" \
    "$(jq -nc --arg p "$who" '{participant:$p,role:"member"}')" "$u" \
    >/dev/null 2>&1 || true   # 409 already-joined is fine
}

probe_oauth() {
  case "$1" in
    claude|codex|gemini|opencode) command -v "$1" >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

spawn_daemon() {
  local s="$1" b="$2" u="$3" name logf rt pid
  name="${PARENT_NAME}-${s}"
  logf="$(agent_log "$s")"
  rt="/tmp/stall-runtime-${s}"
  : > "$logf"; mkdir -p "$rt"
  env RELAY_URL="$u" REFLEXD_RELAY_URL="$u" API_KEY="$b" REFLEXD_API_KEY="$b" \
      REFLEXD_PARTICIPANT="$name" REFLEXD_LEGACY_BEARER=1 \
      QUORUS_STALL_DEMO=1 HOME="$HOME" QUORUS_RUNTIME_DIR="$rt" \
      "$VENV_PY" "$REFLEXD_PY" start --debug \
        --participant "$name" --relay-url "$u" \
        >"$logf" 2>&1 &
  pid=$!
  disown "$pid" 2>/dev/null || true
  printf '%s' "$pid"
}

# verify_round_trip BEARER URL ROOM_ID TARGET
# Returns 0 iff TARGET posts a NEW (post-send) reply within 120s.
# Real claude --print first-call latency dominates the budget.
verify_round_trip() {
  local b="$1" u="$2" rid="$3" target="$4"
  local before prompt body resp mid deadline hist after
  before="$(api_call GET "/rooms/$rid/history?limit=50" "$b" "" "$u" \
    | jq -r --arg a "$target" '
        if (type=="array") then . else (.messages // .history // .items // []) end
        | map(select(.from_name==$a and (.message_type//"chat")!="wake_intent")) | length' \
    2>/dev/null || echo 0)"
  before="${before:-0}"
  prompt="@${target} say hi briefly"
  body="$(jq -nc --arg from "$PARENT_NAME" --arg c "$prompt" \
    '{from_name:$from,content:$c,message_type:"chat"}')"
  resp="$(api_call POST "/rooms/$rid/messages" "$b" "$body" "$u")"
  mid="$(echo "$resp" | jq -r '.id // .message_id // empty')"
  if [[ -z "$mid" ]]; then fail "verify message post failed: $resp"; return 1; fi
  api_call POST /v1/triage "$b" \
    "$(jq -nc --arg rid "$rid" --arg mid "$mid" --arg from "$PARENT_NAME" --arg c "$prompt" \
       '{room_id:$rid,message_id:$mid,from_name:$from,content:$c,message_type:"chat"}')" \
    "$u" >/dev/null 2>&1 || true
  deadline=$(( $(date +%s) + 120 ))
  while [[ $(date +%s) -lt $deadline ]]; do
    hist="$(api_call GET "/rooms/$rid/history?limit=50" "$b" "" "$u")"
    after="$(echo "$hist" | jq -r --arg a "$target" '
        if (type=="array") then . else (.messages // .history // .items // []) end
        | map(select(.from_name==$a and (.message_type//"chat")!="wake_intent")) | length' \
      2>/dev/null || echo 0)"
    [[ "${after:-0}" -gt "$before" ]] && return 0
    sleep 2
  done
  return 1
}

stop_all() {
  local pids name pid
  pids="$(read_pids)"
  [[ "$pids" == "{}" ]] && say "no PID file -- nothing to stop"
  while IFS=$'\t' read -r name pid; do
    [[ -z "$name" ]] && continue
    if is_alive "$pid"; then
      kill "$pid" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8; do is_alive "$pid" || break; sleep 0.2; done
      kill -9 "$pid" 2>/dev/null || true
      ok "stopped $name (pid $pid)"
    else
      ok "$name (pid $pid) already dead"
    fi
  done < <(echo "$pids" | jq -r 'to_entries[]? | "\(.key)\t\(.value)"')
  pkill -f "QUORUS_STALL_DEMO=1" 2>/dev/null || true
  rm -f "$PIDS_FILE" "$RELAY_PORT_FILE" "$RELAY_SECRET_FILE" "$RELAY_MSGS_FILE"
  rm -rf /tmp/stall-runtime-* 2>/dev/null || true
}

# --- Commands --------------------------------------------------------------
cmd_start() {
  if [[ -f "$PIDS_FILE" ]]; then
    local prior any=0
    prior="$(read_pids)"
    while IFS=$'\t' read -r _ pid; do is_alive "$pid" && any=1; done \
      < <(echo "$prior" | jq -r 'to_entries[]? | "\(.key)\t\(.value)"')
    if [[ $any -eq 1 ]]; then
      warn "stall daemons already running -- use 'reset' to redo"
      cmd_status; return 0
    fi
  fi

  say "starting local quorus-relay"
  local relay_pid port secret relay_url
  relay_pid="$(start_local_relay)"
  port="$(cat "$RELAY_PORT_FILE")"
  secret="$(cat "$RELAY_SECRET_FILE")"
  if ! wait_for_relay_health "$port" "$relay_pid"; then
    kill "$relay_pid" 2>/dev/null || true; exit 1
  fi
  relay_url="http://127.0.0.1:$port"
  ok "local relay healthy on :$port"

  say "creating demo room '$ROOM_NAME'"
  local room_id
  room_id="$(ensure_room "$secret" "$relay_url")"
  if [[ -z "$room_id" ]]; then
    fail "could not create room"; kill "$relay_pid" 2>/dev/null; exit 1
  fi
  ok "room $ROOM_NAME id=$room_id"

  say "joining $PARENT_NAME (human)"
  join_member "$secret" "$relay_url" "$room_id" "$PARENT_NAME"
  ok "joined: $PARENT_NAME"

  local active=() skipped=() s
  for s in "${ALL_AGENTS[@]}"; do
    if probe_oauth "$s"; then active+=("$s"); else skipped+=("$s"); fi
  done
  if [[ ${#active[@]} -eq 0 ]]; then
    fail "no host CLIs found on PATH"; kill "$relay_pid" 2>/dev/null; exit 1
  fi
  ok "active harnesses: ${active[*]}"
  [[ ${#skipped[@]} -gt 0 ]] && warn "skipping (no binary): ${skipped[*]}"

  say "joining ${#active[@]} agent participant(s)"
  for s in "${active[@]}"; do
    join_member "$secret" "$relay_url" "$room_id" "${PARENT_NAME}-${s}"
    ok "joined: ${PARENT_NAME}-${s}"
  done

  say "spawning reflexd daemons (REAL mode, no stub)"
  local pids pid
  pids="$(jq -nc --argjson p "$relay_pid" '{relay:$p}')"
  for s in "${active[@]}"; do
    pid="$(spawn_daemon "$s" "$secret" "$relay_url")"
    pids="$(echo "$pids" | jq --arg n "${PARENT_NAME}-${s}" --argjson p "$pid" '.[$n]=$p')"
    ok "${PARENT_NAME}-${s} pid=$pid log=$(agent_log "$s")"
  done
  write_pids "$pids"

  # Wait for ALL daemons to SSE-connect, not just one. claude takes ~1-2s
  # longer than the others to probe vendor CLIs; if we send the verify
  # message before claude's queue is registered, the demo looks broken.
  say "waiting up to 25s for ALL ${#active[@]} daemon(s) to SSE-connect"
  local deadline=$(( $(date +%s) + 25 ))
  local connected=0
  while [[ $(date +%s) -lt $deadline ]]; do
    connected=0
    for s in "${active[@]}"; do
      grep -q "sse connected" "$(agent_log "$s")" 2>/dev/null && connected=$(( connected + 1 ))
    done
    [[ $connected -ge ${#active[@]} ]] && break
    sleep 0.5
  done
  if [[ $connected -lt ${#active[@]} ]]; then
    warn "only $connected/${#active[@]} daemons connected -- continuing"
  else
    ok "all $connected daemons connected to SSE"
  fi
  sleep 1   # let the relay finish wiring up the queues

  local target=""
  for s in "${active[@]}"; do
    [[ "$s" == "claude" ]] && { target="${PARENT_NAME}-claude"; break; }
  done
  [[ -z "$target" ]] && target="${PARENT_NAME}-${active[0]}"

  say "verification: posting '@${target} say hi briefly' (up to 120s)"
  local verify="FAIL"
  if verify_round_trip "$secret" "$relay_url" "$room_id" "$target"; then
    ok "real LLM reply received from $target"
    verify="PASS"
  else
    warn "no reply from $target within 120s -- inspect $(agent_log "${target##*-}")"
    warn "demo may still work for visitor @-mentions; check status"
  fi

  printf '\n%s%sSTALL READY%s\n' "$CB" "$CG" "$C0"
  printf '  relay url:   %s\n' "$relay_url"
  printf '  room:        %s\n' "$ROOM_NAME"
  printf '  human:       @%s\n' "$PARENT_NAME"
  printf '  agents:     '
  for s in "${active[@]}"; do printf ' @%s' "${PARENT_NAME}-${s}"; done
  printf '\n'
  printf '  logs:        /tmp/stall-<agent>.log    (relay: %s)\n' "$RELAY_LOG"
  printf '  status:      bash scripts/stall_demo_local.sh status\n'
  printf '  stop:        bash scripts/stall_demo_local.sh stop\n'
  printf '  verify:      %s\n\n' "$verify"
  printf '  %slaunch the TUI:%s  %sQUORUS_RELAY_URL=%s quorus chat %s%s\n' \
    "$CB" "$C0" "$CC" "$relay_url" "$ROOM_NAME" "$C0"
  printf '  %s(env var points the TUI at the local relay; profile is unchanged)%s\n' \
    "$CD" "$C0"
}

cmd_status() {
  if [[ ! -f "$PIDS_FILE" ]]; then
    say "no PID file -- daemons not started"; return 1
  fi
  local pids name pid alive logf last
  pids="$(read_pids)"
  printf '%-28s %-8s %-8s %s\n' "name" "pid" "alive" "last log line"
  printf '%s\n' "----------------------------------------------------------------------"
  while IFS=$'\t' read -r name pid; do
    [[ -z "$name" ]] && continue
    alive="no"; is_alive "$pid" && alive="yes"
    if [[ "$name" == "relay" ]]; then logf="$RELAY_LOG"; else logf="$(agent_log "${name##*-}")"; fi
    last="-"
    [[ -f "$logf" ]] && last="$(tail -n 1 "$logf" 2>/dev/null | cut -c1-80)"
    printf '%-28s %-8s %-8s %s\n' "$name" "$pid" "$alive" "$last"
  done < <(echo "$pids" | jq -r 'to_entries[]? | "\(.key)\t\(.value)"')
}

cmd_stop()  { stop_all; ok "all stall processes stopped"; }
cmd_reset() { stop_all; cmd_start; }

# --- Dispatch --------------------------------------------------------------
case "${1:-start}" in
  start)  cmd_start ;;
  status) cmd_status ;;
  stop)   cmd_stop ;;
  reset)  cmd_reset ;;
  *) printf 'Usage: %s {start|status|stop|reset}\n' "$0" >&2; exit 2 ;;
esac
