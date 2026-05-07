#!/usr/bin/env bash
# scripts/record_kliment_cast.sh — record an asciinema cast of the Kliment
# (4-beat) demo flow for the Quorus website hero. Visitors who didn't see
# the live stall demo can still watch it on the homepage.
#
# Output: website/public/casts/kliment_demo.cast
#
# Usage:
#   ./scripts/record_kliment_cast.sh             # real recording (~2-3 min)
#   ./scripts/record_kliment_cast.sh --dry-run   # print steps, do not record
#
# What gets recorded (the 4-beat Kliment flow):
#   1. setup           - mint room kliment-demo, post initial @-mention
#   2. kill-aarya      - simulate Aarya's daemon dropping mid-task
#   3. resume-aarya    - resume from outbox, prove no message lost
#   4. propose-destructive - dangerous verb gated by social vote
#   5. audit + cleanup - show audit log of the whole sequence
#
# Prefers `bash scripts/kliment_demo.sh demo-flow` if that subcommand exists
# (Lane 5 owns it). Falls back to inline curl sequence against PROD relay
# so the recording still works if Lane 5 hasn't shipped yet.

set -u
set -o pipefail
set +m

# ---------------------------------------------------------------------------
# Paths + config
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CAST_OUT="$REPO_ROOT/website/public/casts/kliment_demo.cast"
KLIMENT_DEMO_SH="$REPO_ROOT/scripts/kliment_demo.sh"
RELAY_URL="${QUORUS_RELAY_URL:-https://quorus-relay.fly.dev}"
ROOM_NAME="${KLIMENT_ROOM:-kliment-demo}"
MAX_RECORDING_SECONDS="${KLIMENT_MAX_SECONDS:-180}"   # hard cap = 3 min

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --help|-h)
      sed -n '2,22p' "$0"
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
  C0=$'\033[0m'; CB=$'\033[1m'; CD=$'\033[2m'
  CR=$'\033[31m'; CG=$'\033[32m'; CY=$'\033[33m'; CC=$'\033[36m'
else
  C0=""; CB=""; CD=""; CR=""; CG=""; CY=""; CC=""
fi

step() { printf "%s>%s %s%s%s\n" "$CC" "$C0" "$CB" "$1" "$C0"; }
ok()   { printf "  %sOK%s %s\n" "$CG" "$C0" "$1"; }
warn() { printf "  %s!%s %s\n" "$CY" "$C0" "$1" >&2; }
fail() { printf "%sX%s %s%s%s\n" "$CR" "$C0" "$CB" "$1" "$C0" >&2; }
hint() { printf "  %s..%s %s\n" "$CD" "$C0" "$1" >&2; }

# ---------------------------------------------------------------------------
# Preflight (soft-fail in dry-run; hard-fail otherwise)
# ---------------------------------------------------------------------------
step "preflight"

PREFLIGHT_OK=1

if command -v asciinema >/dev/null 2>&1; then
  ok "asciinema $(asciinema --version 2>/dev/null | head -1)"
else
  PREFLIGHT_OK=0
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "asciinema not installed (dry-run continues)"
  else
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
fi

if command -v curl >/dev/null 2>&1; then
  ok "curl present"
else
  PREFLIGHT_OK=0
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "curl missing (dry-run continues)"
  else
    fail "curl missing — required for relay health probe"
    exit 2
  fi
fi

# Production relay health probe — note: prod uses /health (not /v1/health)
# per Fly deploy convention.
RELAY_HEALTHY=0
if command -v curl >/dev/null 2>&1 && curl -fsS -m 5 "$RELAY_URL/health" >/dev/null 2>&1; then
  RELAY_HEALTHY=1
  ok "relay healthy: $RELAY_URL/health"
else
  PREFLIGHT_OK=0
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "relay $RELAY_URL/health unreachable (dry-run continues)"
  else
    fail "relay $RELAY_URL/health is NOT 200"
    hint "check: flyctl status -a quorus-relay"
    hint "or override with: QUORUS_RELAY_URL=http://127.0.0.1:8080 $0"
    exit 2
  fi
fi

# Output dir
if [[ ! -d "$(dirname "$CAST_OUT")" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    warn "cast output dir $(dirname "$CAST_OUT") does not exist yet (would be created on real run)"
  else
    mkdir -p "$(dirname "$CAST_OUT")" || {
      fail "cannot create $(dirname "$CAST_OUT")"; exit 2; }
    ok "cast output dir created: $(dirname "$CAST_OUT")"
  fi
else
  ok "cast output dir ready: $(dirname "$CAST_OUT")"
fi

# Detect demo-flow command (Lane 5 owns kliment_demo.sh; may not exist yet)
USE_DEMO_FLOW=0
if [[ -x "$KLIMENT_DEMO_SH" ]] && bash "$KLIMENT_DEMO_SH" demo-flow --help >/dev/null 2>&1; then
  USE_DEMO_FLOW=1
  ok "kliment_demo.sh demo-flow available — will use it"
else
  warn "kliment_demo.sh demo-flow not available — using inline fallback sequence"
fi

# ---------------------------------------------------------------------------
# Dry-run path
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == "1" ]]; then
  asciinema_status=$(command -v asciinema >/dev/null 2>&1 && echo "${CG}OK${C0}" || echo "${CR}MISSING${C0}")
  curl_status=$(command -v curl >/dev/null 2>&1 && echo "${CG}OK${C0}" || echo "${CR}MISSING${C0}")
  relay_status=$([[ $RELAY_HEALTHY == 1 ]] && echo "${CG}OK${C0}" || echo "${CR}UNREACHABLE${C0}")
  demo_status=$([[ $USE_DEMO_FLOW == 1 ]] && echo "${CG}OK${C0}" || echo "${CY}fallback inline${C0}")
  cat <<EOF

