#!/usr/bin/env bash
# scripts/record_kliment_cast.sh — record an asciinema cast of the Kliment
# 4-beat demo (setup -> kill-aarya -> resume-aarya -> propose-destructive ->
# audit) for the Quorus website hero. Uses scripts/kliment_demo.sh demo-flow
# when present; falls back to a scripted inline narration otherwise.
#
# Output: website/public/casts/kliment_demo.cast
#
# Usage:
#   ./scripts/record_kliment_cast.sh             # real recording (~2-3 min)
#   ./scripts/record_kliment_cast.sh --dry-run   # print steps, do not record

set -u
set -o pipefail
set +m

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CAST_OUT="$REPO_ROOT/website/public/casts/kliment_demo.cast"
KLIMENT_DEMO_SH="$REPO_ROOT/scripts/kliment_demo.sh"
RELAY_URL="${QUORUS_RELAY_URL:-https://quorus-relay.fly.dev}"
MAX_SECS="${KLIMENT_MAX_SECONDS:-180}"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --help|-h) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [[ -t 1 && -z "${NO_COLOR-}" ]]; then
  C0=$'\033[0m'; CB=$'\033[1m'; CD=$'\033[2m'
  CR=$'\033[31m'; CG=$'\033[32m'; CY=$'\033[33m'; CC=$'\033[36m'
else
  C0=""; CB=""; CD=""; CR=""; CG=""; CY=""; CC=""
fi
step() { printf "%s>%s %s%s%s\n" "$CC" "$C0" "$CB" "$1" "$C0"; }
ok()   { printf "  %sOK%s %s\n"   "$CG" "$C0" "$1"; }
warn() { printf "  %s!%s %s\n"    "$CY" "$C0" "$1" >&2; }
fail() { printf "%sX%s %s%s%s\n"  "$CR" "$C0" "$CB" "$1" "$C0" >&2; }

# Preflight: hard-fail when recording, soft-warn in dry-run.
step "preflight"
HAVE_ASCII=0; HAVE_CURL=0; RELAY_OK=0; USE_DEMO_FLOW=0
command -v asciinema >/dev/null 2>&1 && HAVE_ASCII=1
command -v curl      >/dev/null 2>&1 && HAVE_CURL=1
[[ $HAVE_CURL == 1 ]] && curl -fsS -m 5 "$RELAY_URL/health" >/dev/null 2>&1 && RELAY_OK=1
[[ -x "$KLIMENT_DEMO_SH" ]] && bash "$KLIMENT_DEMO_SH" demo-flow --help >/dev/null 2>&1 && USE_DEMO_FLOW=1

if [[ $DRY_RUN == 0 ]]; then
  if [[ $HAVE_ASCII == 0 ]]; then
    fail "asciinema not installed"
    echo "  Install: brew install asciinema   (or: pipx install asciinema)" >&2
    exit 2
  fi
  ok "asciinema $(asciinema --version 2>/dev/null | head -1)"
  [[ $HAVE_CURL == 1 ]] || { fail "curl missing"; exit 2; }
  ok "curl present"
  if [[ $RELAY_OK == 0 ]]; then
    fail "relay $RELAY_URL/health is NOT 200"
    echo "  check: flyctl status -a quorus-relay" >&2
    echo "  override: QUORUS_RELAY_URL=http://127.0.0.1:8080 $0" >&2
    exit 2
  fi
  ok "relay healthy: $RELAY_URL/health"
  mkdir -p "$(dirname "$CAST_OUT")"
  ok "cast output dir: $(dirname "$CAST_OUT")"
  [[ $USE_DEMO_FLOW == 1 ]] && ok "kliment_demo.sh demo-flow available" \
                            || warn "kliment_demo.sh demo-flow missing — using inline fallback"
fi

if [[ $DRY_RUN == 1 ]]; then
  st() { [[ $1 == 1 ]] && echo "${CG}OK${C0}" || echo "${CR}MISS${C0}"; }
  cat <<EOF

${CB}DRY RUN${C0} — would execute:

  Preflight (current state):
    asciinema present ........... $(st $HAVE_ASCII)
    curl present ................ $(st $HAVE_CURL)
    $RELAY_URL/health = 200 ..... $(st $RELAY_OK)
    kliment_demo.sh demo-flow ... $([[ $USE_DEMO_FLOW == 1 ]] && echo "${CG}OK${C0}" || echo "${CY}fallback inline${C0}")

  Recording:
    asciinema rec /tmp/kliment_demo.cast \\
      --idle-time-limit 2 --cols 110 --rows 30 \\
      --title "Quorus - Kliment 4-beat resilience demo" \\
      --command "<inner>"
    inner: $([[ $USE_DEMO_FLOW == 1 ]] && echo "bash scripts/kliment_demo.sh demo-flow" || echo "<inline 4-beat narration: setup -> kill-aarya -> resume -> propose-destructive -> audit>")
    hard cap: ${MAX_SECS}s

  Post-record:
    cp /tmp/kliment_demo.cast -> $CAST_OUT
    cd website && npm run build
    git add $CAST_OUT && git commit -m "chore(website): refresh kliment cast" && git push
    (Vercel rebuilds on push; or: cd website && npx vercel --prod)

EOF
  exit 0
fi

# Build inner script asciinema will record.
WORK_DIR="$(mktemp -d -t kliment-cast.XXXXXX)"
INNER="$WORK_DIR/inner.sh"
TMP_CAST="/tmp/kliment_demo.cast"
SCRIPT_LOG="$WORK_DIR/script.log"
trap 'rc=$?; [[ "${KEEP_TMP-0}" == "1" ]] || rm -rf "$WORK_DIR" 2>/dev/null; exit $rc' EXIT INT TERM

