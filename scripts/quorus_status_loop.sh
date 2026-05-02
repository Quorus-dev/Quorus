#!/usr/bin/env bash
# Local 3-min status-loop poster. Runs in your shell while you're away
# so the room stays alive even if no agent has anything new to ship.
#
# What it does every 3 min:
#   1. Reads the most recent commit message from feat/may4-sprint
#   2. Tails the latest 5 lines from quorus inbox (new room messages)
#   3. Writes a JSON status to ~/.quorus/status/loop.json with timestamp
#   4. If Fly relay has /v1/triage (deployed), authenticates as
#      arav-codex-claude-1m and posts a heartbeat to #quorus-may4-sprint
#   5. Otherwise, just writes the heartbeat to a local file
#
# Run:    bash scripts/quorus_status_loop.sh &
# Stop:   pkill -f quorus_status_loop.sh
# Tail:   tail -f ~/.quorus/status/loop.log

set -euo pipefail

REPO=/Users/aravkekane/Desktop/Quorus
RELAY=https://quorus-relay.fly.dev
KEY=mct_9f282369a2d1_1a9a1d97f4ff29303c812e08a5db8b1e
PARTICIPANT=arav-codex-claude-1m
ROOM=quorus-may4-sprint
INTERVAL=180  # 3 minutes
MAX_RUNS=80   # ~4 hours then auto-stop
STATE_DIR="$HOME/.quorus/status"
LOG="$STATE_DIR/loop.log"
JSON="$STATE_DIR/loop.json"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"
}

probe_deployed() {
  # /v1/triage returns 404 if not deployed, 405 (Method Not Allowed) if it
  # exists and expects POST. Anything 4xx other than 404 means deployed.
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
    "$RELAY/v1/triage" 2>/dev/null || echo 000)
  case "$code" in
    405|400|401|422) return 0 ;;
    *) return 1 ;;
  esac
}

post_heartbeat() {
  local content="$1"
  # Exchange api_key for a JWT (codex's b4d4c1d landed this flow)
  local jwt
  jwt=$(curl -sS --max-time 10 -X POST "$RELAY/v1/auth/token" \
    -H 'Content-Type: application/json' \
    -d "{\"api_key\":\"$KEY\"}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null)
  if [[ -z "$jwt" ]]; then
    log "POST: token exchange failed"
    return 1
  fi
  local code
  code=$(curl -sS --max-time 10 -o /tmp/qsl.body -w '%{http_code}' \
    -X POST "$RELAY/rooms/$ROOM/messages" \
    -H "Authorization: Bearer $jwt" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "
import json, sys
print(json.dumps({
  'from_name': '$PARTICIPANT',
  'content': sys.argv[1],
  'message_type': 'chat',
}))
" "$content")" 2>/dev/null || echo 000)
  log "POST status=$code body=$(head -c 200 /tmp/qsl.body 2>/dev/null)"
}

run=0
while (( run < MAX_RUNS )); do
  run=$((run + 1))

  cd "$REPO" 2>/dev/null || { log "ERR: repo missing"; sleep "$INTERVAL"; continue; }

  # Latest commit
  last_commit=$(git log -1 --pretty=format:'%h %s' 2>/dev/null || echo 'unknown')

  # Latest inbox snippet (best-effort)
  inbox_snip=$(.venv/bin/quorus inbox 2>/dev/null | head -3 | tr '\n' '|' || echo '')

  # Test count (best-effort)
  test_count=$(grep -c "^ *def test_" tests/*.py 2>/dev/null | awk -F: '{s+=$2} END{print s}')

  # Write JSON state
  python3 -c "
import json, time, os
state = {
  'participant': '$PARTICIPANT',
  'run': $run,
  'max_runs': $MAX_RUNS,
  'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
  'last_commit': '''$last_commit''',
  'inbox_snippet': '''$inbox_snip''',
  'test_count': $test_count,
}
with open('$JSON', 'w') as f:
  json.dump(state, f, indent=2)
" 2>>"$LOG"

  log "TICK run=$run/$MAX_RUNS commit=$last_commit"

  # Try posting to chat if Fly is deployed
  if probe_deployed; then
    msg="💓 quorus_status_loop tick $run/$MAX_RUNS · last commit: ${last_commit:0:80} · tests: $test_count"
    post_heartbeat "$msg" || true
  else
    log "POST: skipped (Fly not deployed yet, /v1/triage 404)"
  fi

  sleep "$INTERVAL"
done

log "DONE: max runs reached, exiting cleanly"