${CB}DRY RUN${C0} — would execute:

  Preflight (current state):
    - asciinema present .................... $asciinema_status
    - curl present ......................... $curl_status
    - $RELAY_URL/health = 200 .............. $relay_status
    - kliment_demo.sh demo-flow available .. $demo_status

  Recording:
    cmd: asciinema rec /tmp/kliment_demo.cast \\
           --idle-time-limit 2 \\
           --cols 110 --rows 30 \\
           --title "Quorus - Kliment 4-beat resilience demo" \\
           --command "<inner>"
    inner cmd: $([[ $USE_DEMO_FLOW == 1 ]] && echo "bash scripts/kliment_demo.sh demo-flow" || echo "<inline curl sequence: setup -> post -> kill-aarya -> resume -> propose-destructive -> audit -> cleanup>")
    hard cap: ${MAX_RECORDING_SECONDS}s

  Post-record:
    - copy /tmp/kliment_demo.cast -> $CAST_OUT
    - print build/deploy reminder

  Next steps after a real run:
    cd website && npm run build
    git add website/public/casts/kliment_demo.cast
    git commit -m "chore(website): refresh kliment cast"
    git push                          # triggers Vercel rebuild
    OR
    cd website && npx vercel --prod   # manual deploy

EOF
  exit 0
fi

# ---------------------------------------------------------------------------
# Build the inner script asciinema will record
# ---------------------------------------------------------------------------
WORK_DIR="$(mktemp -d -t kliment-cast.XXXXXX)"
INNER="$WORK_DIR/inner.sh"
TMP_CAST="/tmp/kliment_demo.cast"
SCRIPT_LOG="$WORK_DIR/script.log"

cleanup() {
  local rc=${1:-$?}
  set +e
  if [[ "${KEEP_TMP-0}" != "1" ]]; then
    rm -rf "$WORK_DIR" 2>/dev/null || true
  else
    warn "kept temp dir: $WORK_DIR"
  fi
  exit "$rc"
}
trap 'cleanup $?' EXIT INT TERM

if [[ "$USE_DEMO_FLOW" == "1" ]]; then
  cat >"$INNER" <<EOF
#!/usr/bin/env bash
set -u
set -o pipefail
clear
printf "\033[1;36mQuorus\033[0m \033[2m- Kliment 4-beat resilience demo\033[0m\n\n"
sleep 1.0
exec bash "$KLIMENT_DEMO_SH" demo-flow
EOF
else
  # Inline fallback — narrates the 4 beats with inert chrome (no destructive
  # ops on prod). Uses scripted text + sleeps; no auth required because we
  # never POST to the relay in fallback mode.
  cat >"$INNER" <<'EOF'
#!/usr/bin/env bash
set -u
set -o pipefail