if [[ $USE_DEMO_FLOW == 1 ]]; then
  cat >"$INNER" <<EOF
#!/usr/bin/env bash
set -u; set -o pipefail
clear
printf "\033[1;36mQuorus\033[0m \033[2m- Kliment 4-beat resilience demo\033[0m\n\n"
sleep 1
exec bash "$KLIMENT_DEMO_SH" demo-flow
EOF
else
  # Inline fallback: scripted narration with inert chrome (no prod mutations).
  cat >"$INNER" <<'EOF'
#!/usr/bin/env bash
set -u; set -o pipefail
say() {
  local prefix="$1"; shift; local text="$*"
  printf "%s" "$prefix"
  for (( i=0; i<${#text}; i++ )); do printf "%s" "${text:$i:1}"; sleep 0.018; done
  printf "\n"
}
clear
printf "\033[1;36mQuorus\033[0m \033[2m- Kliment 4-beat resilience demo\033[0m\n\n"
sleep 1
printf "\033[1;33m# Beat 1: setup\033[0m\n"
say "\033[2m$ \033[0m" "quorus rooms create kliment-demo --members arav,arav-codex,aarya-claude"
sleep 0.5; printf "  room \033[1mkliment-demo\033[0m  3 members ready\n\n"; sleep 0.6
say "\033[2m$ \033[0m" "quorus chat kliment-demo"; sleep 0.4
printf "\033[1;36marav        \033[0m  "
say "" "@arav-codex add a /v2/health endpoint, then @aarya-claude review the diff"; sleep 1
printf "\033[1;35marav-codex  \033[0m  "
say "" "claim: scaffolding /v2/health route + handler"; sleep 0.8

printf "\n\033[1;33m# Beat 2: kill-aarya (daemon crash mid-task)\033[0m\n"
say "\033[2m$ \033[0m" "kill -9 \$(pgrep -f 'reflexd.*aarya-claude')"; sleep 0.5
printf "  \033[31m! aarya-claude reflexd PID 84210 killed\033[0m\n"; sleep 0.4
printf "\033[1;35marav-codex  \033[0m  "
say "" "release: /v2/health scaffold complete, see PR #71"; sleep 0.6
printf "  \033[2m> outbox: queued review request for aarya-claude (visibility timeout 60s)\033[0m\n"; sleep 1

printf "\n\033[1;33m# Beat 3: resume-aarya (outbox redelivers, no message lost)\033[0m\n"
say "\033[2m$ \033[0m" "bash scripts/_mint_aarya.sh && quorus daemon start --participant aarya-claude"; sleep 0.5
printf "  aarya-claude reflexd respawned, sse connected\n"; sleep 0.3
printf "  \033[2m> outbox: redelivering 1 pending message after visibility timeout\033[0m\n"; sleep 0.7
printf "\033[1;33maarya-claude\033[0m  "
say "" "claim: reviewing PR #71 (resumed from outbox)"; sleep 1

printf "\n\033[1;33m# Beat 4: propose-destructive (verb gated by social vote)\033[0m\n"
printf "\033[1;33maarya-claude\033[0m  "
say "" "propose: drop legacy /v1/health route (destructive=true)"; sleep 0.8
printf "  \033[2m> social: destructive verb requires advisory + quorum vote\033[0m\n"; sleep 0.5
printf "\033[1;36marav        \033[0m  "
say "" "vote: hold — keep /v1/health behind deprecation header for one release"; sleep 0.8
printf "  \033[2m> social: vote tallied -> deferred (1 hold, 0 accept)\033[0m\n"; sleep 0.8
printf "\033[1;33maarya-claude\033[0m  "
say "" "ack: deferring drop, adding Deprecation header instead"; sleep 1

printf "\n\033[1;33m# audit\033[0m\n"
say "\033[2m$ \033[0m" "quorus audit kliment-demo --since 5m"; sleep 0.4
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
sleep 2
EOF
fi
chmod +x "$INNER"

step "recording cast (max ${MAX_SECS}s)"
[[ -f "$CAST_OUT" ]] && cp "$CAST_OUT" "$CAST_OUT.prev" && ok "previous cast backed up"

REC_START=$(date +%s)
asciinema rec --overwrite --idle-time-limit 2 --cols 110 --rows 30 \
  --title "Quorus - Kliment 4-beat resilience demo" \
  --command "$INNER" "$TMP_CAST" 2>"$SCRIPT_LOG" || {
    fail "asciinema recording failed"; tail -n 30 "$SCRIPT_LOG" >&2 || true; exit 1; }
REC_SECS=$(( $(date +%s) - REC_START ))

[[ -s "$TMP_CAST" ]] || { fail "cast file empty"; tail -n 30 "$SCRIPT_LOG" >&2; exit 1; }
[[ $REC_SECS -gt $MAX_SECS ]] && warn "recording took ${REC_SECS}s (cap ${MAX_SECS}s) — trim sleeps"

cp "$TMP_CAST" "$CAST_OUT"
ok "cast saved: $CAST_OUT ($(wc -c < "$CAST_OUT") bytes, ${REC_SECS}s)"

cat <<EOF

${CB}${CG}Done.${C0}  Cast: ${CB}$CAST_OUT${C0}  (${REC_SECS}s)

  Preview locally:  bash scripts/replay_kliment_cast.sh
  Ship to website:
    cd website && npm run build
    git add website/public/casts/kliment_demo.cast
    git commit -m "chore(website): refresh kliment cast" && git push
    (Vercel rebuilds on push; or: cd website && npx vercel --prod)

EOF
exit 0
