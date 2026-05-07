#!/usr/bin/env bash
# scripts/stall_demo_local.sh - pre-warm 4 cross-vendor agents for the stall.
#
#   bash scripts/stall_demo_local.sh start [--remote|--local]
#   bash scripts/stall_demo_local.sh {status|stop|reset} [--remote|--local]
#
# Modes:
#   LOCAL  (default)        Spawn a 127.0.0.1 quorus-relay + 4 reflexd daemons.
#                           Single-laptop self-contained demo.
#   REMOTE (--remote)       Use https://quorus-relay.fly.dev as the relay.
#                           Mints arav-claude/-codex/-gemini/-opencode against
#                           PRODUCTION using the parent api_key in the active
#                           profile. Same Mac, prod relay = multi-laptop ready.
#
# State:
#   /tmp/stall-mode.txt                 'local' | 'remote'
#   /tmp/stall-pids.json                relay (local only) + daemon PIDs
#   /tmp/stall-relay.{log,port,secret}  local relay state (local mode)
#   /tmp/stall-remote.json              prod relay url + per-agent api_key map
#   /tmp/stall-<agent>.log              per-daemon log

set -u
set -o pipefail
set +m

# --- Config ----------------------------------------------------------------
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ROOM_NAME="${QUORUS_STALL_ROOM:-stall-may7}"
PARENT_NAME="${QUORUS_STALL_HUMAN:-arav}"
REMOTE_RELAY_URL="${QUORUS_STALL_REMOTE_URL:-https://quorus-relay.fly.dev}"
PROFILE_FILE="${QUORUS_PROFILE_FILE:-$HOME/.quorus/profiles/default.json}"
PIDS_FILE="/tmp/stall-pids.json"
MODE_FILE="/tmp/stall-mode.txt"
REMOTE_FILE="/tmp/stall-remote.json"
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
[[ -f "$REFLEXD_PY" ]] || { fail "missing $REFLEXD_PY"; exit 2; }

# --- Mode handling ---------------------------------------------------------
parse_mode_flag() {
  # Parse --remote / --local out of "$@" (non-destructive). Echoes 'remote' or 'local'.
  for a in "$@"; do
    case "$a" in
      --remote) echo "remote"; return 0 ;;
      --local)  echo "local";  return 0 ;;
    esac
  done
  if [[ -f "$MODE_FILE" ]]; then cat "$MODE_FILE"; return 0; fi
  echo "local"
}

write_mode() { printf '%s\n' "$1" > "$MODE_FILE"; }

# --- Helpers (mode-agnostic) ----------------------------------------------
read_pids() { [[ -f "$PIDS_FILE" ]] && cat "$PIDS_FILE" || echo '{}'; }
write_pids(){ printf '%s\n' "$1" > "$PIDS_FILE"; }
agent_log() { echo "/tmp/stall-${1}.log"; }
is_alive()  { kill -0 "$1" 2>/dev/null; }

