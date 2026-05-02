#!/usr/bin/env bash
# Quorus Quick Setup
#
# Two modes:
#   ./setup.sh <name> [relay-url] [secret]
#       Configure ~/.quorus/config.json + print MCP wiring (legacy mode).
#
#   ./setup.sh --install [path/to/python]
#       Provision a working .venv at the repo root with all 5 packages
#       installed editable, on Python 3.10/3.11/3.12/3.13/3.14.
#       This is the path that broke at the Apr 23 2026 hackathon — see
#       docs/HACKATHON_REGRESSION.md for full background.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# Mode 1: --install — fresh dev venv with editable install of all 5 packages.
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--install" ]]; then
  PYTHON_BIN="${2:-python3}"
  VENV_DIR="${SCRIPT_DIR}/.venv"

  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON_BIN not found on PATH" >&2
    exit 1
  fi

  PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  echo "[setup] Using $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

  # IMPORTANT: use stdlib `python -m venv`, NOT `uv venv`.
  # `uv venv` sets the macOS UF_HIDDEN flag on every file inside the venv so
  # Spotlight/Finder won't index them. Python 3.14's site.py SKIPS .pth files
  # whose UF_HIDDEN bit is set (CPython gh-117983), which means hatchling's
  # editable shim files (`_editable_impl_quorus.pth`, …) are silently ignored
  # and `import quorus_sdk` fails after a clean editable install.
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "[setup] Creating venv at $VENV_DIR via stdlib 'python -m venv' (NOT uv venv)"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  else
    echo "[setup] Reusing existing venv at $VENV_DIR"
  fi

  PIP="$VENV_DIR/bin/pip"
  PY="$VENV_DIR/bin/python"

  echo "[setup] Upgrading pip"
  "$PIP" install --quiet --upgrade pip

  echo "[setup] Installing root + 4 subpackages editable"
  # Root pyproject's [tool.hatch.build.targets.wheel] packages list pulls in
  # all 5 source trees, so a single `pip install -e .` is enough — the
  # generated `_editable_impl_quorus.pth` lists every package's parent dir.
  # We still install [test] so pytest + asyncio + fakeredis are available.
  "$PIP" install --quiet -e ".[test]"

  # Belt + braces fix for Python 3.14 + uv-style hidden flags.
  # If anything in the install chain (uv, conda, a sandbox, an HFS attribute
  # template) marked the freshly-written .pth files as macOS-hidden, strip
  # the flag so site.py loads them.
  if [[ "$(uname -s)" == "Darwin" ]]; then
    SITE_DIR="$VENV_DIR/lib/python${PY_VERSION}/site-packages"
    if [[ -d "$SITE_DIR" ]]; then
      # `chflags nohidden` is a no-op when the flag isn't set, so this is safe
      # to always run. Use shopt-style loop to avoid noisy errors when no
      # files match.
      shopt -s nullglob 2>/dev/null || true
      for pth in "$SITE_DIR"/_editable_impl_*.pth "$SITE_DIR"/*.pth; do
        chflags nohidden "$pth" 2>/dev/null || true
      done
    fi
  fi

  echo "[setup] Verifying topology"
  "$PY" - <<'EOF'
import importlib, sys

failed = []
for mod in ("quorus", "quorus_sdk", "quorus_cli", "quorus_mcp", "quorus_tui"):
    try:
        importlib.import_module(mod)
        print(f"  ok  {mod}")
    except Exception as e:
        failed.append((mod, e))
        print(f"  FAIL {mod}: {e!r}")

# CLI entry point must be importable even without console scripts.
try:
    from quorus_cli.cli import main  # noqa: F401
    print("  ok  quorus_cli.cli:main")
except Exception as e:
    failed.append(("quorus_cli.cli:main", e))
    print(f"  FAIL quorus_cli.cli:main: {e!r}")

if failed:
    print(f"\n[setup] FAILED — {len(failed)} import(s) broken", file=sys.stderr)
    sys.exit(1)
print("\n[setup] All 5 packages import cleanly.")
EOF

  echo ""
  echo "[setup] Done. Activate with:  source $VENV_DIR/bin/activate"
  echo "[setup] Or run directly:      $VENV_DIR/bin/quorus --help"
  exit 0
fi

# ---------------------------------------------------------------------------
# Mode 2 (legacy): write ~/.quorus/config.json and print MCP wiring snippet.
# ---------------------------------------------------------------------------
NAME="${1:?Usage: ./setup.sh <your-name> [relay-url] [secret]   (or ./setup.sh --install [python3.x])}"
RELAY_URL="${2:-http://localhost:8080}"
SECRET="${3:-quorus-dev}"

echo "Setting up Quorus for: $NAME"
echo "  Relay: $RELAY_URL"
echo ""

mkdir -p ~/.quorus
umask 077
cat > ~/.quorus/config.json <<EOF
{
  "relay_url": "$RELAY_URL",
  "relay_secret": "$SECRET",
  "instance_name": "$NAME",
  "enable_background_polling": true,
  "push_notification_method": "notifications/claude/channel",
  "push_notification_channel": "quorus"
}
EOF
chmod 0600 ~/.quorus/config.json
echo "Config written to ~/.quorus/config.json"

echo ""
echo "Add this to your Claude Code MCP settings:"
echo ""
echo "  Name: quorus"
echo "  Command: uv run --directory $SCRIPT_DIR python -m quorus.mcp_server"
echo ""
echo "Or add to ~/.claude.json:"
cat <<EOF
{
  "mcpServers": {
    "quorus": {
      "command": "uv",
      "args": ["run", "--directory", "$SCRIPT_DIR", "python", "-m", "quorus.mcp_server"]
    }
  }
}
EOF

echo ""
echo "Start the relay:"
echo "  cd $SCRIPT_DIR && RELAY_SECRET=$SECRET uv run quorus-relay"
echo ""
echo "Or just open the hub:"
echo "  quorus"
echo ""
echo "Need a fresh dev install? ./setup.sh --install python3.13"
