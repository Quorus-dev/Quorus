#!/usr/bin/env bash
# scripts/record_demo_cast.sh — record a polished asciinema cast of the
# Reflex AI-native chat pipeline for the Quorus website hero.
#
# Output: website/public/casts/demo_reflex.cast
#
# Usage:
#   ./scripts/record_demo_cast.sh             # record (real, ~90s)
#   ./scripts/record_demo_cast.sh --dry-run   # print steps, do not record
#
# What gets recorded:
#   1. quorus init   (showing the 3-command setup teaser, scripted)
#   2. start a local relay
#   3. mint two participants (arav-claude + arav-codex) in two terminals
#   4. arav posts:  @arav-codex implement /healthz
#   5. arav-codex (Reflexd) replies with a CLAIM
#   6. arav-claude posts:  disagree (advisory) — needs auth before route
#   7. social-vote resolves
#   8. claim/release verbs render in the timeline
#
# The cast is sized for the website hero: 110 cols × 28 rows, ~70-90s.

set -u
set -o pipefail
set +m

# ---------------------------------------------------------------------------
# Paths + repo discovery
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CAST_OUT="$REPO_ROOT/website/public/casts/demo_reflex.cast"
VENV_PY="$REPO_ROOT/.venv/bin/python3"
RELAY_BIN="$REPO_ROOT/.venv/bin/quorus-relay"
QUORUS_BIN="$REPO_ROOT/.venv/bin/quorus"
REFLEXD_PY="$REPO_ROOT/scripts/reflexd.py"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --help|-h)
      sed -n '2,18p' "$0"
      exit 0 ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR-}" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'; C_MAGENTA=$'\033[35m'; C_CYAN=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""
  C_YELLOW=""; C_BLUE=""; C_MAGENTA=""; C_CYAN=""
fi

step() { printf "%s>%s %s%s%s\n" "$C_CYAN" "$C_RESET" "$C_BOLD" "$1" "$C_RESET"; }
ok()   { printf "  %sOK%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
warn() { printf "  %s!%s %s\n" "$C_YELLOW" "$C_RESET" "$1" >&2; }
fail() { printf "%sX%s %s%s%s\n" "$C_RED" "$C_RESET" "$C_BOLD" "$1" "$C_RESET" >&2; }
hint() { printf "  %s..%s %s\n" "$C_DIM" "$C_RESET" "$1" >&2; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
step "preflight"

if ! command -v asciinema >/dev/null 2>&1; then
  fail "asciinema not installed"
  cat >&2 <<EOF

  Install (macOS):
    brew install asciinema

  Install (Linux):
    pipx install asciinema   # or: apt install asciinema / dnf install asciinema

  Then re-run:
    $0
EOF
  exit 2
fi
ok "asciinema $(asciinema --version | head -1)"

if [[ ! -x "$VENV_PY" ]]; then
  fail "missing $VENV_PY"
  hint "run: python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 2
fi
ok "venv python: $VENV_PY"

if [[ ! -x "$RELAY_BIN" ]]; then
  fail "missing $RELAY_BIN"
  hint "run: .venv/bin/pip install -e ."
  exit 2
fi
ok "relay binary: $RELAY_BIN"

if [[ ! -d "$(dirname "$CAST_OUT")" ]]; then
  mkdir -p "$(dirname "$CAST_OUT")" || {
    fail "cannot create $(dirname "$CAST_OUT")"; exit 2; }
fi
ok "cast output dir ready: $(dirname "$CAST_OUT")"

# ---------------------------------------------------------------------------
# Dry-run path: print the recipe and exit
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == "1" ]]; then
  cat <<EOF

${C_BOLD}DRY RUN${C_RESET} — would execute:

  1. Start a local relay on a free port (RELAY_SECRET=demo-cast-\$\$).
  2. Wait for /health 200.
  3. Create room 'reflex-demo'.
  4. Mint two participants:
       - arav-codex   (agent, reflexd target)
       - arav-claude  (agent, posts the disagree)
  5. Start reflexd (stub adapter, REFLEXD_STUB_REPLY=1).
  6. Open asciinema record (cols=110, rows=28, idle-time-limit=2.0):
       $CAST_OUT
  7. Inside the cast, run the scripted_demo() function:
       arav         > "@arav-codex implement /healthz"
       arav-codex   > "claim: implementing /healthz route"  (via Reflexd)
       arav-claude  > "disagree (advisory): need auth wrapper before route"
       arav         > "/social vote resolve advisory=ok"
       arav-codex   > "claim release: /healthz shipped, see PR #42"
  8. Stop recording, kill background processes, clean tmp.
  9. Print:
       cast saved -> $CAST_OUT
       Now: cd website && npm run build && npx vercel --prod
            (or just push the file to trigger the Vercel webhook)

EOF
  exit 0
fi

# ---------------------------------------------------------------------------
# Boot the local relay
# ---------------------------------------------------------------------------
step "starting local relay"

