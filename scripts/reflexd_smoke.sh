#!/usr/bin/env bash
# scripts/reflexd_smoke.sh — CI sanity for the reflexd headless adapters.
#
# Runs ``python scripts/reflexd.py --dry-run --once <fake-room>`` for each of
# {claude, codex, gemini, cursor} and asserts that:
#   1. The command exits 0.
#   2. The stdout JSON has ok=true.
#   3. The argv_recorded matches the pinned shape for that harness.
#
# This is the integration-level mirror of tests/test_reflexd_adapters.py: the
# unit tests verify the helpers in isolation; this script verifies the end-to-
# end CLI plumbing without ever spawning a real harness binary.
#
# Exits non-zero on any failure. Idempotent. No network. No PHI. Safe for CI.

set -u
set -o pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Find a python interpreter — prefer the repo's .venv.
if [[ -x "$REPO_ROOT/.venv/bin/python3" ]]; then
  PY="$REPO_ROOT/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  echo "ERROR: no python3 found" >&2
  exit 2
fi

REFLEXD="$REPO_ROOT/scripts/reflexd.py"
if [[ ! -f "$REFLEXD" ]]; then
  echo "ERROR: $REFLEXD missing" >&2
  exit 2
fi

# Make absolutely sure no real config / pidfile is touched. The script's
# job is contract-checking, not lifecycle.
TMPHOME="$(mktemp -d -t reflexd-smoke.XXXXXX)"
export HOME="$TMPHOME"
trap 'rm -rf "$TMPHOME"' EXIT

# --once / --dry-run inject placeholders for participant / api-key / relay
# so we don't need a relay running for this smoke.
unset RELAY_URL API_KEY REFLEXD_PARTICIPANT REFLEXD_LEGACY_BEARER REFLEXD_STUB_REPLY 2>/dev/null || true

FAKE_ROOM="reflexd-smoke-room"

PASS=0
FAIL=0

check_harness() {
  local harness="$1"
  local participant="$2"
  local expected_marker="$3"

  echo "─── smoke: harness=$harness participant=$participant ───"
  local out
  if ! out="$("$PY" "$REFLEXD" --dry-run --once "$FAKE_ROOM" --participant "$participant" 2>&1)"; then
    echo "FAIL: reflexd exited non-zero for $harness" >&2
    echo "$out" >&2
    FAIL=$((FAIL + 1))
    return
  fi

  # We expect a single JSON line on stdout. ``grep -F -e`` keeps BSD grep on
  # macOS from interpreting leading dashes (e.g. --prompt, --headless) as
  # its own command-line flags.
  local json="$out"
  if ! grep -F -q -e '"ok": true' <<<"$json"; then
    echo "FAIL: expected ok=true in output for $harness" >&2
    echo "$json" >&2
    FAIL=$((FAIL + 1))
    return
  fi

  if ! grep -F -q -e "\"harness\": \"$harness\"" <<<"$json"; then
    echo "FAIL: expected harness=$harness in output" >&2
    echo "$json" >&2
    FAIL=$((FAIL + 1))
    return
  fi

  if ! grep -F -q -e "$expected_marker" <<<"$json"; then
    echo "FAIL: expected argv-marker '$expected_marker' for $harness" >&2
    echo "$json" >&2
    FAIL=$((FAIL + 1))
    return
  fi

  echo "OK: $harness adapter recorded $expected_marker"
  PASS=$((PASS + 1))
}

# Each call asserts a substring that uniquely identifies the pinned argv shape
# for that harness — so a flag rename in any one of them flunks this script.
check_harness "claude" "smoke-claude" "claude_agent_sdk"
check_harness "codex"  "smoke-codex"  "exec"
check_harness "gemini" "smoke-gemini" "--prompt"
check_harness "cursor" "smoke-cursor" "--headless"

echo
echo "reflexd smoke: $PASS passed, $FAIL failed"
if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi
exit 0
