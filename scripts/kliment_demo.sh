#!/usr/bin/env bash
# kliment_demo.sh - Kliment Demo Night, May 7 2026 (Plan v8: AGENT-NATIVE OS).
# Subcommands: setup post-tasks kill-aarya resume-aarya propose-destructive audit cleanup.
# Modes: --remote (prod relay) or --local (127.0.0.1). See docs/KLIMENT_DEMO_RUNCARD.md.
set -u; set -o pipefail; set +m

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ROOM_NAME="${QUORUS_KLIMENT_ROOM:-kliment-demo}"
PARENT_NAME="${QUORUS_KLIMENT_HUMAN:-arav}"
REMOTE_RELAY_URL="${QUORUS_KLIMENT_REMOTE_URL:-https://quorus-relay.fly.dev}"
PROFILE_FILE="${QUORUS_PROFILE_FILE:-$HOME/.quorus/profiles/default.json}"
KLIMENT_STATE_FILE="/tmp/kliment-state.json"
PIDS_FILE="/tmp/kliment-pids.json"
MODE_FILE="/tmp/kliment-mode.txt"
EVENTS_LOG="/tmp/kliment-events.log"
RELAY_LOG="/tmp/kliment-relay.log"
RELAY_PORT_FILE="/tmp/kliment-relay.port"
RELAY_SECRET_FILE="/tmp/kliment-relay.secret"
RELAY_MSGS_FILE="/tmp/kliment-relay.msgs.json"
VENV_PY="$REPO_ROOT/.venv/bin/python3"
RELAY_BIN="$REPO_ROOT/.venv/bin/quorus-relay"
REFLEXD_PY="$REPO_ROOT/scripts/reflexd.py"

if [[ -t 1 && -z "${NO_COLOR-}" ]]; then
  C0=$'\033[0m'; CB=$'\033[1m'; CR=$'\033[31m'; CG=$'\033[32m'; CY=$'\033[33m'; CC=$'\033[36m'
else
  C0=""; CB=""; CR=""; CG=""; CY=""; CC=""
fi
say()  { printf "%s>%s %s\n" "$CC" "$C0" "$1"; }
ok()   { printf "  %s+%s %s\n" "$CG" "$C0" "$1"; }
warn() { printf "  %s!%s %s\n" "$CY" "$C0" "$1" >&2; }
fail() { printf "%sx%s %s%s%s\n" "$CR" "$C0" "$CB" "$1" "$C0" >&2; }
log_event() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >> "$EVENTS_LOG"; }

for b in curl jq; do command -v "$b" >/dev/null 2>&1 || { fail "missing $b"; exit 2; }; done
[[ -x "$VENV_PY" && -f "$REFLEXD_PY" ]] || { fail "venv or reflexd missing -- run: python3 -m venv .venv && .venv/bin/pip install -e ."; exit 2; }

parse_mode_flag() {
  for a in "$@"; do case "$a" in --remote) echo remote; return;; --local) echo local; return;; esac; done
  [[ -f "$MODE_FILE" ]] && { cat "$MODE_FILE"; return; }
  if [[ -f "$PROFILE_FILE" ]] && jq -e '.api_key' "$PROFILE_FILE" >/dev/null 2>&1; then echo remote; else echo local; fi
}
write_mode() { printf '%s\n' "$1" > "$MODE_FILE"; }

api_call() { local m=$1 p=$2 b=$3 body=${4-} u=$5
  if [[ -n "$body" ]]; then curl -sS --max-time 15 -X "$m" -H "Authorization: Bearer $b" -H 'Content-Type: application/json' -d "$body" "$u$p"
  else curl -sS --max-time 15 -X "$m" -H "Authorization: Bearer $b" "$u$p"; fi
}
api_status() { local m=$1 p=$2 b=$3 body=${4-} u=$5
  if [[ -n "$body" ]]; then curl -sS -o /dev/null -w '%{http_code}' --max-time 15 -X "$m" -H "Authorization: Bearer $b" -H 'Content-Type: application/json' -d "$body" "$u$p"
  else curl -sS -o /dev/null -w '%{http_code}' --max-time 15 -X "$m" -H "Authorization: Bearer $b" "$u$p"; fi
}
is_alive() { kill -0 "$1" 2>/dev/null; }
pick_free_port() { "$VENV_PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
write_state() { printf '%s\n' "$1" > "$KLIMENT_STATE_FILE"; }
state_get() { jq -r --arg k "$1" '.[$k] // empty' "$KLIMENT_STATE_FILE" 2>/dev/null; }
write_pids(){ printf '%s\n' "$1" > "$PIDS_FILE"; }

# Ensure the kliment-demo room exists; print its id.
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
    >/dev/null 2>&1 || true
}

