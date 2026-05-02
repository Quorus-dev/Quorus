#!/usr/bin/env bash
# Patch the .venv entry-point scripts so they inject sys.path themselves,
# bypassing the macOS Python-3.14 + Spotlight UF_HIDDEN .pth bug entirely.
#
# Background: hatchling editable installs rely on a `.pth` file in
# site-packages. On macOS, mdworker (Spotlight) keeps re-applying UF_HIDDEN
# to that .pth file via com.apple.provenance, and CPython 3.14 silently
# skips hidden .pth files (gh-117983). Result: `import quorus` fails.
#
# This script rewrites each entry-point script (quorus, quorus-relay,
# quorus-mcp, quorus-analytics) to prepend the 5 package source dirs to
# sys.path BEFORE the entry-point's first import, so we never depend on
# .pth file processing in the first place.
#
# Idempotent: if the entry-point already has the bootstrap, nothing changes.

set -euo pipefail
REPO_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
VENV_DIR="${REPO_DIR}/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[patch_entry_points] no venv at $VENV_DIR — skipping" >&2
  exit 0
fi

BIN_DIR="$VENV_DIR/bin"
PY_SHEBANG="$(head -1 "$BIN_DIR/quorus" 2>/dev/null || echo "")"

if [[ -z "$PY_SHEBANG" ]] || [[ "${PY_SHEBANG:0:2}" != "#!" ]]; then
  echo "[patch_entry_points] cannot find quorus entry-point — skipping" >&2
  exit 0
fi

# Build the bootstrap prelude. Hard-coded repo path is safe because
# editable installs are always tied to one source tree. Quote with a
# Python repr to handle any path containing spaces or quotes.
QUOTED_REPO="$(python3 -c "import sys; print(repr(sys.argv[1]))" "$REPO_DIR")"
PRELUDE="import sys, os
_QUORUS_REPO = ${QUOTED_REPO}
for _p in (
    _QUORUS_REPO,
    os.path.join(_QUORUS_REPO, 'packages', 'sdk'),
    os.path.join(_QUORUS_REPO, 'packages', 'cli'),
    os.path.join(_QUORUS_REPO, 'packages', 'mcp'),
    os.path.join(_QUORUS_REPO, 'packages', 'tui'),
):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
del _QUORUS_REPO, _p"

MARKER="# quorus-bootstrap-marker-v1"

for ep in quorus quorus-relay quorus-mcp quorus-analytics; do
  f="$BIN_DIR/$ep"
  [[ -f "$f" ]] || continue
  if grep -q "$MARKER" "$f"; then
    continue  # already patched
  fi
  shebang="$(head -1 "$f")"
  body="$(tail -n +2 "$f")"
  {
    echo "$shebang"
    echo "$MARKER"
    echo "$PRELUDE"
    echo "$body"
  } > "${f}.tmp"
  chmod +x "${f}.tmp"
  mv "${f}.tmp" "$f"
  echo "[patch_entry_points] patched $f"
done
