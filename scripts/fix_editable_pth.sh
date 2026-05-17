#!/bin/bash
# Quorus — fix hatchling-editable .pth files on macOS Sequoia/Tahoe + Python 3.14.
#
# Symptom: ``import quorus_sdk`` fails after ``pip install -e .`` despite the
# .pth file existing in site-packages. Verbose Python output shows
# ``Skipping hidden .pth file:`` for every quorus _editable_impl_*.pth.
#
# Root cause: hatchling's editable backend creates .pth files that get tagged
# with the macOS ``com.apple.provenance`` extended attribute by recent macOS
# releases. That xattr causes the kernel to keep ``UF_HIDDEN`` re-applied even
# after ``chflags nohidden``. Python 3.14 added a security check that
# silently skips .pth files with UF_HIDDEN set (CPython site.addpackage,
# 2025-era hardening).
#
# Fix: strip all extended attributes, then clear all file flags. The
# ``com.apple.provenance`` xattr must go BEFORE chflags or UF_HIDDEN
# re-applies on the next stat().
#
# Run after every ``pip install -e``. Idempotent and harmless on Linux.

set -euo pipefail

VENV_DIR="${1:-.venv}"

if [ "$(uname -s)" != "Darwin" ]; then
  # Linux / WSL / CI containers don't have UF_HIDDEN, nothing to do.
  exit 0
fi

# Find every quorus-related .pth in the venv (covers both the legacy
# _editable_impl_* layout and the post-rename quorus_editable_* layout).
# Using a while-read loop for bash 3.2 compatibility (macOS default shell).
COUNT=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  xattr -c "$f" 2>/dev/null || true
  chflags 0 "$f" 2>/dev/null || true
  COUNT=$((COUNT + 1))
done < <(
  find "$VENV_DIR" -type f \( -name '_editable_impl_quorus*.pth' -o -name 'quorus_editable_*.pth' \) 2>/dev/null
)

if [ "$COUNT" -eq 0 ]; then
  exit 0
fi

# Verify Python now picks them up. Fail loudly if not — silent failure here
# is exactly the bug we're working around.
if ! "$VENV_DIR/bin/python" -c "import quorus_sdk" 2>/dev/null; then
  echo "fix_editable_pth.sh: .pth files cleaned but import still failing." >&2
  echo "Run with QUORUS_DEBUG_PTH=1 to see the full trace." >&2
  if [ "${QUORUS_DEBUG_PTH:-}" = "1" ]; then
    "$VENV_DIR/bin/python" -v -c "pass" 2>&1 | grep -i pth | head -20 >&2
  fi
  exit 1
fi

echo "fix_editable_pth.sh: cleaned $COUNT .pth file(s) — imports OK"