# --- Local relay -----------------------------------------------------------
start_local_relay() {
  local port secret pid
  port="$(pick_free_port)"
  secret="kliment-secret-$(date +%s)-$$"
  printf '%s\n' "$port"   > "$RELAY_PORT_FILE"
  printf '%s\n' "$secret" > "$RELAY_SECRET_FILE"
  : > "$RELAY_LOG"
  env PORT="$port" RELAY_SECRET="$secret" MESSAGES_FILE="$RELAY_MSGS_FILE" \
      ALLOW_LEGACY_AUTH=1 QUORUS_KLIMENT_DEMO=1 LOG_LEVEL=INFO \
      "$RELAY_BIN" >"$RELAY_LOG" 2>&1 &
  pid=$!; disown "$pid" 2>/dev/null || true
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
  fail "local relay /health did not come up within 15s"; return 1
}

# --- Remote helpers --------------------------------------------------------
remote_load_profile() {
  if [[ ! -f "$PROFILE_FILE" ]]; then
    fail "profile not found: $PROFILE_FILE -- run 'quorus join <code>' first"
    return 1
  fi
  local key inst url
  key="$(jq -r '.api_key // empty' "$PROFILE_FILE")"
  inst="$(jq -r '.instance_name // empty' "$PROFILE_FILE")"
  url="$(jq -r --arg d "$REMOTE_RELAY_URL" '.relay_url // $d' "$PROFILE_FILE")"
  if [[ -z "$key" ]]; then
    fail "profile has no api_key -- run 'quorus join <code>' first"; return 1
  fi
  printf '%s\t%s\t%s' "$key" "${inst:-arav}" "${url:-$REMOTE_RELAY_URL}"
}
remote_health_check() {
  local url="$1" code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$url/health" 2>/dev/null || echo "000")"
  [[ "$code" == "200" ]] && return 0
  fail "production relay /health returned $code -- prod degraded (Upstash quota?)"
  warn "fall back to local mode: bash scripts/kliment_demo.sh setup --local"
  return 1
}
remote_exchange_jwt() {
  local pk="$1" url="$2" body resp
  body="$(jq -nc --arg k "$pk" '{api_key:$k}')"
  resp="$(curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' \
    -d "$body" "$url/v1/auth/token" 2>/dev/null || echo "")"
  echo "$resp" | jq -r '.token // empty' 2>/dev/null
}
# remote_mint_child PARENT_KEY URL SUFFIX -> "name\tkey" or empty.
# WARNING: every call to /v1/auth/register-agent revokes the prior key for
# this suffix (memory: quorus_shim_register_agent.md). Only call when we
# really need a fresh key, otherwise reuse from KLIMENT_STATE_FILE.
remote_mint_child() {
  local pk="$1" url="$2" suffix="$3" code
  code="$(curl -sS --max-time 15 -o /tmp/.kliment-mint.body -w '%{http_code}' \
    -X POST -H "Authorization: Bearer $pk" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg s "$suffix" '{suffix:$s}')" \
    "$url/v1/auth/register-agent" 2>/dev/null || echo "000")"
  [[ "$code" != "200" ]] && return 1
  jq -r '. as $d | "\($d.agent_name // "")\t\($d.api_key // "")"' \
    /tmp/.kliment-mint.body 2>/dev/null
}

