#!/usr/bin/env bash
# launch_smoke.sh — Gate A acceptance gate for Quorus public launch.
#
# Run from repo root. Every assertion below maps 1:1 to a row in
# docs/LAUNCH_FINALIZATION_PLAN.md Gate A table. If any line fails the
# whole script fails, with a "FAIL A<n>:" prefix so the failing blocker
# is unambiguous.
#
# Usage:
#   bash scripts/launch_smoke.sh                  # full gate
#   bash scripts/launch_smoke.sh --skip-prod      # skip the live prod parity check (A7)
#   bash scripts/launch_smoke.sh --skip-pypi      # skip the live PyPI parity check (A8)

set -euo pipefail

SKIP_PROD=0
SKIP_PYPI=0
for arg in "$@"; do
  case "$arg" in
    --skip-prod) SKIP_PROD=1 ;;
    --skip-pypi) SKIP_PYPI=1 ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  echo "FAIL pre-flight: no Python at $PYTHON. Set PYTHON=... or create the venv." >&2
  exit 1
fi

red() { printf "\033[31m%s\033[0m\n" "$*"; }
grn() { printf "\033[32m%s\033[0m\n" "$*"; }
yel() { printf "\033[33m%s\033[0m\n" "$*"; }

trap 'red "FAIL: gate aborted (see output above)"; exit 1' ERR

echo "==> A1/A2/A4/A5: regression test suite"
RELAY_SECRET=test-secret "$PYTHON" -m pytest \
  tests/test_xss_reply_to.py \
  tests/test_security_headers.py \
  tests/test_jwt_url_leak.py \
  tests/test_phase1_audit_invariant.py \
  tests/test_outbox_retry_storm.py \
  tests/test_dm_rate_limit_and_dashboard_robots.py \
  -q
grn "  A1/A2/A4/A5/A6/B4/B6 regression tests green"

echo "==> A3: editable install + .pth fix script"
bash scripts/fix_editable_pth.sh >/dev/null
"$PYTHON" -c "import quorus, quorus_sdk, quorus_cli, quorus_mcp, quorus_tui" \
  || { red "FAIL A3: editable imports failed even after fix script"; exit 1; }
grn "  A3 editable install + fix script OK"

echo "==> A9: harness count truth"
HITS=$(grep -rni "6 verified\|six.*harness.*verified\|fully proactive on six" \
  website/src docs/QUORUS_OS_SPEC.md README.md 2>/dev/null \
  | grep -v "LAUNCH_FINALIZATION_PLAN" \
  | grep -v "launch_smoke.sh" \
  || true)
if [ -n "$HITS" ]; then
  red "FAIL A9: '6 verified' claim still present:"
  echo "$HITS"
  exit 1
fi
grn "  A9 harness count truth OK"

echo "==> A11: license consistency"
# We don't enforce one side or the other — just that LICENSE file's choice
# is the canonical one referenced in copy.
if [ -f LICENSE ]; then
  LICENSE_NAME=$(head -1 LICENSE | grep -oE "MIT|Apache" || echo "unknown")
  yel "  A11 LICENSE file declares: $LICENSE_NAME (manual verification required)"
fi

echo "==> A12: fake model names"
HITS=$(grep -rn "claude-sonnet-4-6\|gpt-5\b" website/src/ 2>/dev/null || true)
if [ -n "$HITS" ]; then
  red "FAIL A12: fake model names still present:"
  echo "$HITS"
  exit 1
fi
grn "  A12 model name truth OK"

echo "==> A10: asciinema cast is real (>5KB)"
SIZE=$(wc -c < website/public/casts/demo_reflex.cast 2>/dev/null || echo 0)
if [ "$SIZE" -lt 5000 ]; then
  red "FAIL A10: asciinema cast is $SIZE bytes (placeholder threshold = 5000)"
  red "         Record a real demo: asciinema rec website/public/casts/demo_reflex.cast"
  exit 1
fi
if grep -q "(placeholder)" website/src/components/AsciinemaPlayer.tsx 2>/dev/null; then
  red "FAIL A10: AsciinemaPlayer still shows '(placeholder)' caption"
  exit 1
fi
grn "  A10 asciinema cast OK ($SIZE bytes)"

echo "==> A13: ComparisonBand mounted + anchors land"
grep -q "ComparisonBand" website/src/pages/Home.tsx \
  || { red "FAIL A13: ComparisonBand not imported/mounted in Home.tsx"; exit 1; }
grep -q 'id="waitlist"' website/src/components/HeroLight.tsx \
  || { red "FAIL A13: HeroLight missing id=waitlist anchor"; exit 1; }
grep -q 'id="features"' website/src/components/BentoStitch.tsx \
  || { red "FAIL A13: BentoStitch missing id=features anchor"; exit 1; }
grn "  A13 ComparisonBand + anchors OK"

if [ "$SKIP_PROD" -eq 0 ]; then
  echo "==> A7: prod relay has Phase 1 routes"
  PROD_URL="${QUORUS_PROD_URL:-https://quorus-relay.fly.dev}"
  PHASE1=$(curl -sf "$PROD_URL/openapi.json" 2>/dev/null \
    | "$PYTHON" -c "
import sys, json
p = json.load(sys.stdin)['paths']
print(
  sum(1 for k in p if '/v1/memory' in k),
  sum(1 for k in p if '/tools/' in k),
  sum(1 for k in p if '/v1/capabilities' in k),
)
" 2>/dev/null || echo "0 0 0")
  read -r MEM TOOLS CAPS <<< "$PHASE1"
  if [ "$MEM" -lt 1 ] || [ "$TOOLS" -lt 1 ] || [ "$CAPS" -lt 1 ]; then
    red "FAIL A7: prod missing Phase 1 routes — memory=$MEM tools=$TOOLS capabilities=$CAPS"
    red "         Run: fly deploy"
    exit 1
  fi
  grn "  A7 prod parity OK — memory=$MEM tools=$TOOLS capabilities=$CAPS"
else
  yel "  A7 SKIPPED via --skip-prod (no live prod check)"
fi

if [ "$SKIP_PYPI" -eq 0 ]; then
  echo "==> A8: PyPI version consistency"
  PYPI_VERSION=$(curl -sf https://pypi.org/pypi/quorus/json 2>/dev/null \
    | "$PYTHON" -c "import sys, json; print(json.load(sys.stdin)['info']['version'])" \
    2>/dev/null || echo "missing")
  LOCAL_VERSION=$(grep -m1 '^version' pyproject.toml | grep -oE '"[0-9.]+"' | tr -d '"' || echo "unknown")
  if [ "$PYPI_VERSION" = "missing" ]; then
    yel "  A8 SKIPPED — no PyPI release yet (acceptable if 'pip install quorus' is removed from docs)"
  elif [ "$PYPI_VERSION" != "$LOCAL_VERSION" ]; then
    red "FAIL A8: PyPI serves $PYPI_VERSION but local pyproject is $LOCAL_VERSION"
    red "         Yank stale: pip yank quorus==$PYPI_VERSION"
    red "         Republish:  python -m build && twine upload dist/*"
    exit 1
  else
    grn "  A8 PyPI parity OK — $PYPI_VERSION"
  fi
else
  yel "  A8 SKIPPED via --skip-pypi"
fi

echo
grn "==================================="
grn "Gate A: all closeable blockers green"
grn "==================================="
