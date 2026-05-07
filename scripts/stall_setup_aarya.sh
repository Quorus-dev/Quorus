#!/usr/bin/env bash
# scripts/stall_setup_aarya.sh - one-shot Aarya-MacBook setup for the stall.
#
# Run on AARYA's laptop. Idempotent. Mints 4 host agents on production.
# Skips opencode cleanly if its CLI is missing.

set -u
set -o pipefail
set +m

REMOTE_RELAY_URL="${QUORUS_STALL_REMOTE_URL:-https://quorus-relay.fly.dev}"
ROOM_NAME="${QUORUS_STALL_ROOM:-stall-may7}"
PROFILE_DIR="$HOME/.quorus/profiles"
PROFILE_FILE="$PROFILE_DIR/default.json"
PIDS_FILE="/tmp/stall-pids.json"
MODE_FILE="/tmp/stall-mode.txt"
REMOTE_FILE="/tmp/stall-remote.json"
ACTIVE_FILE="/tmp/stall-aarya-active.txt"
ALL_AGENTS=("claude" "codex" "gemini" "opencode")

if [[ -t 1 && -z "${NO_COLOR-}" ]]; then
  C0=$'\033[0m'; CB=$'\033[1m'; CR=$'\033[31m'; CG=$'\033[32m'
  CY=$'\033[33m'; CC=$'\033[36m'
else
  C0=""; CB=""; CR=""; CG=""; CY=""; CC=""
fi
say()  { printf "%s>%s %s\n" "$CC" "$C0" "$1"; }
ok()   { printf "  %s+%s %s\n" "$CG" "$C0" "$1"; }
warn() { printf "  %s!%s %s\n" "$CY" "$C0" "$1" >&2; }
fail() { printf "%sx%s %s%s%s\n" "$CR" "$C0" "$CB" "$1" "$C0" >&2; }

# --- Prereqs ---------------------------------------------------------------
say "step 1: prereqs (python>=3.10, pipx, curl, jq)"
command -v python3 >/dev/null || { fail "python3 not on PATH"; exit 1; }
PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
case "$PYV" in 3.1[0-9]|3.[2-9][0-9]) ok "python $PYV" ;;
  *) fail "python $PYV too old; brew install python@3.12"; exit 1 ;;
esac
if ! command -v pipx >/dev/null; then
  fail "pipx not found"
  warn "install: brew install pipx     (or: python3 -m pip install --user pipx && pipx ensurepath)"
  exit 1
fi
ok "pipx present"
for t in curl jq; do
  command -v "$t" >/dev/null || { fail "missing $t (brew install $t)"; exit 1; }
done

# --- Install Quorus --------------------------------------------------------
say "step 2: pipx install quorus from GitHub"
if command -v quorus >/dev/null; then
  ok "quorus already installed: $(command -v quorus)"
else
  pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git" \
    || { fail "pipx install failed"; exit 1; }
  command -v quorus >/dev/null || { fail "binary not on PATH after install (run: pipx ensurepath)"; exit 1; }
  ok "installed: $(command -v quorus)"
fi

# --- Get api_key (Arav must mint it) --------------------------------------
say "step 3: configure api_key"
EXISTING=""
[[ -f "$PROFILE_FILE" ]] && EXISTING="$(jq -r '.api_key // empty' "$PROFILE_FILE" 2>/dev/null)"
if [[ -n "$EXISTING" && "$EXISTING" =~ ^mct_ ]]; then
  ok "profile already configured (idempotent)"
else
  printf '\n%sArav must mint your key on HIS Mac:%s\n' "$CB" "$C0"
  printf '  %squorus register-agent --suffix aarya --print-key%s\n' "$CC" "$C0"
  printf '  %s(if that command does not exist yet, ask Arav to run scripts/_mint_aarya.sh)%s\n\n' "$CY" "$C0"
  printf 'Paste the api_key (mct_...): '
  IFS= read -r KEY
  [[ "$KEY" =~ ^mct_ ]] || { fail "expected key starting with mct_"; exit 1; }
  mkdir -p "$PROFILE_DIR"
  jq -n --arg k "$KEY" --arg n aarya --arg u "$REMOTE_RELAY_URL" \
    '{api_key:$k,instance_name:$n,relay_url:$u,chat_identity:$n,poll_mode:"sse"}' \
    > "$PROFILE_FILE"
  chmod 600 "$PROFILE_FILE" 2>/dev/null || true
  ok "wrote $PROFILE_FILE"
fi

# --- whoami ----------------------------------------------------------------
say "step 4: quorus whoami"
quorus whoami 2>&1 | sed 's/^/    /' || { fail "whoami failed"; exit 1; }