# --- spawn_daemon (claude only on this Mac) --------------------------------
spawn_claude_daemon() {
  local b="$1" u="$2" name="$3" logf rt pid
  logf="/tmp/kliment-claude.log"; rt="/tmp/kliment-runtime-claude"
  : > "$logf"; mkdir -p "$rt"
  env RELAY_URL="$u" REFLEXD_RELAY_URL="$u" API_KEY="$b" REFLEXD_API_KEY="$b" \
      REFLEXD_PARTICIPANT="$name" REFLEXD_LEGACY_BEARER=1 \
      QUORUS_KLIMENT_DEMO=1 HOME="$HOME" QUORUS_RUNTIME_DIR="$rt" \
      "$VENV_PY" "$REFLEXD_PY" start --debug \
        --participant "$name" --relay-url "$u" >"$logf" 2>&1 &
  pid=$!; disown "$pid" 2>/dev/null || true
  printf '%s' "$pid"
}

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

# --- setup -----------------------------------------------------------------
cmd_setup() {
  local mode; mode="$(parse_mode_flag "$@")"
  log_event "setup mode=$mode"
  : > "$EVENTS_LOG.tmp" 2>/dev/null || true   # ignore
  if [[ "$mode" == "remote" ]]; then setup_remote; else setup_local; fi
}

setup_local() {
  write_mode local
  [[ -x "$RELAY_BIN" ]] || { fail "missing $RELAY_BIN (relay not installed in .venv)"; exit 2; }
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
  [[ -z "$room_id" ]] && { fail "could not create room"; kill "$relay_pid" 2>/dev/null; exit 1; }
  ok "room $ROOM_NAME id=$room_id"

  join_member "$secret" "$relay_url" "$room_id" "$PARENT_NAME"
  ok "joined: $PARENT_NAME"

  local agent_name="${PARENT_NAME}-claude"
  join_member "$secret" "$relay_url" "$room_id" "$agent_name"
  local pid="$(spawn_claude_daemon "$secret" "$relay_url" "$agent_name")"
  ok "$agent_name pid=$pid log=/tmp/kliment-claude.log"

  local pids; pids="$(jq -nc --argjson r "$relay_pid" --arg n "$agent_name" --argjson p "$pid" \
    '{relay:$r} + {($n):$p}')"
  write_pids "$pids"

  local state; state="$(jq -nc \
    --arg mode "local" --arg url "$relay_url" --arg rid "$room_id" \
    --arg human "$PARENT_NAME" --arg agent "$agent_name" \
    --arg auth "$secret" \
    '{mode:$mode,relay_url:$url,room_id:$rid,human:$human,
      agent:$agent,auth:$auth}')"
  write_state "$state"

  finish_setup "$secret" "$relay_url" "$room_id" "$agent_name" "local"
}

setup_remote() {
  write_mode remote
  say "loading profile $PROFILE_FILE"
  local row parent_key parent_inst relay_url parent_jwt
  row="$(remote_load_profile)" || exit 1
  IFS=$'\t' read -r parent_key parent_inst relay_url <<<"$row"
  PARENT_NAME="${parent_inst:-$PARENT_NAME}"
  ok "parent: @$PARENT_NAME  relay: $relay_url"
  remote_health_check "$relay_url" || exit 1
  parent_jwt="$(remote_exchange_jwt "$parent_key" "$relay_url")"
  if [[ -z "$parent_jwt" ]]; then
    fail "/v1/auth/token rejected api_key -- re-run 'quorus join <code>'"
    rm -f "$MODE_FILE"; exit 1
  fi
  ok "JWT obtained (len=${#parent_jwt})"

  # Reuse existing claude key if state file has one and it still authenticates.
  local claude_name claude_key existing_key
  existing_key="$(state_get auth_claude)"
  if [[ -n "$existing_key" ]] \
      && [[ "$(api_status GET /rooms "$existing_key" "" "$relay_url")" == "200" ]]; then
    claude_name="$(state_get agent)"
    claude_key="$existing_key"
    ok "reusing existing claude key (no re-mint -- avoids revoking active daemon)"
  else
    say "minting arav-claude on PRODUCTION"
    local row2; row2="$(remote_mint_child "$parent_key" "$relay_url" "claude" || true)"
    [[ -z "$row2" ]] && { fail "register-agent failed -- prod 5xx?"; exit 1; }
    IFS=$'\t' read -r claude_name claude_key <<<"$row2"
    [[ -z "$claude_key" ]] && { fail "mint returned empty key"; exit 1; }
    ok "minted: @$claude_name"
  fi

  say "ensuring room '$ROOM_NAME' on production"
  local room_id; room_id="$(ensure_room "$parent_jwt" "$relay_url")"
  if [[ -z "$room_id" || "$room_id" == "null" ]]; then
    fail "could not create/find room on prod -- use --local"; exit 1
  fi
  ok "room $ROOM_NAME id=$room_id"
  join_member "$parent_jwt" "$relay_url" "$room_id" "$PARENT_NAME"
  join_member "$parent_jwt" "$relay_url" "$room_id" "$claude_name"
  ok "joined: $PARENT_NAME, $claude_name"

  local pid="$(spawn_claude_daemon "$claude_key" "$relay_url" "$claude_name")"
  ok "$claude_name pid=$pid log=/tmp/kliment-claude.log"

  local pids; pids="$(jq -nc --arg n "$claude_name" --argjson p "$pid" '{($n):$p}')"
  write_pids "$pids"

  local state; state="$(jq -nc \
    --arg mode "remote" --arg url "$relay_url" --arg rid "$room_id" \
    --arg human "$PARENT_NAME" --arg agent "$claude_name" \
    --arg auth "$parent_jwt" --arg auth_claude "$claude_key" \
    --arg parent_key "$parent_key" \
    '{mode:$mode,relay_url:$url,room_id:$rid,human:$human,
      agent:$agent,auth:$auth,auth_claude:$auth_claude,parent_key:$parent_key}')"
  write_state "$state"

  finish_setup "$parent_jwt" "$relay_url" "$room_id" "$claude_name" "remote"
}

