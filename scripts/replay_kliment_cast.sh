#!/usr/bin/env bash
# scripts/replay_kliment_cast.sh — play the recorded Kliment demo cast at 1.5x.
#
# Usage:
#   ./scripts/replay_kliment_cast.sh                # 1.5x default
#   ./scripts/replay_kliment_cast.sh 2.0            # custom speed multiplier
#   ./scripts/replay_kliment_cast.sh --idle 1       # also cap idle to 1s
#
# Useful for:
#   - Rehearsing the demo before the venue
#   - Walking visitors through the flow when Arav is busy with another visitor
#   - Verifying a freshly-recorded cast looks right before pushing to website

set -u
set -o pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CAST_PATH="${KLIMENT_CAST:-$REPO_ROOT/website/public/casts/kliment_demo.cast}"

SPEED="1.5"
IDLE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --idle)
      IDLE="$2"; shift 2 ;;
    --help|-h)
      sed -n '2,12p' "$0"
      exit 0 ;;
    -*)
      echo "unknown flag: $1" >&2
      exit 2 ;;
    *)
      SPEED="$1"; shift ;;
  esac
done

if ! command -v asciinema >/dev/null 2>&1; then
  echo "asciinema not installed — brew install asciinema (macOS) or pipx install asciinema (Linux)" >&2
  exit 2
fi

if [[ ! -s "$CAST_PATH" ]]; then
  echo "cast not found or empty: $CAST_PATH" >&2
  echo "record one first: bash scripts/record_kliment_cast.sh" >&2
  exit 2
fi

ARGS=(play "$CAST_PATH" --speed "$SPEED")
if [[ -n "$IDLE" ]]; then
  ARGS+=(--idle-time-limit "$IDLE")
fi

echo "playing $CAST_PATH at ${SPEED}x${IDLE:+ (idle cap ${IDLE}s)}"
exec asciinema "${ARGS[@]}"