# --- Mint child agent keys -------------------------------------------------
say "step 5: mint host-agent api_keys against PRODUCTION"
KEY="$(jq -r '.api_key' "$PROFILE_FILE")"
INST="$(jq -r '.instance_name' "$PROFILE_FILE")"
REMOTE_JSON="$(jq -nc --arg url "$REMOTE_RELAY_URL" --arg pk "$KEY" --arg human "$INST" \
  '{relay_url:$url,parent_key:$pk,human:$human,agents:{}}')"
ACTIVE=()
SKIPPED=()
for s in "${ALL_AGENTS[@]}"; do
  command -v "$s" >/dev/null || { SKIPPED+=("$s"); continue; }
  CODE="$(curl -sS --max-time 15 -o /tmp/.aarya-mint.body -w '%{http_code}' \
    -X POST -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg s "$s" '{suffix:$s}')" \
    "$REMOTE_RELAY_URL/v1/auth/register-agent" 2>/dev/null || echo "000")"
  if [[ "$CODE" == "200" ]]; then
    AN="$(jq -r '.agent_name // empty' /tmp/.aarya-mint.body)"
    AK="$(jq -r '.api_key // empty' /tmp/.aarya-mint.body)"
    if [[ -n "$AN" && -n "$AK" ]]; then
      REMOTE_JSON="$(echo "$REMOTE_JSON" | jq --arg s "$s" --arg n "$AN" --arg k "$AK" \
        '.agents[$s]={name:$n,api_key:$k}')"
      ACTIVE+=("$s"); ok "minted: @$AN"; continue
    fi
  fi
  warn "mint suffix=$s failed (HTTP $CODE -- prod degraded?)"