finish_setup() {
  local b="$1" u="$2" rid="$3" target="$4" mode="$5"

  say "waiting up to 25s for claude daemon to SSE-connect"
  local deadline=$(( $(date +%s) + 25 ))
  local connected=0
  while [[ $(date +%s) -lt $deadline ]]; do
    grep -q "sse connected" "/tmp/kliment-claude.log" 2>/dev/null \
      && { connected=1; break; }
    sleep 0.5
  done
  [[ $connected -eq 1 ]] && ok "claude daemon connected to SSE" \
                         || warn "claude daemon SSE handshake not seen in 25s -- check log"
  sleep 1

  say "verification: posting '@${target} say hi briefly' (up to 120s)"
  local verify="FAIL"
  if verify_round_trip "$b" "$u" "$rid" "$target"; then
    ok "real LLM reply received from $target"; verify="PASS"
  else
    warn "no reply from $target within 120s -- inspect /tmp/kliment-claude.log"
  fi

  printf '\n%s%sDEMO READY%s\n' "$CB" "$CG" "$C0"
  printf '  mode:        %s\n' "$mode"
  printf '  relay url:   %s\n' "$u"
  printf '  room:        %s (id=%s)\n' "$ROOM_NAME" "$rid"
  printf '  human:       @%s\n' "$PARENT_NAME"
  printf '  agent:       @%s\n' "$target"
  printf '  log:         /tmp/kliment-claude.log\n'
  printf '  events:      %s\n' "$EVENTS_LOG"
  printf '  verify:      %s\n\n' "$verify"
  printf '  %sNEXT - on Aarya MacBook (paste exactly):%s\n' "$CB" "$C0"
  if [[ "$mode" == "remote" ]]; then
    printf '    %sbash %s/scripts/stall_setup_aarya.sh%s\n' "$CC" "$REPO_ROOT" "$C0"
    printf '    (Aarya keeps her existing prod profile -- room name=%s)\n' "$ROOM_NAME"
  else
    printf '    %sQUORUS_RELAY_URL=%s quorus chat %s%s\n' "$CC" "$u" "$ROOM_NAME" "$C0"
    printf '    (LOCAL mode; Aarya cannot join from another laptop -- use --remote on demo night)\n'
  fi
  printf '\n  %sTUI for Arav:%s  ' "$CB" "$C0"
  if [[ "$mode" == "remote" ]]; then
    printf '%squorus chat %s%s\n\n' "$CC" "$ROOM_NAME" "$C0"
  else
    printf '%sQUORUS_RELAY_URL=%s quorus chat %s%s\n\n' "$CC" "$u" "$ROOM_NAME" "$C0"
  fi
}