pick_free_port() {
  "$VENV_PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

# api METHOD PATH BEARER [BODY] URL
api_call() {
  local m="$1" p="$2" b="$3" body="${4-}" u="$5"
  if [[ -n "$body" ]]; then
    curl -sS --max-time 15 -X "$m" -H "Authorization: Bearer $b" \
      -H 'Content-Type: application/json' -d "$body" "$u$p"
  else
    curl -sS --max-time 15 -X "$m" -H "Authorization: Bearer $b" "$u$p"
  fi
}

# api_status METHOD PATH BEARER [BODY] URL  -> echoes HTTP status code only
api_status() {
  local m="$1" p="$2" b="$3" body="${4-}" u="$5"
  if [[ -n "$body" ]]; then
    curl -sS -o /dev/null -w '%{http_code}' --max-time 15 -X "$m" \
      -H "Authorization: Bearer $b" -H 'Content-Type: application/json' \
      -d "$body" "$u$p"
  else
    curl -sS -o /dev/null -w '%{http_code}' --max-time 15 -X "$m" \
      -H "Authorization: Bearer $b" "$u$p"
  fi
}

ensure_room() {
  local b="$1" u="$2" rooms existing resp
  rooms="$(api_call GET /rooms "$b" "" "$u")"
  existing="$(echo "$rooms" | jq -r --arg n "$ROOM_NAME" '.[]? | select(.name==$n) | .id' 2>/dev/null | head -1)"
  [[ -n "$existing" ]] && { printf '%s' "$existing"; return 0; }
  resp="$(api_call POST /rooms "$b" \
    "$(jq -nc --arg n "$ROOM_NAME" --arg c "$PARENT_NAME" '{name:$n,created_by:$c}')" "$u")"
  echo "$resp" | jq -r '.id // empty' 2>/dev/null
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
  mid="$(echo "$resp" | jq -r '.id // .message_id // empty' 2>/dev/null)"
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
  rm -f "$PIDS_FILE" "$RELAY_PORT_FILE" "$RELAY_SECRET_FILE" "$RELAY_MSGS_FILE" \
        "$REMOTE_FILE" "$MODE_FILE"
  rm -rf /tmp/stall-runtime-* 2>/dev/null || true
}

# --- LOCAL-mode helpers ----------------------------------------------------
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

# --- REMOTE-mode helpers ---------------------------------------------------
remote_load_profile() {
  # Echoes "$api_key\t$instance_name\t$relay_url" — non-empty only on success.
  if [[ ! -f "$PROFILE_FILE" ]]; then
    fail "profile not found: $PROFILE_FILE -- run 'quorus join <code>' first"
    return 1
  fi
  local key inst url
  key="$(jq -r '.api_key // empty' "$PROFILE_FILE")"
  inst="$(jq -r '.instance_name // empty' "$PROFILE_FILE")"
  url="$(jq -r --arg d "$REMOTE_RELAY_URL" '.relay_url // $d' "$PROFILE_FILE")"
  if [[ -z "$key" ]]; then
    fail "profile $PROFILE_FILE has no api_key -- run 'quorus join <code>' first"
    return 1
  fi
  printf '%s\t%s\t%s' "$key" "${inst:-arav}" "${url:-$REMOTE_RELAY_URL}"
}

remote_health_check() {
  local url="$1" code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$url/health" 2>/dev/null || echo "000")"
  if [[ "$code" != "200" ]]; then
    fail "production relay /health returned $code -- prod degraded"
    warn "fall back to local mode: bash scripts/stall_demo_local.sh start --local"
    return 1
  fi
  return 0
}

# remote_exchange_jwt PARENT_KEY URL -> echoes JWT or empty
remote_exchange_jwt() {
  local pk="$1" url="$2" body resp
  body="$(jq -nc --arg k "$pk" '{api_key:$k}')"
  resp="$(curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' \
    -d "$body" "$url/v1/auth/token" 2>/dev/null || echo "")"
  echo "$resp" | jq -r '.token // empty' 2>/dev/null
}

# remote_mint_child PARENT_KEY URL SUFFIX -> echoes "$agent_name\t$api_key" or empty on failure
remote_mint_child() {
  local pk="$1" url="$2" suffix="$3" body resp code
  body="$(jq -nc --arg s "$suffix" '{suffix:$s}')"
  resp="$(curl -sS --max-time 15 -o /tmp/.stall-mint.body -w '%{http_code}' \
    -X POST -H "Authorization: Bearer $pk" -H 'Content-Type: application/json' \
    -d "$body" "$url/v1/auth/register-agent" 2>/dev/null || echo "000")"
  code="$resp"
  if [[ "$code" != "200" ]]; then
    return 1
  fi
  jq -r '. as $d | "\($d.agent_name // "")\t\($d.api_key // "")"' /tmp/.stall-mint.body 2>/dev/null
}

# --- Commands --------------------------------------------------------------
cmd_start() {
  local mode; mode="$(parse_mode_flag "$@")"
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

  if [[ "$mode" == "remote" ]]; then
    cmd_start_remote
  else
    cmd_start_local
  fi
}

cmd_start_local() {
  write_mode local
  [[ -x "$RELAY_BIN"  ]] || { fail "missing $RELAY_BIN (relay not installed in .venv)"; exit 2; }

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

  start_room_and_daemons "$secret" "$relay_url" "$relay_pid" "local"
}

cmd_start_remote() {
  write_mode remote
  say "loading profile $PROFILE_FILE"
  local row parent_key parent_inst relay_url parent_jwt
  row="$(remote_load_profile)" || exit 1
  IFS=$'\t' read -r parent_key parent_inst relay_url <<<"$row"
  PARENT_NAME="${parent_inst:-$PARENT_NAME}"
  ok "parent: @$PARENT_NAME  relay: $relay_url"

  say "checking production relay health"
  remote_health_check "$relay_url" || exit 1
  ok "production relay healthy"

  say "exchanging parent api_key for JWT"
  parent_jwt="$(remote_exchange_jwt "$parent_key" "$relay_url")"
  if [[ -z "$parent_jwt" ]]; then
    fail "/v1/auth/token rejected the parent api_key -- profile may be stale"
    warn "re-run 'quorus join <code>' to refresh the api_key"
    rm -f "$MODE_FILE"
    exit 1
  fi
  ok "JWT obtained (len=${#parent_jwt})"

  say "minting 4 child api_keys against PRODUCTION"
  # bash 3.2 on macOS has no associative arrays; accumulate in a JSON object instead.
  local active=() skipped=() s row2 agent_name agent_key fail_count=0
  local remote_json
  remote_json="$(jq -nc --arg url "$relay_url" --arg pk "$parent_key" --arg human "$PARENT_NAME" \
    '{relay_url:$url,parent_key:$pk,human:$human,agents:{}}')"
  for s in "${ALL_AGENTS[@]}"; do
    if ! probe_oauth "$s"; then
      skipped+=("$s"); continue
    fi
    row2="$(remote_mint_child "$parent_key" "$relay_url" "$s" || true)"
    if [[ -n "$row2" ]]; then
      IFS=$'\t' read -r agent_name agent_key <<<"$row2"
      if [[ -n "$agent_name" && -n "$agent_key" ]]; then
        remote_json="$(echo "$remote_json" \
          | jq --arg s "$s" --arg n "$agent_name" --arg k "$agent_key" \
              '.agents[$s]={name:$n,api_key:$k}')"
        active+=("$s")
        ok "minted: @$agent_name"
        continue
      fi
    fi
    fail_count=$(( fail_count + 1 ))
    warn "mint failed for suffix=$s (relay 5xx?)"
  done

  if [[ ${#active[@]} -eq 0 ]]; then
    fail "production relay is degraded (could not mint any child keys)"
    warn "fall back to local mode for single-laptop demo:"
    warn "  bash scripts/stall_demo_local.sh start --local"
    rm -f "$MODE_FILE"
    exit 1
  fi
  if [[ $fail_count -gt 0 ]]; then
    warn "$fail_count/${#ALL_AGENTS[@]} mints failed -- continuing with ${#active[@]} agent(s)"
  fi
  [[ ${#skipped[@]} -gt 0 ]] && warn "skipping (no binary): ${skipped[*]}"

  # Persist the prod state for status/stop and aarya cross-reference.
  printf '%s\n' "$remote_json" > "$REMOTE_FILE"

  # Verify JWT can list rooms (catches relay-side 5xx unrelated to mint).
  say "verifying JWT against prod /rooms"
  local rooms_code
  rooms_code="$(api_status GET /rooms "$parent_jwt" "" "$relay_url")"
  if [[ "$rooms_code" != "200" ]]; then
    fail "GET /rooms returned $rooms_code with JWT"
    warn "production is in a degraded state -- recommend --local fallback"
    rm -f "$MODE_FILE" "$REMOTE_FILE"
    exit 1
  fi
  ok "JWT accepted by /rooms"

  # Room create & message-post use JWT (per Fly v22 enforcement).
  start_room_and_daemons_remote "$parent_jwt" "$relay_url" "$remote_json"
}

# Local-mode wiring (preserves prior behavior)
start_room_and_daemons() {
  local secret="$1" relay_url="$2" relay_pid="$3" mode="$4"

  say "creating demo room '$ROOM_NAME'"
  local room_id
  room_id="$(ensure_room "$secret" "$relay_url")"
  if [[ -z "$room_id" ]]; then
    fail "could not create room"
    [[ -n "$relay_pid" ]] && kill "$relay_pid" 2>/dev/null
    exit 1
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
    fail "no host CLIs found on PATH"
    [[ -n "$relay_pid" ]] && kill "$relay_pid" 2>/dev/null
    exit 1
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
  if [[ -n "$relay_pid" ]]; then
    pids="$(jq -nc --argjson p "$relay_pid" '{relay:$p}')"
  else
    pids="$(jq -nc '{}')"
  fi
  for s in "${active[@]}"; do
    pid="$(spawn_daemon "$s" "$secret" "$relay_url")"
    pids="$(echo "$pids" | jq --arg n "${PARENT_NAME}-${s}" --argjson p "$pid" '.[$n]=$p')"
    ok "${PARENT_NAME}-${s} pid=$pid log=$(agent_log "$s")"
  done
  write_pids "$pids"

  finish_start "$secret" "$relay_url" "$room_id" "${active[@]}"
}

# Remote-mode wiring: parent JWT for admin ops, per-child api_keys for daemons
start_room_and_daemons_remote() {
  local parent_jwt="$1" relay_url="$2" remote_json="$3"
  local active=() s

  while IFS= read -r s; do
    [[ -z "$s" ]] && continue; active+=("$s")
  done < <(echo "$remote_json" | jq -r '.agents | keys[]')

  say "ensuring room '$ROOM_NAME' on production"
  local room_id
  room_id="$(ensure_room "$parent_jwt" "$relay_url")"
  if [[ -z "$room_id" || "$room_id" == "null" ]]; then
    fail "could not create/find room on production (POST /rooms may be 5xx)"
    warn "fall back to local mode: bash scripts/stall_demo_local.sh start --local"
    rm -f "$MODE_FILE" "$REMOTE_FILE"; exit 1
  fi
  ok "room $ROOM_NAME id=$room_id"

  say "joining $PARENT_NAME (human)"
  join_member "$parent_jwt" "$relay_url" "$room_id" "$PARENT_NAME"
  ok "joined: $PARENT_NAME"

  say "joining ${#active[@]} agent participant(s)"
  local agent_name
  for s in "${active[@]}"; do
    agent_name="$(echo "$remote_json" | jq -r --arg s "$s" '.agents[$s].name')"
    # Use parent JWT to join children (tenant admin can add members)
    join_member "$parent_jwt" "$relay_url" "$room_id" "$agent_name"
    ok "joined: $agent_name"
  done

  say "spawning reflexd daemons (REAL mode against PRODUCTION)"
  local pids pid
  pids="$(jq -nc '{}')"
  for s in "${active[@]}"; do
    agent_name="$(echo "$remote_json" | jq -r --arg s "$s" '.agents[$s].name')"
    agent_key="$(echo "$remote_json" | jq -r --arg s "$s" '.agents[$s].api_key')"
    pid="$(spawn_daemon_remote "$s" "$agent_key" "$relay_url" "$agent_name")"
    pids="$(echo "$pids" | jq --arg n "$agent_name" --argjson p "$pid" '.[$n]=$p')"
    ok "$agent_name pid=$pid log=$(agent_log "$s")"
  done
  write_pids "$pids"

  finish_start "$parent_key" "$relay_url" "$room_id" "${active[@]}"
}

# spawn_daemon_remote — like spawn_daemon, but uses a fully-qualified name
spawn_daemon_remote() {
  local s="$1" b="$2" u="$3" name="$4" logf rt pid
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

finish_start() {
  local b="$1" relay_url="$2" room_id="$3"; shift 3
  local active=("$@")

  say "waiting up to 25s for ALL ${#active[@]} daemon(s) to SSE-connect"
  local deadline=$(( $(date +%s) + 25 ))
  local connected=0 s
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
  sleep 1

  local target=""
  for s in "${active[@]}"; do
    [[ "$s" == "claude" ]] && { target="${PARENT_NAME}-claude"; break; }
  done
  [[ -z "$target" ]] && target="${PARENT_NAME}-${active[0]}"

  say "verification: posting '@${target} say hi briefly' (up to 120s)"
  local verify="FAIL"
  if verify_round_trip "$b" "$relay_url" "$room_id" "$target"; then
    ok "real LLM reply received from $target"
    verify="PASS"
  else
    warn "no reply from $target within 120s -- inspect $(agent_log "${target##*-}")"
    warn "demo may still work for visitor @-mentions; check status"
  fi

  local mode=""; [[ -f "$MODE_FILE" ]] && mode="$(cat "$MODE_FILE")"

  printf '\n%s%sSTALL READY%s\n' "$CB" "$CG" "$C0"
  printf '  mode:        %s\n' "${mode:-local}"
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
  if [[ "${mode:-local}" == "remote" ]]; then
    printf '  %slaunch the TUI:%s  %squorus chat %s%s\n' \
      "$CB" "$C0" "$CC" "$ROOM_NAME" "$C0"
    printf '  %s(uses your active profile -> %s)%s\n' \
      "$CD" "$relay_url" "$C0"
  else
    printf '  %slaunch the TUI:%s  %sQUORUS_RELAY_URL=%s quorus chat %s%s\n' \
      "$CB" "$C0" "$CC" "$relay_url" "$ROOM_NAME" "$C0"
    printf '  %s(env var points the TUI at the local relay; profile is unchanged)%s\n' \
      "$CD" "$C0"
  fi
}

cmd_status() {
  local mode=""; [[ -f "$MODE_FILE" ]] && mode="$(cat "$MODE_FILE")"
  printf 'mode: %s\n' "${mode:-(none)}"
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
  if [[ "$mode" == "remote" && -f "$REMOTE_FILE" ]]; then
    printf '\nremote relay: %s\n' "$(jq -r .relay_url "$REMOTE_FILE")"
  fi
}

cmd_stop()  { stop_all; ok "all stall processes stopped"; }
cmd_reset() {
  local mode; mode="$(parse_mode_flag "$@")"
  stop_all
  if [[ "$mode" == "remote" ]]; then cmd_start_remote; else cmd_start_local; fi
}

# --- Dispatch --------------------------------------------------------------
case "${1:-start}" in
  start)  shift; cmd_start "$@" ;;
  status) shift; cmd_status "$@" ;;
  stop)   shift; cmd_stop "$@" ;;
  reset)  shift; cmd_reset "$@" ;;
  *) printf 'Usage: %s {start|status|stop|reset} [--remote|--local]\n' "$0" >&2; exit 2 ;;
esac