done
if [[ ${#ACTIVE[@]} -eq 0 ]]; then
  fail "production rejected all mints -- is /v1/auth/register-agent 5xx-ing?"
  exit 1
fi
[[ ${#SKIPPED[@]} -gt 0 ]] && warn "skipping (no binary): ${SKIPPED[*]}"
printf '%s\n' "$REMOTE_JSON" > "$REMOTE_FILE"
printf 'remote\n' > "$MODE_FILE"
printf '%s\n' "${ACTIVE[@]}" > "$ACTIVE_FILE"

# --- Print Arav-side commands ---------------------------------------------
say "step 6: Arav must add you to room '$ROOM_NAME' on HIS Mac"
printf '\n%sCopy these lines to Arav:%s\n\n' "$CB" "$C0"
printf 'JWT=$(curl -sS -X POST -H "Content-Type: application/json" \\\n'
printf "  -d '{\"api_key\":\"'\"\$ARAV_KEY\"'\"}' \\\n"
printf "  %s/v1/auth/token | jq -r .token)\n" "$REMOTE_RELAY_URL"
printf 'RID=$(curl -sS -H "Authorization: Bearer $JWT" %s/rooms \\\n' "$REMOTE_RELAY_URL"
printf "  | jq -r '.[]|select(.name==\"%s\")|.id' | head -1)\n" "$ROOM_NAME"
for n in aarya $(jq -r '.agents[].name' "$REMOTE_FILE"); do
  printf "curl -sS -X POST -H \"Authorization: Bearer \$JWT\" -H 'Content-Type: application/json' \\\n"
  printf "  -d '{\"participant\":\"%s\",\"role\":\"member\"}' %s/rooms/\$RID/join\n" \
    "$n" "$REMOTE_RELAY_URL"
done
printf '\n%sPress Enter once Arav confirms (or Ctrl-C to abort): %s' "$CB" "$C0"
IFS= read -r _ || true

# --- Spawn daemons ---------------------------------------------------------
say "step 7: spawn ${#ACTIVE[@]} reflexd daemon(s) against production"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python3"
REFLEXD_PY="$REPO_ROOT/scripts/reflexd.py"
if [[ ! -x "$VENV_PY" || ! -f "$REFLEXD_PY" ]]; then
  fail "this script needs the Quorus repo cloned with .venv set up"
  warn "git clone https://github.com/Quorus-dev/Quorus.git && cd Quorus && python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
fi
PIDS="$(jq -nc '{}')"
for s in "${ACTIVE[@]}"; do
  AN="$(jq -r --arg s "$s" '.agents[$s].name' "$REMOTE_FILE")"
  AK="$(jq -r --arg s "$s" '.agents[$s].api_key' "$REMOTE_FILE")"
  LOGF="/tmp/stall-${s}.log"
  RT="/tmp/stall-runtime-${s}"
  : > "$LOGF"; mkdir -p "$RT"
  env RELAY_URL="$REMOTE_RELAY_URL" REFLEXD_RELAY_URL="$REMOTE_RELAY_URL" \
      API_KEY="$AK" REFLEXD_API_KEY="$AK" \
      REFLEXD_PARTICIPANT="$AN" REFLEXD_LEGACY_BEARER=1 \
      QUORUS_STALL_DEMO=1 HOME="$HOME" QUORUS_RUNTIME_DIR="$RT" \
      "$VENV_PY" "$REFLEXD_PY" start --debug \
        --participant "$AN" --relay-url "$REMOTE_RELAY_URL" >"$LOGF" 2>&1 &
  PID=$!; disown "$PID" 2>/dev/null || true
  PIDS="$(echo "$PIDS" | jq --arg n "$AN" --argjson p "$PID" '.[$n]=$p')"
  ok "$AN pid=$PID log=$LOGF"
done
printf '%s\n' "$PIDS" > "$PIDS_FILE"

# --- Verify (post @aarya-claude hello, wait <=60s) -------------------------
say "step 8: round-trip verification"
JWT="$(curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' \
  -d "{\"api_key\":\"$KEY\"}" "$REMOTE_RELAY_URL/v1/auth/token" 2>/dev/null \
  | jq -r '.token // empty')"
if [[ -z "$JWT" ]]; then warn "JWT exchange failed -- skipping verify"; else
  RID="$(curl -sS --max-time 10 -H "Authorization: Bearer $JWT" \
    "$REMOTE_RELAY_URL/rooms" 2>/dev/null \
    | jq -r --arg n "$ROOM_NAME" '.[]?|select(.name==$n)|.id' | head -1)"
  if [[ -z "$RID" ]]; then
    warn "room '$ROOM_NAME' not visible (Arav add you yet?) -- skipping verify"
  else
    TARGET="aarya-claude"
    grep -q '^claude$' "$ACTIVE_FILE" || TARGET="aarya-$(head -1 "$ACTIVE_FILE")"
    BEFORE="$(curl -sS --max-time 10 -H "Authorization: Bearer $JWT" \
      "$REMOTE_RELAY_URL/rooms/$RID/history?limit=50" \
      | jq -r --arg a "$TARGET" 'if (type=="array") then . else (.messages//.history//.items//[]) end
        | map(select(.from_name==$a and (.message_type//"chat")!="wake_intent"))|length' 2>/dev/null || echo 0)"
    BODY="$(jq -nc --arg from aarya --arg c "@$TARGET hello" \
      '{from_name:$from,content:$c,message_type:"chat"}')"
    MID="$(curl -sS --max-time 15 -X POST -H "Authorization: Bearer $JWT" \
      -H 'Content-Type: application/json' -d "$BODY" \
      "$REMOTE_RELAY_URL/rooms/$RID/messages" 2>/dev/null \
      | jq -r '.id // .message_id // empty' 2>/dev/null)"
    if [[ -z "$MID" ]]; then warn "verify post failed"; else
      DEADLINE=$(( $(date +%s) + 60 ))
      VERIFY=FAIL
      while [[ $(date +%s) -lt $DEADLINE ]]; do
        AFTER="$(curl -sS --max-time 10 -H "Authorization: Bearer $JWT" \
          "$REMOTE_RELAY_URL/rooms/$RID/history?limit=50" \
          | jq -r --arg a "$TARGET" 'if (type=="array") then . else (.messages//.history//.items//[]) end
            | map(select(.from_name==$a and (.message_type//"chat")!="wake_intent"))|length' 2>/dev/null || echo 0)"
        [[ "${AFTER:-0}" -gt "${BEFORE:-0}" ]] && { ok "real LLM reply from @$TARGET"; VERIFY=PASS; break; }
        sleep 2
      done
      [[ "$VERIFY" == FAIL ]] && warn "no reply within 60s -- inspect /tmp/stall-${TARGET##*-}.log"
    fi
  fi
fi

# --- Banner ----------------------------------------------------------------
printf '\n%s%sAARYA STALL READY%s\n' "$CB" "$CG" "$C0"
printf '  mode:        remote\n'
printf '  relay url:   %s\n  room:        %s\n  human:       @aarya\n' \
  "$REMOTE_RELAY_URL" "$ROOM_NAME"
printf '  agents:     '
for s in "${ACTIVE[@]}"; do
  printf ' @%s' "$(jq -r --arg s "$s" '.agents[$s].name' "$REMOTE_FILE")"
done
printf '\n  status:      bash scripts/stall_demo_local.sh status\n'
printf '  stop:        bash scripts/stall_demo_local.sh stop\n\n'
printf '  %slaunch the TUI:%s  %squorus chat %s%s\n\n' "$CB" "$C0" "$CC" "$ROOM_NAME" "$C0"