# --- post-tasks ------------------------------------------------------------
cmd_post_tasks() {
  ensure_setup_loaded
  local b="${AUTH_BEARER}" u="${RELAY_URL}" rid="${ROOM_ID}"
  local content
  content=$'Splitable task list (claim what you can):\n1. design /v1/health/dashboard schema\n2. write integration test for outbox replay\n3. update CONTEXT.md with the AGENT-NATIVE OS framing\n4. add Prometheus metric for fanout_published\n5. draft the cross-laptop demo runcard\n6. open PR with all of the above'
  local body resp mid
  body="$(jq -nc --arg from "$PARENT_NAME" --arg c "$content" \
    '{from_name:$from,content:$c,message_type:"chat"}')"
  resp="$(api_call POST "/rooms/$rid/messages" "$b" "$body" "$u")"
  mid="$(echo "$resp" | jq -r '.id // empty' 2>/dev/null)"
  [[ -z "$mid" ]] && { fail "post-tasks failed: $resp"; exit 1; }
  api_call POST /v1/triage "$b" \
    "$(jq -nc --arg rid "$rid" --arg mid "$mid" --arg from "$PARENT_NAME" --arg c "$content" \
       '{room_id:$rid,message_id:$mid,from_name:$from,content:$c,message_type:"chat"}')" \
    "$u" >/dev/null 2>&1 || true
  log_event "post-tasks message_id=$mid"
  ok "posted 6-task list  message_id=$mid"
  printf '\n%sBeat 1 in motion%s -- watch agents claim with /claim verbs.\n' "$CB" "$C0"
}

# --- kill-aarya ------------------------------------------------------------
# On Arav Mac we may not have the aarya daemon. The script kills any
# /tmp/kliment-aarya-codex.pid (managed by Aarya's setup) OR falls back to
# `pkill` on aarya-* daemons (when running both Macs is impractical, the
# audit panel still narrates the disconnect against any subscriber that
# stops draining its SSE stream).
cmd_kill_aarya() {
  ensure_setup_loaded
  local pid_file="/tmp/kliment-aarya-codex.pid"
  local killed=0
  if [[ -f "$pid_file" ]]; then
    local p; p="$(cat "$pid_file" 2>/dev/null || echo "")"
    if [[ -n "$p" ]] && is_alive "$p"; then
      kill -TERM "$p" 2>/dev/null && { killed=1; ok "SIGTERM sent to aarya-codex pid=$p"; }
    fi
  fi
  if [[ $killed -eq 0 ]]; then
    pkill -TERM -f 'REFLEXD_PARTICIPANT=aarya-codex' 2>/dev/null && killed=1
    [[ $killed -eq 1 ]] && ok "SIGTERM sent to aarya-codex (matched via env)" \
                        || warn "no aarya-codex daemon found -- audit panel will still show queue depth"
  fi
  log_event "kill-aarya killed=$killed"
}

# --- resume-aarya ----------------------------------------------------------
cmd_resume_aarya() {
  ensure_setup_loaded
  local resume_cmd="bash $REPO_ROOT/scripts/stall_demo_local.sh start --remote"
  log_event "resume-aarya invoked"
  warn "resume must run on Aarya's Mac:  $resume_cmd"
  warn "watching /tmp/kliment-claude.log for 30s for outbox replay markers..."
  local deadline=$(( $(date +%s) + 30 )) seen=0
  while [[ $(date +%s) -lt $deadline ]]; do
    if grep -qE "(replay|backlog|reconnect|resync)" /tmp/kliment-claude.log 2>/dev/null; then
      seen=1; break
    fi
    sleep 1
  done
  [[ $seen -eq 1 ]] && ok "replay marker detected in claude log" \
                    || warn "no replay marker in 30s (Aarya may not be up yet)"
}