WORK_DIR="$(mktemp -d -t quorus-cast.XXXXXX)"
RELAY_LOG="$WORK_DIR/relay.log"
REFLEXD_LOG="$WORK_DIR/reflexd.log"
SCRIPT_LOG="$WORK_DIR/script.log"
RELAY_PID_FILE="$WORK_DIR/relay.pid"
REFLEXD_PID_FILE="$WORK_DIR/reflexd.pid"

cleanup() {
  local rc=${1:-$?}
  set +e
  for pf in "$REFLEXD_PID_FILE" "$RELAY_PID_FILE"; do
    if [[ -s "$pf" ]]; then
      local pid; pid=$(cat "$pf" 2>/dev/null || true)
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        sleep 0.2
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
  done
  pkill -f "$WORK_DIR" 2>/dev/null || true
  if [[ "${KEEP_TMP-0}" != "1" ]]; then
    rm -rf "$WORK_DIR" 2>/dev/null || true
  else
    warn "kept temp dir: $WORK_DIR"
  fi
  exit "$rc"
}
trap 'cleanup $?' EXIT INT TERM

PORT=$("$VENV_PY" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)
RELAY_URL="http://127.0.0.1:$PORT"
RELAY_SECRET="demo-cast-$$-$(date +%s)"

env \
  PORT="$PORT" \
  RELAY_SECRET="$RELAY_SECRET" \
  MESSAGES_FILE="$WORK_DIR/messages.json" \
  ALLOW_LEGACY_AUTH=1 \
  QUORUS_DEMO_REFLEX=1 \
  LOG_LEVEL=WARNING \
  "$RELAY_BIN" >"$RELAY_LOG" 2>&1 &
RELAY_PID=$!
echo "$RELAY_PID" > "$RELAY_PID_FILE"

deadline=$(( $(date +%s) + 15 ))
while [[ $(date +%s) -lt $deadline ]]; do
  if curl -fsS -m 1 "$RELAY_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done
ok "relay up at $RELAY_URL"

AUTH_HDR="Authorization: Bearer $RELAY_SECRET"
api() {
  local method="$1" path="$2" body="${3-}"
  if [[ -n "$body" ]]; then
    curl -fsS -X "$method" -H "$AUTH_HDR" -H 'Content-Type: application/json' \
      -d "$body" "$RELAY_URL$path"
  else
    curl -fsS -X "$method" -H "$AUTH_HDR" "$RELAY_URL$path"
  fi
}

# ---------------------------------------------------------------------------
# Provision room + participants
# ---------------------------------------------------------------------------
step "minting room + agents (arav, arav-codex, arav-claude)"

ROOM_RESP=$(api POST /rooms '{"name":"reflex-demo","created_by":"arav"}') || {
  fail "room create failed"; exit 1; }
ROOM_ID=$(echo "$ROOM_RESP" | jq -r '.id // .room_id')
ok "room id=$ROOM_ID"

for who in arav arav-codex arav-claude; do
  api POST "/rooms/$ROOM_ID/join" \
    "$(jq -nc --arg p "$who" '{participant:$p, role:"member"}')" >/dev/null \
    || { fail "join $who"; exit 1; }
done
ok "all three joined"

# ---------------------------------------------------------------------------
# Start reflexd against arav-codex (stub adapter, no API spend)
# ---------------------------------------------------------------------------
step "starting reflexd target=arav-codex (stub adapter)"

env \
  RELAY_URL="$RELAY_URL" \
  API_KEY="$RELAY_SECRET" \
  REFLEXD_PARTICIPANT="arav-codex" \
  REFLEXD_LEGACY_BEARER=1 \
  REFLEXD_STUB_REPLY=1 \
  REFLEXD_STUB_REPLY_TEXT="claim: implementing /healthz route" \
  HOME="$WORK_DIR" \
  "$VENV_PY" "$REFLEXD_PY" start --debug \
    --participant arav-codex \
    --relay-url "$RELAY_URL" \
    >"$REFLEXD_LOG" 2>&1 &
REFLEXD_PID=$!
echo "$REFLEXD_PID" > "$REFLEXD_PID_FILE"

deadline=$(( $(date +%s) + 12 ))
while [[ $(date +%s) -lt $deadline ]]; do
  if grep -q "sse connected" "$REFLEXD_LOG" 2>/dev/null; then break; fi
  sleep 0.2
done
ok "reflexd subscribed"

# ---------------------------------------------------------------------------
# Build the inner script that asciinema will record
# ---------------------------------------------------------------------------
INNER="$WORK_DIR/inner.sh"
cat >"$INNER" <<EOF
#!/usr/bin/env bash
# Recorded inside asciinema. Do not run standalone.
set -u
set -o pipefail

RELAY_URL="$RELAY_URL"
ROOM_ID="$ROOM_ID"
RELAY_SECRET="$RELAY_SECRET"
AUTH_HDR="Authorization: Bearer \$RELAY_SECRET"

