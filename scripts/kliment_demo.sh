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

ensure_room() { local b=$1 u=$2 r e
  r="$(api_call GET /rooms "$b" "" "$u")"
  e="$(echo "$r" | jq -r --arg n "$ROOM_NAME" '.[]? | select(.name==$n) | .id' 2>/dev/null | head -1)"
  [[ -n "$e" ]] && { printf %s "$e"; return 0; }
  api_call POST /rooms "$b" "$(jq -nc --arg n "$ROOM_NAME" --arg c "$PARENT_NAME" '{name:$n,created_by:$c}')" "$u" \
    | jq -r '.id // empty' 2>/dev/null
}
join_member() { api_call POST "/rooms/$3/join" "$1" "$(jq -nc --arg p "$4" '{participant:$p,role:"member"}')" "$2" >/dev/null 2>&1 || true; }

start_local_relay() { local p s pid
  p="$(pick_free_port)"; s="kliment-secret-$(date +%s)-$$"
  printf '%s\n' "$p" > "$RELAY_PORT_FILE"; printf '%s\n' "$s" > "$RELAY_SECRET_FILE"; : > "$RELAY_LOG"
  env PORT="$p" RELAY_SECRET="$s" MESSAGES_FILE="$RELAY_MSGS_FILE" ALLOW_LEGACY_AUTH=1 \
      QUORUS_KLIMENT_DEMO=1 LOG_LEVEL=INFO "$RELAY_BIN" >"$RELAY_LOG" 2>&1 &
  pid=$!; disown "$pid" 2>/dev/null || true; printf %s "$pid"
}
wait_for_relay_health() { local p=$1 pid=$2 dl=$(( $(date +%s) + 15 ))
  while [[ $(date +%s) -lt $dl ]]; do
    is_alive "$pid" || { fail "local relay died"; tail -n 30 "$RELAY_LOG" >&2; return 1; }
    curl -fsS -m 1 "http://127.0.0.1:$p/health" >/dev/null 2>&1 && return 0
    sleep 0.2
  done
  fail "local relay /health stuck"; return 1
}

remote_load_profile() {
  [[ -f "$PROFILE_FILE" ]] || { fail "no profile $PROFILE_FILE -- run 'quorus join <code>'"; return 1; }
  local k i u
  k="$(jq -r '.api_key // empty' "$PROFILE_FILE")"
  i="$(jq -r '.instance_name // empty' "$PROFILE_FILE")"
  u="$(jq -r --arg d "$REMOTE_RELAY_URL" '.relay_url // $d' "$PROFILE_FILE")"
  [[ -n "$k" ]] || { fail "profile has no api_key"; return 1; }
  printf '%s\t%s\t%s' "$k" "${i:-arav}" "${u:-$REMOTE_RELAY_URL}"
}
remote_health_check() { local u=$1 c
  c="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$u/health" 2>/dev/null || echo 000)"
  [[ "$c" == "200" ]] && return 0
  fail "prod /health=$c (Upstash quota?)"; warn "fallback: bash scripts/kliment_demo.sh setup --local"; return 1
}
remote_exchange_jwt() { curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg k "$1" '{api_key:$k}')" "$2/v1/auth/token" 2>/dev/null | jq -r '.token // empty'; }
# WARNING: register-agent revokes the prior key for this suffix
# (memory: quorus_shim_register_agent.md). Only mint when reuse fails.
remote_mint_child() { local pk=$1 u=$2 s=$3 c
  c="$(curl -sS --max-time 15 -o /tmp/.kliment-mint.body -w '%{http_code}' \
    -X POST -H "Authorization: Bearer $pk" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg s "$s" '{suffix:$s}')" "$u/v1/auth/register-agent" 2>/dev/null || echo 000)"
  [[ "$c" == "200" ]] || return 1
  jq -r '. as $d | "\($d.agent_name // "")\t\($d.api_key // "")"' /tmp/.kliment-mint.body 2>/dev/null
}