# --- propose-destructive ---------------------------------------------------
cmd_propose_destructive() {
  ensure_setup_loaded
  local b="${AUTH_BEARER}" u="${RELAY_URL}" rid="${ROOM_ID}"
  local agent="${AGENT_NAME}"
  local content
  content=$'@'"${agent}"$' PROPOSAL: drop the user.api_key column to simplify the auth model -- vote yes/no.\n\nThis is destructive and irreversible. Vote should be /vote no per the SOCIAL_PROTOCOL_v1 safety rule.'
  local body resp mid
  body="$(jq -nc --arg from "$PARENT_NAME" --arg c "$content" \
    '{from_name:$from,content:$c,message_type:"chat"}')"
  resp="$(api_call POST "/rooms/$rid/messages" "$b" "$body" "$u")"
  mid="$(echo "$resp" | jq -r '.id // empty' 2>/dev/null)"
  [[ -z "$mid" ]] && { fail "proposal post failed"; exit 1; }
  ok "destructive proposal posted (message_id=$mid)"

  api_call POST /v1/triage "$b" \
    "$(jq -nc --arg rid "$rid" --arg mid "$mid" --arg from "$PARENT_NAME" --arg c "$content" \
       '{room_id:$rid,message_id:$mid,from_name:$from,content:$c,message_type:"chat"}')" \
    "$u" >/dev/null 2>&1 || true

  # Simulate the /disagree blocking response as a chat message (the social
  # verb endpoint requires a JWT we don't always have here; the audit
  # panel still shows the human-readable narrative).
  sleep 2
  local disagree_body disagree_resp
  disagree_body="$(jq -nc --arg from "$agent" --arg ref "$mid" \
    --arg c "/disagree blocking ref=$mid -- destructive irreversible action requires consensus, not consent. Recommending CONSENSUS_REJECTED." \
    '{from_name:$from,content:$c,message_type:"chat",reply_to:$ref}')"
  disagree_resp="$(api_call POST "/rooms/$rid/messages" "$b" "$disagree_body" "$u")"
  ok "simulated /disagree blocking from $agent"

  sleep 1
  local vote_body vote_resp
  vote_body="$(jq -nc --arg from "${PARENT_NAME}-codex" --arg ref "$mid" \
    --arg c "/vote no ref=$mid -- second vote: blocking proposal stands. Audit chain shows PROPOSED -> DISAGREED -> CONSENSUS_REJECTED." \
    '{from_name:$from,content:$c,message_type:"chat",reply_to:$ref}')"
  vote_resp="$(api_call POST "/rooms/$rid/messages" "$b" "$vote_body" "$u")"
  ok "simulated /vote no from codex"

  log_event "propose-destructive proposal_id=$mid CONSENSUS=REJECTED"
  printf '\n%sBeat 3 narrative posted%s -- audit shows PROPOSED -> DISAGREED -> CONSENSUS_REJECTED.\n' "$CB" "$C0"
}

