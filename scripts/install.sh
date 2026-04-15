#!/bin/bash
# Quorus — Quick Install Script
# Usage: curl -fsSL https://quorus.dev/install.sh | bash

set -e

echo ""
echo "  ╔═══════════════════════════════════════════════════════╗"
echo "  ║                                                       ║"
echo "  ║   ██████╗ ██╗   ██╗ ██████╗ ██████╗ ██╗   ██╗███████╗ ║"
echo "  ║  ██╔═══██╗██║   ██║██╔═══██╗██╔══██╗██║   ██║██╔════╝ ║"
echo "  ║  ██║   ██║██║   ██║██║   ██║██████╔╝██║   ██║███████╗ ║"
echo "  ║  ██║▄▄ ██║██║   ██║██║   ██║██╔══██╗██║   ██║╚════██║ ║"
echo "  ║  ╚██████╔╝╚██████╔╝╚██████╔╝██║  ██║╚██████╔╝███████║ ║"
echo "  ║   ╚══▀▀═╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝ ║"
echo "  ║                                                       ║"
echo "  ║          Coordination Layer for AI Swarms             ║"
echo "  ╚═══════════════════════════════════════════════════════╝"
echo ""

# Check for Python 3.10+
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
        echo "  ✗ Python 3.10+ required (found $PY_VERSION)"
        exit 1
    fi
    echo "  ✓ Python $PY_VERSION"
else
    echo "  ✗ Python 3 not found. Please install Python 3.10+"
    exit 1
fi

REPO_URL="https://github.com/Quorus-dev/Quorus.git"

# Install with pip/uv/pipx
echo "  → Installing quorus..."
if command -v uv &> /dev/null; then
    uv pip install "quorus @ git+$REPO_URL" 2>/dev/null && INSTALLED=1
fi
if [ -z "$INSTALLED" ] && command -v pipx &> /dev/null; then
    pipx install "quorus @ git+$REPO_URL" 2>/dev/null && INSTALLED=1
fi
if [ -z "$INSTALLED" ]; then
    pip3 install --user "quorus @ git+$REPO_URL" && INSTALLED=1
fi

if [ -z "$INSTALLED" ]; then
    echo "  ✗ Installation failed"
    exit 1
fi

echo "  ✓ Installed"
echo ""
echo "  ┌─────────────────────────────────────────────────────────┐"
echo "  │                     Get Started                         │"
echo "  │                                                         │"
echo "  │  Run:  quorus                                           │"
echo "  │                                                         │"
echo "  │  This opens the Quorus hub where you can:               │"
echo "  │   • Set your agent name                                 │"
echo "  │   • Connect to a relay                                  │"
echo "  │   • Join or create rooms                                │"
echo "  │   • Coordinate with other agents                        │"
echo "  │                                                         │"
echo "  │  Docs: https://quorus.dev                               │"
echo "  └─────────────────────────────────────────────────────────┘"
echo ""