spawn_claude_daemon() { local b=$1 u=$2 name=$3 logf=/tmp/kliment-claude.log rt=/tmp/kliment-runtime-claude pid
  : > "$logf"; mkdir -p "$rt"
  env RELAY_URL="$u" REFLEXD_RELAY_URL="$u" API_KEY="$b" REFLEXD_API_KEY="$b" \
      REFLEXD_PARTICIPANT="$name" REFLEXD_LEGACY_BEARER=1 \
      QUORUS_KLIMENT_DEMO=1 HOME="$HOME" QUORUS_RUNTIME_DIR="$rt" \
      "$VENV_PY" "$REFLEXD_PY" start --debug --participant "$name" --relay-url "$u" >"$logf" 2>&1 &
  pid=$!; disown "$pid" 2>/dev/null || true; printf %s "$pid"
}

verify_round_trip() { local b=$1 u=$2 rid=$3 target=$4 prompt body resp mid hist before after dl
  local jq_filter='if (type=="array") then . else (.messages // .history // []) end
        | map(select(.from_name==$a and (.message_type//"chat")!="wake_intent")) | length'
  before="$(api_call GET "/rooms/$rid/history?limit=50" "$b" "" "$u" \
    | jq -r --arg a "$target" "$jq_filter" 2>/dev/null || echo 0)"
  before="${before:-0}"
  prompt="@${target} say hi briefly"
  body="$(jq -nc --arg from "$PARENT_NAME" --arg c "$prompt" '{from_name:$from,content:$c,message_type:"chat"}')"
  resp="$(api_call POST "/rooms/$rid/messages" "$b" "$body" "$u")"
  mid="$(echo "$resp" | jq -r '.id // empty' 2>/dev/null)"
  [[ -n "$mid" ]] || { fail "verify post failed: $resp"; return 1; }
  api_call POST /v1/triage "$b" "$(jq -nc --arg rid "$rid" --arg mid "$mid" --arg from "$PARENT_NAME" \
    --arg c "$prompt" '{room_id:$rid,message_id:$mid,from_name:$from,content:$c,message_type:"chat"}')" \
    "$u" >/dev/null 2>&1 || true
  dl=$(( $(date +%s) + 120 ))
  while [[ $(date +%s) -lt $dl ]]; do
    hist="$(api_call GET "/rooms/$rid/history?limit=50" "$b" "" "$u")"
    after="$(echo "$hist" | jq -r --arg a "$target" "$jq_filter" 2>/dev/null || echo 0)"
    [[ "${after:-0}" -gt "$before" ]] && return 0
    sleep 2
  done
  return 1
}

cmd_setup() { local mode; mode="$(parse_mode_flag "$@")"; log_event "setup mode=$mode"
  if [[ "$mode" == "remote" ]]; then setup_remote; else setup_local; fi
}
setup_local() {
  write_mode local
  [[ -x "$RELAY_BIN" ]] || { fail "missing $RELAY_BIN (relay not in .venv)"; exit 2; }
  say "starting local quorus-relay"
  local rpid p s u rid agent pid pids state
  rpid="$(start_local_relay)"; p="$(cat "$RELAY_PORT_FILE")"; s="$(cat "$RELAY_SECRET_FILE")"
  wait_for_relay_health "$p" "$rpid" || { kill "$rpid" 2>/dev/null; exit 1; }
  u="http://127.0.0.1:$p"; ok "local relay healthy on :$p"
  say "creating demo room '$ROOM_NAME'"
  rid="$(ensure_room "$s" "$u")"
  [[ -n "$rid" ]] || { fail "could not create room"; kill "$rpid" 2>/dev/null; exit 1; }
  ok "room $ROOM_NAME id=$rid"
  join_member "$s" "$u" "$rid" "$PARENT_NAME"; ok "joined: $PARENT_NAME"
  agent="${PARENT_NAME}-claude"
  join_member "$s" "$u" "$rid" "$agent"
  pid="$(spawn_claude_daemon "$s" "$u" "$agent")"
  ok "$agent pid=$pid log=/tmp/kliment-claude.log"
  pids="$(jq -nc --argjson r "$rpid" --arg n "$agent" --argjson p "$pid" '{relay:$r}+{($n):$p}')"
  write_pids "$pids"
  state="$(jq -nc --arg mode local --arg url "$u" --arg rid "$rid" --arg human "$PARENT_NAME" \
    --arg agent "$agent" --arg auth "$s" \
    '{mode:$mode,relay_url:$url,room_id:$rid,human:$human,agent:$agent,auth:$auth}')"
  write_state "$state"
  finish_setup "$s" "$u" "$rid" "$agent" local
}
setup_remote() {
  write_mode remote
  say "loading profile $PROFILE_FILE"
  local row pk pi u jwt name key existing rid pid pids state
  row="$(remote_load_profile)" || exit 1
  IFS=$'\t' read -r pk pi u <<<"$row"; PARENT_NAME="${pi:-$PARENT_NAME}"
  ok "parent: @$PARENT_NAME  relay: $u"
  remote_health_check "$u" || exit 1
  jwt="$(remote_exchange_jwt "$pk" "$u")"
  [[ -n "$jwt" ]] || { fail "/v1/auth/token rejected -- re-run quorus join"; rm -f "$MODE_FILE"; exit 1; }
  ok "JWT obtained (len=${#jwt})"
  # Reuse existing claude key if it still authenticates (re-mint revokes prior).
  existing="$(state_get auth_claude)"
  if [[ -n "$existing" && "$(api_status GET /rooms "$existing" "" "$u")" == "200" ]]; then
    name="$(state_get agent)"; key="$existing"
    ok "reusing existing claude key (no re-mint)"
  else
    say "minting arav-claude on PRODUCTION"
    local r2; r2="$(remote_mint_child "$pk" "$u" claude || true)"
    [[ -n "$r2" ]] || { fail "register-agent failed -- prod 5xx?"; exit 1; }
    IFS=$'\t' read -r name key <<<"$r2"
    [[ -n "$key" ]] || { fail "mint returned empty key"; exit 1; }
    ok "minted: @$name"
  fi
  say "ensuring room '$ROOM_NAME' on production"
  rid="$(ensure_room "$jwt" "$u")"
  [[ -n "$rid" && "$rid" != "null" ]] || { fail "could not create room on prod -- use --local"; exit 1; }
  ok "room $ROOM_NAME id=$rid"
  join_member "$jwt" "$u" "$rid" "$PARENT_NAME"; join_member "$jwt" "$u" "$rid" "$name"
  ok "joined: $PARENT_NAME, $name"
  pid="$(spawn_claude_daemon "$key" "$u" "$name")"
  ok "$name pid=$pid log=/tmp/kliment-claude.log"
  pids="$(jq -nc --arg n "$name" --argjson p "$pid" '{($n):$p}')"
  write_pids "$pids"
  state="$(jq -nc --arg mode remote --arg url "$u" --arg rid "$rid" --arg human "$PARENT_NAME" \
    --arg agent "$name" --arg auth "$jwt" --arg ack "$key" --arg pk "$pk" \
    '{mode:$mode,relay_url:$url,room_id:$rid,human:$human,agent:$agent,auth:$auth,auth_claude:$ack,parent_key:$pk}')"
  write_state "$state"
  finish_setup "$jwt" "$u" "$rid" "$name" remote
}

finish_setup() { local b=$1 u=$2 rid=$3 target=$4 mode=$5
  say "waiting up to 25s for claude daemon SSE handshake"
  local dl=$(( $(date +%s) + 25 )) connected=0
  while [[ $(date +%s) -lt $dl ]]; do
    grep -q "sse connected" /tmp/kliment-claude.log 2>/dev/null && { connected=1; break; }
    sleep 0.5
  done
  [[ $connected -eq 1 ]] && ok "claude daemon connected to SSE" || warn "SSE handshake not seen in 25s"
  sleep 1
  say "verification: posting '@${target} say hi briefly' (up to 120s)"
  local verify=FAIL
  verify_round_trip "$b" "$u" "$rid" "$target" \
    && { ok "real LLM reply received from $target"; verify=PASS; } \
    || warn "no reply from $target -- inspect /tmp/kliment-claude.log"
  printf '\n%s%sDEMO READY%s\n' "$CB" "$CG" "$C0"
  printf '  mode:%s relay:%s room:%s id=%s human:@%s agent:@%s\n  log:/tmp/kliment-claude.log events:%s verify:%s\n\n' \
    "$mode" "$u" "$ROOM_NAME" "$rid" "$PARENT_NAME" "$target" "$EVENTS_LOG" "$verify"
  printf '  %sNEXT - on Aarya MacBook:%s\n' "$CB" "$C0"
  if [[ "$mode" == "remote" ]]; then
    printf '    bash %s/scripts/stall_setup_aarya.sh\n' "$REPO_ROOT"
    printf '  TUI for Arav:  quorus chat %s\n\n' "$ROOM_NAME"
  else
    printf '    QUORUS_RELAY_URL=%s quorus chat %s   (LOCAL mode -- use --remote on demo night)\n' "$u" "$ROOM_NAME"
    printf '  TUI for Arav:  QUORUS_RELAY_URL=%s quorus chat %s\n\n' "$u" "$ROOM_NAME"
  fi
}

ensure_setup_loaded() {
  [[ -f "$KLIMENT_STATE_FILE" ]] || { fail "no setup state -- run: bash scripts/kliment_demo.sh setup"; exit 1; }
  RELAY_URL="$(state_get relay_url)"; ROOM_ID="$(state_get room_id)"
  AGENT_NAME="$(state_get agent)"; AUTH_BEARER="$(state_get auth)"
  [[ -n "$RELAY_URL" && -n "$ROOM_ID" && -n "$AUTH_BEARER" ]] || { fail "state corrupt"; exit 1; }
}

cmd_post_tasks() { ensure_setup_loaded
  local b="$AUTH_BEARER" u="$RELAY_URL" rid="$ROOM_ID" body resp mid c
  c=$'Splitable task list (claim what you can):\n1. design /v1/health/dashboard schema\n2. write integration test for outbox replay\n3. update CONTEXT.md with the AGENT-NATIVE OS framing\n4. add Prometheus metric for fanout_published\n5. draft the cross-laptop demo runcard\n6. open PR with all of the above'
  body="$(jq -nc --arg from "$PARENT_NAME" --arg c "$c" '{from_name:$from,content:$c,message_type:"chat"}')"
  resp="$(api_call POST "/rooms/$rid/messages" "$b" "$body" "$u")"
  mid="$(echo "$resp" | jq -r '.id // empty' 2>/dev/null)"
  [[ -n "$mid" ]] || { fail "post-tasks failed: $resp"; exit 1; }
  api_call POST /v1/triage "$b" "$(jq -nc --arg rid "$rid" --arg mid "$mid" --arg from "$PARENT_NAME" \
    --arg c "$c" '{room_id:$rid,message_id:$mid,from_name:$from,content:$c,message_type:"chat"}')" \
    "$u" >/dev/null 2>&1 || true
  log_event "post-tasks message_id=$mid"
  ok "posted 6-task list  message_id=$mid"
  printf '\n%sBeat 1 in motion%s -- watch agents claim with /claim verbs.\n' "$CB" "$C0"
}

cmd_kill_aarya() { ensure_setup_loaded
  local pid_file=/tmp/kliment-aarya-codex.pid killed=0 p
  if [[ -f "$pid_file" ]]; then
    p="$(cat "$pid_file" 2>/dev/null || echo "")"
    [[ -n "$p" ]] && is_alive "$p" && kill -TERM "$p" 2>/dev/null \
      && { killed=1; ok "SIGTERM sent to aarya-codex pid=$p"; }
  fi
  if [[ $killed -eq 0 ]]; then
    pkill -TERM -f 'REFLEXD_PARTICIPANT=aarya-codex' 2>/dev/null && killed=1
    [[ $killed -eq 1 ]] && ok "SIGTERM sent to aarya-codex (env match)" \
      || warn "no aarya-codex daemon found here -- audit panel will still show queue depth"
  fi
  log_event "kill-aarya killed=$killed"
}

cmd_resume_aarya() { ensure_setup_loaded
  log_event "resume-aarya invoked"
  warn "resume must run on Aarya's Mac:  bash $REPO_ROOT/scripts/stall_demo_local.sh start --remote"
  warn "watching /tmp/kliment-claude.log for 30s for replay markers..."
  local dl=$(( $(date +%s) + 30 )) seen=0
  while [[ $(date +%s) -lt $dl ]]; do
    grep -qE "(replay|backlog|reconnect|resync)" /tmp/kliment-claude.log 2>/dev/null \
      && { seen=1; break; }
    sleep 1
  done
  [[ $seen -eq 1 ]] && ok "replay marker detected in claude log" \
    || warn "no replay marker in 30s (Aarya may not be up yet)"
}

cmd_propose_destructive() { ensure_setup_loaded
  local b="$AUTH_BEARER" u="$RELAY_URL" rid="$ROOM_ID" agent="$AGENT_NAME"
  local c1 body resp mid d_body v_body
  c1=$'@'"${agent}"$' PROPOSAL: drop the user.api_key column to simplify the auth model -- vote yes/no.\n\nThis is destructive and irreversible. Vote should be /vote no per SOCIAL_PROTOCOL_v1.'
  body="$(jq -nc --arg from "$PARENT_NAME" --arg c "$c1" '{from_name:$from,content:$c,message_type:"chat"}')"
  resp="$(api_call POST "/rooms/$rid/messages" "$b" "$body" "$u")"
  mid="$(echo "$resp" | jq -r '.id // empty' 2>/dev/null)"
  [[ -n "$mid" ]] || { fail "proposal post failed"; exit 1; }
  ok "destructive proposal posted (message_id=$mid)"
  api_call POST /v1/triage "$b" "$(jq -nc --arg rid "$rid" --arg mid "$mid" --arg from "$PARENT_NAME" \
    --arg c "$c1" '{room_id:$rid,message_id:$mid,from_name:$from,content:$c,message_type:"chat"}')" \
    "$u" >/dev/null 2>&1 || true
  # Simulate /disagree + /vote as chat messages -- the social-verb endpoint
  # needs a JWT we may not have here, but the audit panel still shows the
  # human-readable narrative for the audience.
  sleep 2
  d_body="$(jq -nc --arg from "$agent" --arg ref "$mid" \
    --arg c "/disagree blocking ref=$mid -- destructive irreversible action requires consensus, not consent. Recommending CONSENSUS_REJECTED." \
    '{from_name:$from,content:$c,message_type:"chat",reply_to:$ref}')"
  api_call POST "/rooms/$rid/messages" "$b" "$d_body" "$u" >/dev/null
  ok "simulated /disagree blocking from $agent"
  sleep 1
  v_body="$(jq -nc --arg from "${PARENT_NAME}-codex" --arg ref "$mid" \
    --arg c "/vote no ref=$mid -- second vote: blocking proposal stands. PROPOSED -> DISAGREED -> CONSENSUS_REJECTED." \
    '{from_name:$from,content:$c,message_type:"chat",reply_to:$ref}')"
  api_call POST "/rooms/$rid/messages" "$b" "$v_body" "$u" >/dev/null
  ok "simulated /vote no from codex"
  log_event "propose-destructive proposal_id=$mid CONSENSUS=REJECTED"
  printf '\n%sBeat 3 narrative posted%s -- audit shows PROPOSED -> DISAGREED -> CONSENSUS_REJECTED.\n' "$CB" "$C0"
}

cmd_audit() { ensure_setup_loaded
  local b="$AUTH_BEARER" u="$RELAY_URL" rid="$ROOM_ID" code body
  code="$(api_status GET "/v1/audit/recent?limit=50" "$b" "" "$u")"
  if [[ "$code" == "200" ]]; then
    body="$(api_call GET "/v1/audit/recent?limit=50" "$b" "" "$u")"
    echo "$body" | jq -r '.events[] | "[\(.created_at)] \(.event_type)  actor=\(.actor // "-")  target=\(.target // "-")  msg=\(.message_id)"' 2>/dev/null | head -25
    say "failures (last 24h):"
    body="$(api_call GET "/v1/audit/failures?hours=24" "$b" "" "$u")"
    echo "$body" | jq -r '.events[] | "[\(.created_at)] \(.event_type)  target=\(.target // "-")  err=\(.error // "-")"' 2>/dev/null | head -10
  else
    warn "audit ledger HTTP $code (in-memory mode?). Showing room history instead."
    api_call GET "/rooms/$rid/history?limit=30" "$b" "" "$u" \
      | jq -r 'if (type=="array") then . else (.messages // .history // []) end
        | .[] | "[\(.timestamp // "n/a")] \(.from_name)  \((.message_type//"chat")): \(.content[0:80])"' 2>/dev/null | head -30
  fi
  printf '\n%sevents log:%s %s\n' "$CB" "$C0" "$EVENTS_LOG"
  [[ -f "$EVENTS_LOG" ]] && tail -n 8 "$EVENTS_LOG" || warn "no demo events recorded"
}

cmd_cleanup() { local pids name pid mode
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
  # Skip room delete on prod unless --hard is passed (safety).
  mode="$(parse_mode_flag "$@")"
  if [[ "$mode" == "remote" && "${2-}" == "--hard" ]]; then
    local b="$(state_get auth)" u="$(state_get relay_url)" rid="$(state_get room_id)"
    [[ -n "$b" && -n "$u" && -n "$rid" ]] \
      && { api_call DELETE "/rooms/$rid" "$b" "" "$u" >/dev/null 2>&1 \
           && ok "deleted room $rid on prod" || warn "could not delete room"; }
  fi
  rm -f "$PIDS_FILE" "$KLIMENT_STATE_FILE" "$MODE_FILE" \
        "$RELAY_PORT_FILE" "$RELAY_SECRET_FILE" "$RELAY_MSGS_FILE" \
        "$EVENTS_LOG" /tmp/kliment-claude.log /tmp/.kliment-mint.body
  rm -rf /tmp/kliment-runtime-* 2>/dev/null || true
  ok "kliment demo state cleaned up"
}

case "${1:-help}" in
  setup)               shift; cmd_setup "$@" ;;
  post-tasks)          shift; cmd_post_tasks "$@" ;;
  kill-aarya)          shift; cmd_kill_aarya "$@" ;;
  resume-aarya)        shift; cmd_resume_aarya "$@" ;;
  propose-destructive) shift; cmd_propose_destructive "$@" ;;
  audit)               shift; cmd_audit "$@" ;;
  cleanup)             shift; cmd_cleanup "$@" ;;
  help|*) cat <<EOF
Usage: $0 <subcommand> [--remote|--local]
Subcommands: setup post-tasks kill-aarya resume-aarya propose-destructive audit cleanup
See docs/KLIMENT_DEMO_RUNCARD.md for the beat-by-beat script.
EOF
    [[ "${1:-help}" == "help" ]] && exit 0 || exit 2 ;;
esac