# --- audit -----------------------------------------------------------------
# Pretty-print the audit ledger (Postgres-backed only). Falls back to a
# room-history view if the relay is in-memory mode (e.g. local --local).
cmd_audit() {
  ensure_setup_loaded
  local b="${AUTH_BEARER}" u="${RELAY_URL}" rid="${ROOM_ID}"
  local code body
  code="$(api_status GET "/v1/audit/recent?limit=50" "$b" "" "$u")"
  if [[ "$code" == "200" ]]; then
    body="$(api_call GET "/v1/audit/recent?limit=50" "$b" "" "$u")"
    echo "$body" | jq -r '.events[] |
      "[\(.created_at)] \(.event_type)  actor=\(.actor // "-")  target=\(.target // "-")  msg=\(.message_id)"' \
      2>/dev/null | head -25
    say "failures (last 24h):"
    body="$(api_call GET "/v1/audit/failures?hours=24" "$b" "" "$u")"
    echo "$body" | jq -r '.events[] |
      "[\(.created_at)] \(.event_type)  target=\(.target // "-")  err=\(.error // "-")"' \
      2>/dev/null | head -10
  else
    warn "audit ledger not available (HTTP $code -- in-memory mode?). Showing room history instead."
    api_call GET "/rooms/$rid/history?limit=30" "$b" "" "$u" \
      | jq -r '
          if (type=="array") then . else (.messages // .history // []) end
          | .[] | "[\(.timestamp // "n/a")] \(.from_name)  \((.message_type//"chat")): \(.content[0:80])"' \
      2>/dev/null | head -30
  fi
  printf '\n%sevents log:%s %s\n' "$CB" "$C0" "$EVENTS_LOG"
  [[ -f "$EVENTS_LOG" ]] && tail -n 8 "$EVENTS_LOG" || warn "no demo events recorded yet"
}

# --- cleanup ---------------------------------------------------------------
cmd_cleanup() {
  local pids name pid
  if [[ -f "$PIDS_FILE" ]]; then
    pids="$(cat "$PIDS_FILE")"
    while IFS=$'\t' read -r name pid; do
      [[ -z "$name" ]] && continue
      if is_alive "$pid"; then
        kill "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6; do is_alive "$pid" || break; sleep 0.2; done
        kill -9 "$pid" 2>/dev/null || true
        ok "stopped $name (pid $pid)"
      fi
    done < <(echo "$pids" | jq -r 'to_entries[]? | "\(.key)\t\(.value)"')
  fi
  pkill -f "QUORUS_KLIMENT_DEMO=1" 2>/dev/null || true
  pkill -f "REFLEXD_PARTICIPANT=arav-claude" 2>/dev/null || true

  # Optionally delete the room on remote (skip on prod for safety unless --hard).
  local mode; mode="$(parse_mode_flag "$@")"
  if [[ "$mode" == "remote" ]] && [[ "${2-}" == "--hard" ]]; then
    local b="$(state_get auth)" u="$(state_get relay_url)" rid="$(state_get room_id)"
    if [[ -n "$b" && -n "$u" && -n "$rid" ]]; then
      api_call DELETE "/rooms/$rid" "$b" "" "$u" >/dev/null 2>&1 \
        && ok "deleted room $rid on prod" || warn "could not delete room"
    fi
  fi

  rm -f "$PIDS_FILE" "$KLIMENT_STATE_FILE" "$MODE_FILE" \
        "$RELAY_PORT_FILE" "$RELAY_SECRET_FILE" "$RELAY_MSGS_FILE" \
        "$EVENTS_LOG" /tmp/kliment-claude.log /tmp/.kliment-mint.body
  rm -rf /tmp/kliment-runtime-* 2>/dev/null || true
  ok "kliment demo state cleaned up"
}

# --- helpers used by subcommands -------------------------------------------
ensure_setup_loaded() {
  if [[ ! -f "$KLIMENT_STATE_FILE" ]]; then
    fail "no setup state -- run: bash scripts/kliment_demo.sh setup"; exit 1
  fi
  RELAY_URL="$(state_get relay_url)"
  ROOM_ID="$(state_get room_id)"
  AGENT_NAME="$(state_get agent)"
  AUTH_BEARER="$(state_get auth)"
  if [[ -z "$RELAY_URL" || -z "$ROOM_ID" || -z "$AUTH_BEARER" ]]; then
    fail "state file is corrupt; re-run setup"; exit 1
  fi
}

# --- Dispatch --------------------------------------------------------------
case "${1:-help}" in
  setup)               shift; cmd_setup "$@" ;;
  post-tasks)          shift; cmd_post_tasks "$@" ;;
  kill-aarya)          shift; cmd_kill_aarya "$@" ;;
  resume-aarya)        shift; cmd_resume_aarya "$@" ;;
  propose-destructive) shift; cmd_propose_destructive "$@" ;;
  audit)               shift; cmd_audit "$@" ;;
  cleanup)             shift; cmd_cleanup "$@" ;;
  help|*)
    cat <<EOF
Usage: $0 <subcommand> [--remote|--local]
Subcommands:
  setup                mint arav-claude on this Mac, create kliment-demo room
  post-tasks           Beat 1: post the 6-task split-able list
  kill-aarya           Beat 2a: SIGTERM aarya-codex daemon
  resume-aarya         Beat 2b: print resume command + watch for replay markers
  propose-destructive  Beat 3: simulate destructive proposal + reject via verbs
  audit                pretty-print recent audit events + failures
  cleanup              kill daemons + delete state files
EOF
    [[ "${1:-help}" == "help" ]] && exit 0 || exit 2 ;;
esac
