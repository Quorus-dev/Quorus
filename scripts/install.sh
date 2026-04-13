#!/bin/bash
# Murmur Quick Install Script
# Usage: curl -fsSL https://raw.githubusercontent.com/Aarya2004/murmur/main/scripts/install.sh | bash

set -e

echo ""
echo "  Installing Murmur..."
echo ""

# Check for Python 3.10+
if ! command -v python3 &> /dev/null; then
    echo "  Error: Python 3.10+ is required"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ $(echo "$PYTHON_VERSION < 3.10" | bc -l) -eq 1 ]]; then
    echo "  Error: Python 3.10+ required (found $PYTHON_VERSION)"
    exit 1
fi

# Install with pip
if command -v uv &> /dev/null; then
    echo "  Using uv..."
    uv pip install murmur-ai
elif command -v pipx &> /dev/null; then
    echo "  Using pipx..."
    pipx install murmur-ai
else
    echo "  Using pip..."
    pip install --user murmur-ai
fi

echo ""
echo "  Done! Run 'murmur begin' to start."
echo ""
