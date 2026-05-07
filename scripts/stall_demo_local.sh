#!/usr/bin/env bash
# scripts/stall_demo_local.sh — single-command pre-warm for the stall demo.
# Visitors type into Arav's TUI; reflexd daemons running here do the work.
#
#   bash scripts/stall_demo_local.sh start    # idempotent spin-up
#   bash scripts/stall_demo_local.sh status   # daemon health
#   bash scripts/stall_demo_local.sh stop     # kill cleanly
#   bash scripts/stall_demo_local.sh reset    # stop + restart fresh
#
# State on disk:
#   /tmp/stall-pids.json             — relay + daemon PIDs
#   /tmp/stall-relay.{log,port,secret} — local relay state
#   /tmp/stall-<agent>.log           — per-daemon log
#
# Strategy: spin up a LOCAL quorus-relay (same binary as production) and
# point all 4 reflexd daemons + the TUI at it. No new API keys; uses the
# legacy admin bearer pattern that demo_reflex.sh proved works. Visitors
# never know the relay is local — they just type into the TUI and see
# real LLM replies stream back.

set -u
set -o pipefail
set +m  # silence "Killed: 9" job-control banners

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
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

# All four candidates. Each must have its CLI installed AND OAuth'd.
ALL_AGENTS=("claude" "codex" "gemini" "opencode")

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR-}" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_CYAN=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""
fi
say()  { printf "%s>%s %s\n" "$C_CYAN" "$C_RESET" "$1"; }
ok()   { printf "  %s+%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
warn() { printf "  %s!%s %s\n" "$C_YELLOW" "$C_RESET" "$1" >&2; }
fail() { printf "%sx%s %s%s%s\n" "$C_RED" "$C_RESET" "$C_BOLD" "$1" "$C_RESET" >&2; }

# ---------------------------------------------------------------------------
# Prereqs
# ---------------------------------------------------------------------------
require_bin() {
  command -v "$1" >/dev/null 2>&1 || { fail "missing $1 on PATH"; exit 2; }
}
require_bin curl
require_bin jq

[[ -x "$VENV_PY"   ]] || { fail "missing $VENV_PY -- run 'python3 -m venv .venv && .venv/bin/pip install -e .'"; exit 2; }
[[ -x "$RELAY_BIN" ]] || { fail "missing $RELAY_BIN -- relay not installed in .venv";                          exit 2; }
[[ -f "$REFLEXD_PY" ]] || { fail "missing $REFLEXD_PY";                                                          exit 2; }

# ---------------------------------------------------------------------------
# PID tracking
# ---------------------------------------------------------------------------
read_pids() { [[ -f "$PIDS_FILE" ]] && cat "$PIDS_FILE" || echo '{}'; }
write_pids(){ printf '%s\n' "$1" > "$PIDS_FILE"; }
agent_log() { echo "/tmp/stall-${1}.log"; }
is_alive()  { kill -0 "$1" 2>/dev/null; }

# ---------------------------------------------------------------------------
# Relay helpers
# ---------------------------------------------------------------------------
pick_free_port() {
  "$VENV_PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

start_local_relay() {
  local port secret
  port="$(pick_free_port)"
  secret="stall-secret-$(date +%s)-$$"
  printf '%s\n' "$port"   > "$RELAY_PORT_FILE"
  printf '%s\n' "$secret" > "$RELAY_SECRET_FILE"
  : > "$RELAY_LOG"
  env \
    PORT="$port" \
    RELAY_SECRET="$secret" \
    MESSAGES_FILE="$RELAY_MSGS_FILE" \
    ALLOW_LEGACY_AUTH=1 \
    QUORUS_STALL_DEMO=1 \
    LOG_LEVEL=INFO \
    "$RELAY_BIN" >"$RELAY_LOG" 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  printf '%s' "$pid"
}

wait_for_relay_health() {
  local port="$1" pid="$2"
  local deadline=$(( $(date +%s) + 15 ))
  while [[ $(date +%s) -lt $deadline ]]; do
    if ! is_alive "$pid"; then
      fail "local relay died during startup"
      tail -n 30 "$RELAY_LOG" >&2 || true
      return 1
    fi
    if curl -fsS -m 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  fail "local relay /health did not come up within 15s"
  tail -n 30 "$RELAY_LOG" >&2 || true
  return 1
}

# api METHOD PATH BEARER [JSON_BODY]
api_call() {
  local method="$1" path="$2" bearer="$3" body="${4-}" url="$5"
  if [[ -n "$body" ]]; then
    curl -sS --max-time 10 -X "$method" \
      -H "Authorization: Bearer $bearer" -H 'Content-Type: application/json' \
      -d "$body" "$url$path"
  else
    curl -sS --max-time 10 -X "$method" \
      -H "Authorization: Bearer $bearer" "$url$path"
  fi
}

ensure_room() {
  local bearer="$1" url="$2" rooms existing resp
  rooms="$(api_call GET /rooms "$bearer" "" "$url")"
  existing="$(echo "$rooms" | jq -r --arg n "$ROOM_NAME" '.[]? | select(.name==$n) | .id' | head -1)"
  if [[ -n "$existing" ]]; then
    printf '%s' "$existing" ; return 0
  fi
  resp="$(api_call POST /rooms "$bearer" \
    "$(jq -nc --arg n "$ROOM_NAME" --arg c "$PARENT_NAME" '{name:$n, created_by:$c}')" \
    "$url")"
  echo "$resp" | jq -r '.id // empty'
}

join_member() {
  local bearer="$1" url="$2" room_id="$3" who="$4"
  api_call POST "/rooms/$room_id/join" "$bearer" \
    "$(jq -nc --arg p "$who" '{participant:$p, role:"member"}')" "$url" \
    >/dev/null 2>&1 || true   # 409 on already-joined is fine
}

probe_oauth() {
  case "$1" in
    claude)   command -v claude   >/dev/null 2>&1 ;;
    codex)    command -v codex    >/dev/null 2>&1 ;;
    gemini)   command -v gemini   >/dev/null 2>&1 ;;
    opencode) command -v opencode >/dev/null 2>&1 ;;
    *)        return 1 ;;
  esac
}