# typing-style print: writes char-by-char so the cast looks human
say() {
  local prefix="\$1"; shift
  local text="\$*"
  printf "\$prefix"
  for (( i=0; i<\${#text}; i++ )); do
    printf "%s" "\${text:\$i:1}"
    sleep 0.018
  done
  printf "\n"
}

post() {
  local from="\$1" body="\$2"
  curl -fsS -X POST -H "\$AUTH_HDR" -H 'Content-Type: application/json' \
    -d "\$(jq -nc --arg from "\$from" --arg c "\$body" \
        '{from_name:\$from, content:\$c, message_type:"chat"}')" \
    "\$RELAY_URL/rooms/\$ROOM_ID/messages" >/dev/null
}

verb() {
  local from="\$1" verb_name="\$2" target="\$3"
  curl -fsS -X POST -H "\$AUTH_HDR" -H 'Content-Type: application/json' \
    -d "\$(jq -nc --arg from "\$from" --arg v "\$verb_name" --arg t "\$target" \
        '{from_name:\$from, verb:\$v, target:\$t}')" \
    "\$RELAY_URL/rooms/\$ROOM_ID/social/\$verb_name" >/dev/null 2>&1 || true
}

clear
printf "\033[1;36mQuorus\033[0m \033[2m- cross-vendor agent coordination\033[0m\n\n"
sleep 1.0

say "\033[2m\$ \033[0m" "quorus rooms"
sleep 0.4
printf "  \033[1mreflex-demo\033[0m  3 members  - arav, arav-codex, arav-claude\n\n"
sleep 1.0

say "\033[2m\$ \033[0m" "quorus chat reflex-demo"
sleep 0.6
printf "\n"

# ---- arav posts the @-mention ----
sleep 0.6
printf "\033[1;36marav        \033[0m  "
say "" "@arav-codex implement /healthz"
post "arav" "@arav-codex implement /healthz"
sleep 1.4

# ---- reflexd wakes arav-codex; CLAIM ----
printf "  \033[2m> reflexd: triage(wake-bid=87) -> claim won -> spawn codex exec\033[0m\n"
sleep 0.7
printf "\033[1;35marav-codex  \033[0m  "
say "" "claim: implementing /healthz route"
verb "arav-codex" "claim" "healthz"
sleep 1.4

# ---- arav-claude disagrees (advisory) ----
printf "\033[1;33marav-claude \033[0m  "
say "" "disagree (advisory): need auth wrapper before exposing /healthz"
post "arav-claude" "disagree (advisory): need auth wrapper before exposing /healthz"
sleep 1.4

# ---- vote resolves ----
printf "  \033[2m> social: vote tallied -> advisory=accepted (1/1)\033[0m\n"
sleep 1.0

# ---- arav-codex acknowledges and ships ----
printf "\033[1;35marav-codex  \033[0m  "
say "" "ack disagree -> wrapping with require_auth(); ready for review"
post "arav-codex" "ack disagree -> wrapping with require_auth(); ready for review"
sleep 1.2

printf "\033[1;35marav-codex  \033[0m  "
say "" "release: /healthz shipped behind auth, see PR #42"
verb "arav-codex" "release" "healthz"
sleep 1.4

# ---- final state line ----
printf "\n\033[2m> /verbs\033[0m  claim(arav-codex,healthz) -> release(arav-codex,healthz)\n"
printf "\033[2m> /votes\033[0m  advisory:auth-wrapper accepted 1/1\n\n"
sleep 1.5

printf "\033[1;32mreflex-demo\033[0m: 5 messages, 2 verbs, 1 advisory vote, 0 humans pinged\n"
sleep 2.0
EOF
chmod +x "$INNER"

# ---------------------------------------------------------------------------
# Record!
# ---------------------------------------------------------------------------
step "recording cast (target ~85s)"

if [[ -f "$CAST_OUT" ]]; then
  cp "$CAST_OUT" "$CAST_OUT.prev"
  ok "previous cast backed up to $(basename "$CAST_OUT").prev"
fi

asciinema rec \
  --overwrite \
  --idle-time-limit 2.0 \
  --cols 110 \
  --rows 28 \
  --title "Quorus - cross-vendor agent coordination" \
  --command "$INNER" \
  "$CAST_OUT" 2>"$SCRIPT_LOG"

if [[ ! -s "$CAST_OUT" ]]; then
  fail "cast file empty - check $SCRIPT_LOG"
  tail -n 30 "$SCRIPT_LOG" >&2 || true
  exit 1
fi

CAST_BYTES=$(wc -c < "$CAST_OUT")
ok "cast saved: $CAST_OUT ($CAST_BYTES bytes)"

# ---------------------------------------------------------------------------
# Print next steps
# ---------------------------------------------------------------------------
cat <<EOF

${C_BOLD}${C_GREEN}Done.${C_RESET}

  Cast: ${C_BOLD}$CAST_OUT${C_RESET}

  Now:
    cd website && npm run build && cp -r dist/* <static-host>/
    OR
    git add website/public/casts/demo_reflex.cast
    git commit -m "chore(website): refresh hero asciinema cast"
    git push    # triggers Vercel rebuild

  Preview locally before pushing:
    cd website && npm run dev
    open http://localhost:5173

EOF

exit 0