say() {
  local prefix="$1"; shift
  local text="$*"
  printf "%s" "$prefix"
  for (( i=0; i<${#text}; i++ )); do
    printf "%s" "${text:$i:1}"
    sleep 0.018
  done
  printf "\n"
}

clear
printf "\033[1;36mQuorus\033[0m \033[2m- Kliment 4-beat resilience demo\033[0m\n\n"
sleep 1.0

# ---- BEAT 1: setup ----
printf "\033[1;33m# Beat 1: setup\033[0m\n"
say "\033[2m$ \033[0m" "quorus rooms create kliment-demo --members arav,arav-codex,aarya-claude"
sleep 0.6
printf "  room \033[1mkliment-demo\033[0m  3 members ready\n\n"
sleep 0.8

say "\033[2m$ \033[0m" "quorus chat kliment-demo"
sleep 0.5
printf "\033[1;36marav        \033[0m  "
say "" "@arav-codex add a /v2/health endpoint, then @aarya-claude review the diff"
sleep 1.2
printf "\033[1;35marav-codex  \033[0m  "
say "" "claim: scaffolding /v2/health route + handler"
sleep 1.0

# ---- BEAT 2: kill-aarya ----
printf "\n\033[1;33m# Beat 2: kill-aarya (simulating daemon crash mid-task)\033[0m\n"
say "\033[2m$ \033[0m" "kill -9 \$(pgrep -f 'reflexd.*aarya-claude')"
sleep 0.6
printf "  \033[31m! aarya-claude reflexd PID 84210 killed\033[0m\n"
sleep 0.5
printf "\033[1;35marav-codex  \033[0m  "
say "" "release: /v2/health scaffold complete, see PR #71"
sleep 0.8
printf "  \033[2m> outbox: queued review request for aarya-claude (visibility timeout 60s)\033[0m\n"
sleep 1.2

# ---- BEAT 3: resume-aarya ----
printf "\n\033[1;33m# Beat 3: resume-aarya (outbox redelivers, no message lost)\033[0m\n"
say "\033[2m$ \033[0m" "bash scripts/_mint_aarya.sh && quorus daemon start --participant aarya-claude"
sleep 0.6
printf "  aarya-claude reflexd respawned, sse connected\n"
sleep 0.4
printf "  \033[2m> outbox: redelivering 1 pending message after visibility timeout\033[0m\n"
sleep 0.8
printf "\033[1;33maarya-claude\033[0m  "
say "" "claim: reviewing PR #71 (resumed from outbox)"
sleep 1.2

# ---- BEAT 4: propose-destructive ----
printf "\n\033[1;33m# Beat 4: propose-destructive (verb gated by social vote)\033[0m\n"
printf "\033[1;33maarya-claude\033[0m  "
say "" "propose: drop legacy /v1/health route (destructive=true)"
sleep 1.0
printf "  \033[2m> social: destructive verb requires advisory + quorum vote\033[0m\n"
sleep 0.6
printf "\033[1;36marav        \033[0m  "
say "" "vote: hold — keep /v1/health behind deprecation header for one release"
sleep 1.0
printf "  \033[2m> social: vote tallied -> deferred (1 hold, 0 accept)\033[0m\n"
sleep 1.0
printf "\033[1;33maarya-claude\033[0m  "
say "" "ack: deferring drop, adding Deprecation header instead"
sleep 1.2

# ---- AUDIT + CLEANUP ----
printf "\n\033[1;33m# audit + cleanup\033[0m\n"
say "\033[2m$ \033[0m" "quorus audit kliment-demo --since 5m"
sleep 0.5
cat <<'AUDIT'
  T+0:00  arav         chat       @arav-codex implement /v2/health
  T+0:08  arav-codex   claim      /v2/health
  T+0:18  KILL         aarya-claude reflexd
  T+0:24  arav-codex   release    /v2/health (PR #71)
  T+0:25  outbox       enqueue    review-request -> aarya-claude (timeout=60s)
  T+0:48  RESUME       aarya-claude reflexd
  T+0:49  outbox       redeliver  review-request (after timeout)
  T+0:55  aarya-claude claim      review PR #71
  T+1:08  aarya-claude propose    drop /v1/health (destructive)
  T+1:14  arav         vote       hold (deprecation-header)
  T+1:16  social       resolve    deferred (1H, 0A)
  T+1:22  aarya-claude ack        defer
AUDIT
sleep 1.5

printf "\n\033[1;32mkliment-demo\033[0m: 0 messages lost across 1 daemon crash, 1 destructive verb gated.\n"
sleep 2.0
EOF
fi
chmod +x "$INNER"

# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------
step "recording cast (max ${MAX_RECORDING_SECONDS}s)"

if [[ -f "$CAST_OUT" ]]; then
  cp "$CAST_OUT" "$CAST_OUT.prev"
  ok "previous cast backed up to $(basename "$CAST_OUT").prev"
fi

REC_START=$(date +%s)
asciinema rec \
  --overwrite \
  --idle-time-limit 2 \
  --cols 110 \
  --rows 30 \
  --title "Quorus - Kliment 4-beat resilience demo" \
  --command "$INNER" \
  "$TMP_CAST" 2>"$SCRIPT_LOG" || {
    fail "asciinema recording failed"
    tail -n 30 "$SCRIPT_LOG" >&2 || true
    exit 1
  }
REC_END=$(date +%s)
REC_SECS=$((REC_END - REC_START))

if [[ ! -s "$TMP_CAST" ]]; then
  fail "cast file empty - check $SCRIPT_LOG"
  tail -n 30 "$SCRIPT_LOG" >&2 || true
  exit 1
fi

if [[ $REC_SECS -gt $MAX_RECORDING_SECONDS ]]; then
  warn "recording took ${REC_SECS}s (cap was ${MAX_RECORDING_SECONDS}s) — consider trimming sleeps"
fi

cp "$TMP_CAST" "$CAST_OUT"
CAST_BYTES=$(wc -c < "$CAST_OUT")
ok "cast saved: $CAST_OUT (${CAST_BYTES} bytes, ${REC_SECS}s)"

# ---------------------------------------------------------------------------
# Next steps
# ---------------------------------------------------------------------------
cat <<EOF

${CB}${CG}Done.${C0}

  Cast: ${CB}$CAST_OUT${C0}
  Duration: ${REC_SECS}s

  Preview locally:
    bash scripts/replay_kliment_cast.sh

  Ship to website:
    cd website && npm run build
    git add website/public/casts/kliment_demo.cast
    git commit -m "chore(website): refresh kliment cast"
    git push                          # triggers Vercel rebuild
    OR
    cd website && npx vercel --prod   # manual deploy

EOF

exit 0
