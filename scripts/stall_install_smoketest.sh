#!/usr/bin/env bash
# scripts/stall_install_smoketest.sh - cold-install reproducer for the stall.
#
# Simulates a stranger sitting at the booth and saying "let me try":
#   1. Fresh venv at /tmp/stall-cold + pipx
#   2. pipx install quorus from GitHub (TIMED, warns if >120s)
#   3. quorus version  (banner check)
#   4. quorus doctor   (relay-reachable / auth-reachable check)
#   5. Tear down /tmp/stall-cold
#   6. Print PASS/FAIL with timing

set -u
set -o pipefail

COLD_DIR="${QUORUS_COLD_DIR:-/tmp/stall-cold}"
GIT_URL="${QUORUS_GIT_URL:-git+https://github.com/Quorus-dev/Quorus.git}"
WARN_SECS="${QUORUS_INSTALL_WARN_SECS:-120}"

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

cleanup() { rm -rf "$COLD_DIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

OVERALL_START=$(date +%s)

# Step 0: prereqs
say "step 0: prereqs"
command -v python3 >/dev/null || { fail "python3 not on PATH"; exit 1; }
PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
case "$PYV" in 3.1[0-9]|3.[2-9][0-9]) ok "python $PYV" ;;
  *) fail "python $PYV too old; need 3.10+"; exit 1 ;;
esac

# Step 1: cold venv + pipx
say "step 1: fresh venv at $COLD_DIR"
rm -rf "$COLD_DIR" 2>/dev/null || true
python3 -m venv "$COLD_DIR" 2>/tmp/.cold-venv.err \
  || { fail "venv create failed"; cat /tmp/.cold-venv.err >&2 || true; exit 1; }
PIP="$COLD_DIR/bin/pip"; PIPX="$COLD_DIR/bin/pipx"
"$PIP" install --quiet --no-cache-dir --upgrade pip 2>/dev/null || true
"$PIP" install --quiet --no-cache-dir pipx 2>/tmp/.cold-pipx.err \
  || { fail "pip install pipx failed"; cat /tmp/.cold-pipx.err >&2 || true; exit 1; }
ok "pipx ready in venv"

# Step 2: pipx install quorus (TIMED)
say "step 2: pipx install quorus (timed)"
INSTALL_START=$(date +%s)
export PIPX_HOME="$COLD_DIR/pipx-home" PIPX_BIN_DIR="$COLD_DIR/pipx-bin"
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p "$PIPX_HOME" "$PIPX_BIN_DIR"
if ! "$PIPX" install --pip-args="--no-cache-dir" "quorus @ $GIT_URL" \
    >/tmp/.cold-install.log 2>&1; then
  fail "pipx install failed after $(( $(date +%s) - INSTALL_START ))s"
  tail -20 /tmp/.cold-install.log >&2 || true
  exit 1
fi
INSTALL_SECS=$(( $(date +%s) - INSTALL_START ))
QBIN="$PIPX_BIN_DIR/quorus"
[[ -x "$QBIN" ]] || { fail "quorus binary missing at $QBIN"; ls "$PIPX_BIN_DIR" >&2; exit 1; }
if [[ $INSTALL_SECS -gt $WARN_SECS ]]; then
  warn "install took ${INSTALL_SECS}s (>${WARN_SECS}s threshold)"
else
  ok "install took ${INSTALL_SECS}s"
fi

# Step 3: quorus version
say "step 3: quorus version"
"$QBIN" version >/tmp/.cold-version.out 2>&1 \
  || { fail "version exited non-zero"; cat /tmp/.cold-version.out >&2; exit 1; }
if grep -qiE 'quorus|coordination' /tmp/.cold-version.out; then
  ok "version banner present"
  grep -E 'v[0-9]+\.[0-9]+' /tmp/.cold-version.out | head -1 | sed 's/^/    /' || true
else
  warn "version banner missing (output below)"; head -8 /tmp/.cold-version.out >&2 || true
fi

# Step 4: quorus doctor
say "step 4: quorus doctor"
DOC_RC=0; "$QBIN" doctor >/tmp/.cold-doctor.out 2>&1 || DOC_RC=$?
RELAY_OK=0; AUTH_OK=0
grep -qiE 'relay.*reach|relay.*health|relay.*ok|relay url' /tmp/.cold-doctor.out && RELAY_OK=1
grep -qiE 'auth|profile|token|whoami|identity|api[_ -]key' /tmp/.cold-doctor.out && AUTH_OK=1
if [[ $RELAY_OK -eq 1 && $AUTH_OK -eq 1 ]]; then
  ok "doctor: relay-reachable + auth-reachable"
else
  warn "doctor (rc=$DOC_RC) missing probes:"
  [[ $RELAY_OK -eq 0 ]] && warn "  -> no relay-reachable line"
  [[ $AUTH_OK  -eq 0 ]] && warn "  -> no auth-reachable line"
  head -20 /tmp/.cold-doctor.out >&2 || true
fi

TOTAL_SECS=$(( $(date +%s) - OVERALL_START ))

# PASS/FAIL banner
if [[ $RELAY_OK -eq 1 && $AUTH_OK -eq 1 ]]; then
  printf '\n%s%sCOLD-INSTALL SMOKETEST: PASS%s\n' "$CB" "$CG" "$C0"
  printf '  install: %ds  total: %ds\n' "$INSTALL_SECS" "$TOTAL_SECS"
  exit 0
fi
printf '\n%s%sCOLD-INSTALL SMOKETEST: PARTIAL%s\n' "$CB" "$CY" "$C0"
printf '  install: %ds  total: %ds\n' "$INSTALL_SECS" "$TOTAL_SECS"
printf '  relay probe: %s  auth probe: %s\n' \
  "$([[ $RELAY_OK -eq 1 ]] && echo PASS || echo FAIL)" \
  "$([[ $AUTH_OK -eq 1 ]] && echo PASS || echo FAIL)"
exit 1