spawn_daemon() {
  local suffix="$1" bearer="$2" url="$3"
  local agent_name="${PARENT_NAME}-${suffix}"
  local logf rt_dir pid
  logf="$(agent_log "$suffix")"
  : > "$logf"
  rt_dir="/tmp/stall-runtime-${suffix}"
  mkdir -p "$rt_dir"
  env \
    RELAY_URL="$url" \
    REFLEXD_RELAY_URL="$url" \
    API_KEY="$bearer" \
    REFLEXD_API_KEY="$bearer" \
    REFLEXD_PARTICIPANT="$agent_name" \
    REFLEXD_LEGACY_BEARER=1 \
    QUORUS_STALL_DEMO=1 \
    HOME="$HOME" \
    QUORUS_RUNTIME_DIR="$rt_dir" \
    "$VENV_PY" "$REFLEXD_PY" start --debug \
      --participant "$agent_name" \
      --relay-url "$url" \
      >"$logf" 2>&1 &
  pid=$!
  disown "$pid" 2>/dev/null || true
  printf '%s' "$pid"
}

# ---------------------------------------------------------------------------
# Verification — does ONE daemon reply within 30s?
# ---------------------------------------------------------------------------
verify_round_trip() {
  local bearer="$1" url="$2" room_id="$3" target="$4"
  local prompt="@${target} say hi briefly"
  local body resp msg_id
  body="$(jq -nc --arg from "$PARENT_NAME" --arg c "$prompt" \
    '{from_name:$from, content:$c, message_type:"chat"}')"
  resp="$(api_call POST "/rooms/$room_id/messages" "$bearer" "$body" "$url")"
  msg_id="$(echo "$resp" | jq -r '.id // .message_id // empty')"
  if [[ -z "$msg_id" ]]; then
    fail "could not post verification message: $resp"
    return 1
  fi
  api_call POST /v1/triage "$bearer" \
    "$(jq -nc --arg rid "$room_id" --arg mid "$msg_id" \
       --arg from "$PARENT_NAME" --arg c "$prompt" \
       '{room_id:$rid, message_id:$mid, from_name:$from, content:$c, message_type:"chat"}')" \
    "$url" >/dev/null 2>&1 || true
  local deadline=$(( $(date +%s) + 30 ))
  while [[ $(date +%s) -lt $deadline ]]; do
    local hist replies
    hist="$(api_call GET "/rooms/$room_id/history?limit=30" "$bearer" "" "$url")"
    replies="$(echo "$hist" | jq -r --arg agent "$target" '
      if (type=="array") then . else (.messages // .history // .items // []) end
      | map(select(.from_name==$agent and (.message_type//"chat")!="wake_intent"))
      | length')"
    if [[ "${replies:-0}" -gt 0 ]]; then return 0; fi
    sleep 1
  done
  return 1
}

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
stop_all() {
  local pids name pid
  pids="$(read_pids)"
  if [[ "$pids" == "{}" ]]; then
    say "no PID file -- nothing to stop"
  fi
  while IFS=$'\t' read -r name pid; do
    [[ -z "$name" ]] && continue
    if is_alive "$pid"; then
      kill "$pid" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8; do
        is_alive "$pid" || break
        sleep 0.2
      done
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

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
cmd_start() {
  # Idempotency: if relay PID is alive, treat as already running.
  if [[ -f "$PIDS_FILE" ]]; then
    local prior any_alive=0
    prior="$(read_pids)"
    while IFS=$'\t' read -r _ pid; do
      is_alive "$pid" && any_alive=1
    done < <(echo "$prior" | jq -r 'to_entries[]? | "\(.key)\t\(.value)"')
    if [[ $any_alive -eq 1 ]]; then
      warn "stall daemons already running -- use 'reset' to redo"
      cmd_status
      return 0
    fi
  fi

  say "starting local quorus-relay"
  local relay_pid
  relay_pid="$(start_local_relay)"
  local port secret
  port="$(cat "$RELAY_PORT_FILE")"
  secret="$(cat "$RELAY_SECRET_FILE")"
  if ! wait_for_relay_health "$port" "$relay_pid"; then
    kill "$relay_pid" 2>/dev/null || true
    exit 1
  fi
  local relay_url="http://127.0.0.1:$port"
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

  # Decide which agents have working OAuth.
  local active=() skipped=()
  for s in "${ALL_AGENTS[@]}"; do
    if probe_oauth "$s"; then active+=("$s"); else skipped+=("$s"); fi
  done
  if [[ ${#active[@]} -eq 0 ]]; then
    fail "no host CLIs found on PATH"; kill "$relay_pid" 2>/dev/null; exit 1
  fi
  ok "active harnesses: ${active[*]}"
  if [[ ${#skipped[@]} -gt 0 ]]; then
    warn "skipping (no binary on PATH): ${skipped[*]}"
  fi

  say "joining ${#active[@]} agent participant(s)"
  local s
  for s in "${active[@]}"; do
    join_member "$secret" "$relay_url" "$room_id" "${PARENT_NAME}-${s}"
    ok "joined: ${PARENT_NAME}-${s}"
  done

  say "spawning reflexd daemons (REAL mode, no stub)"
  local pids; pids="$(jq -nc --arg n relay --argjson p "$relay_pid" '{($n):$p}')"
  for s in "${active[@]}"; do
    local pid; pid="$(spawn_daemon "$s" "$secret" "$relay_url")"
    pids="$(echo "$pids" | jq --arg n "${PARENT_NAME}-${s}" --argjson p "$pid" '.[$n]=$p')"
    ok "${PARENT_NAME}-${s} pid=$pid log=$(agent_log "$s")"
  done
  write_pids "$pids"

  say "waiting up to 12s for SSE connect"
  local deadline=$(( $(date +%s) + 12 ))
  local any_connected=0
  while [[ $(date +%s) -lt $deadline ]]; do
    for s in "${active[@]}"; do
      if grep -q "sse connected" "$(agent_log "$s")" 2>/dev/null; then
        any_connected=1; break
      fi
    done
    [[ $any_connected -eq 1 ]] && break
    sleep 0.4
  done
  if [[ $any_connected -ne 1 ]]; then
    warn "no daemon reported SSE connect within 12s -- check logs"
  else
    ok "at least one daemon connected to SSE"
  fi

  # Pick a verification target -- prefer claude (fastest start), else first active.
  local target=""
  for s in "${active[@]}"; do
    if [[ "$s" == "claude" ]]; then target="${PARENT_NAME}-claude"; break; fi
  done
  [[ -z "$target" ]] && target="${PARENT_NAME}-${active[0]}"

  say "verification: posting '@${target} say hi briefly'"
  local verify_status="FAIL"
  if verify_round_trip "$secret" "$relay_url" "$room_id" "$target"; then
    ok "real LLM reply received from $target"
    verify_status="PASS"
  else
    warn "no reply from $target within 30s -- inspect $(agent_log "${target##*-}")"
    warn "demo may still work for visitor @-mentions; check status"
  fi

  printf '\n%s%sSTALL READY%s\n' "$C_BOLD" "$C_GREEN" "$C_RESET"
  printf '  relay url:   %s\n' "$relay_url"
  printf '  room:        %s\n' "$ROOM_NAME"
  printf '  human:       @%s\n' "$PARENT_NAME"
  printf '  agents:     '
  for s in "${active[@]}"; do printf ' @%s' "${PARENT_NAME}-${s}"; done
  printf '\n'
  printf '  logs:        /tmp/stall-<agent>.log    (relay: %s)\n' "$RELAY_LOG"
  printf '  status:      bash scripts/stall_demo_local.sh status\n'
  printf '  stop:        bash scripts/stall_demo_local.sh stop\n'
  printf '  verify:      %s\n\n' "$verify_status"
  printf '  %slaunch the TUI:%s  %sQUORUS_RELAY_URL=%s quorus chat %s%s\n' \
    "$C_BOLD" "$C_RESET" "$C_CYAN" "$relay_url" "$ROOM_NAME" "$C_RESET"
  printf '  %s(env var points the TUI at the local relay; profile is unchanged)%s\n' \
    "$C_DIM" "$C_RESET"
}

cmd_status() {
  if [[ ! -f "$PIDS_FILE" ]]; then
    say "no PID file -- daemons not started"
    return 1
  fi
  local pids
  pids="$(read_pids)"
  printf '%-28s %-8s %-8s %s\n' "name" "pid" "alive" "last log line"
  printf '%s\n' "----------------------------------------------------------------------"
  while IFS=$'\t' read -r name pid; do
    [[ -z "$name" ]] && continue
    local alive="no"; is_alive "$pid" && alive="yes"
    local last="-" logf
    if [[ "$name" == "relay" ]]; then
      logf="$RELAY_LOG"
    else
      logf="$(agent_log "${name##*-}")"
    fi
    if [[ -f "$logf" ]]; then
      last="$(tail -n 1 "$logf" 2>/dev/null | cut -c1-80)"
    fi
    printf '%-28s %-8s %-8s %s\n' "$name" "$pid" "$alive" "$last"
  done < <(echo "$pids" | jq -r 'to_entries[]? | "\(.key)\t\(.value)"')
}

cmd_stop()  { stop_all; ok "all stall processes stopped"; }
cmd_reset() { stop_all; cmd_start; }

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
ACTION="${1:-start}"
case "$ACTION" in
  start)  cmd_start ;;
  status) cmd_status ;;
  stop)   cmd_stop ;;
  reset)  cmd_reset ;;
  *) printf 'Usage: %s {start|status|stop|reset}\n' "$0" >&2; exit 2 ;;
esac
