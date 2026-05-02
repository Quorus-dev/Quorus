#!/usr/bin/env bash
# cold_install_smoke.sh — local reproducer for the cold-install CI gate.
#
# Mirrors what .github/workflows/cold-install.yml does on every PR:
#   1. Verifies pipx is available (or falls back to a venv install).
#   2. Cold-installs the current checkout into an ISOLATED prefix
#      (no cached pip wheels, no shared venv state).
#   3. Runs scripts/cold_install_smoke.py end-to-end with a timeout.
#
# Usage:
#   scripts/cold_install_smoke.sh                  # full pipx cold install + smoke
#   scripts/cold_install_smoke.sh --skip-install   # use whatever quorus is on PATH
#   scripts/cold_install_smoke.sh --port 19090     # override smoke port
#
# Exits 1 with a clear "FAIL at step N" message on any failure.
# POSIX-bash-clean enough to run under macOS bash 3.2.

set -eu

# ---- arg parsing ------------------------------------------------------------
SKIP_INSTALL=0
SMOKE_PORT="${QUORUS_SMOKE_PORT:-18080}"
SMOKE_TIMEOUT="${QUORUS_SMOKE_TIMEOUT:-60}"

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-install) SKIP_INSTALL=1 ;;
        --port)         SMOKE_PORT="$2"; shift ;;
        --timeout)      SMOKE_TIMEOUT="$2"; shift ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

# Locate repo root (script lives in scripts/).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- step 0: pre-flight -----------------------------------------------------
echo "==> step 0: pre-flight"
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "FAIL at step 0: python3 not on PATH" >&2
    exit 1
fi
echo "  python: $($PYTHON_BIN --version 2>&1)"

# ---- step 1: cold install ---------------------------------------------------
SMOKE_HOME="$(mktemp -d 2>/dev/null || mktemp -d -t quorus-smoke)"
trap 'rm -rf "$SMOKE_HOME" 2>/dev/null || true' EXIT INT TERM

if [ "$SKIP_INSTALL" -eq 1 ]; then
    echo "==> step 1: skipping install (using PATH-resolved quorus)"
else
    echo "==> step 1: cold install via pipx (isolated prefix at $SMOKE_HOME)"

    # Force pipx into an isolated home so we don't pollute the user's pipx env,
    # and disable any pip cache so this is a true cold-install.
    export PIPX_HOME="$SMOKE_HOME/pipx"
    export PIPX_BIN_DIR="$SMOKE_HOME/bin"
    export PIP_NO_CACHE_DIR=1
    export PIP_DISABLE_PIP_VERSION_CHECK=1
    PATH="$PIPX_BIN_DIR:$PATH"
    export PATH

    if ! command -v pipx >/dev/null 2>&1; then
        echo "  pipx not found — installing into ephemeral venv"
        "$PYTHON_BIN" -m pip install --user --no-cache-dir --quiet pipx || {
            echo "FAIL at step 1: could not install pipx" >&2
            exit 1
        }
    fi

    # Install from the local checkout (mirrors a PR-time install).
    if ! pipx install --force --pip-args="--no-cache-dir" "$REPO_ROOT"; then
        echo "FAIL at step 1: pipx install of local checkout failed" >&2
        exit 1
    fi

    # Make sure the entrypoints are now on PATH.
    if ! command -v quorus >/dev/null 2>&1; then
        echo "FAIL at step 1: quorus binary not on PATH after install" >&2
        exit 1
    fi
    if ! command -v quorus-relay >/dev/null 2>&1; then
        echo "FAIL at step 1: quorus-relay binary not on PATH after install" >&2
        exit 1
    fi
    echo "  quorus:       $(command -v quorus)"
    echo "  quorus-relay: $(command -v quorus-relay)"
fi

# ---- step 2: version check (cheap import smoke) -----------------------------
echo "==> step 2: version smoke"
if ! quorus version >/dev/null 2>&1; then
    # Some builds print version on `--version`; try both before failing.
    if ! quorus --version >/dev/null 2>&1; then
        echo "FAIL at step 2: quorus version command failed — broken install" >&2
        exit 1
    fi
fi
echo "  quorus version OK"

# ---- step 3: run the python smoke (relay + roundtrip) -----------------------
echo "==> step 3: end-to-end smoke (port=$SMOKE_PORT, timeout=${SMOKE_TIMEOUT}s)"

# macOS bash 3.2 has no `timeout` builtin; use whichever exists.
TIMEOUT_CMD=""
if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD="timeout ${SMOKE_TIMEOUT}s"
elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_CMD="gtimeout ${SMOKE_TIMEOUT}s"
fi

if [ -n "$TIMEOUT_CMD" ]; then
    # shellcheck disable=SC2086
    $TIMEOUT_CMD "$PYTHON_BIN" "$SCRIPT_DIR/cold_install_smoke.py" \
        --port "$SMOKE_PORT" --timeout "$SMOKE_TIMEOUT"
else
    # Fallback: trust the script's internal budget enforcement.
    "$PYTHON_BIN" "$SCRIPT_DIR/cold_install_smoke.py" \
        --port "$SMOKE_PORT" --timeout "$SMOKE_TIMEOUT"
fi

echo "==> PASS: cold-install smoke clean"
