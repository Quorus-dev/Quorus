#!/bin/sh
# The Show HN path, end to end, on the wheel: plain `quorus` from an installed
# quorus-ai (never a checkout) in a pty with a tmp HOME and stub codex + opencode,
# through the first run's default review gate: the gate question, the "Review gate
# on" line naming the second reviewer, a fix held off main, the reviewer's GO, and
# the "reviewed by" publish. These are tests/firstrun/test_gate_first_run.py's two
# episodes, run against the package a user installs instead of the worktree.
#
#   scripts/wheel_first_run.sh            build this checkout's wheel, install it into
#                                         a fresh venv, run the episodes against it
#   scripts/wheel_first_run.sh some.whl   same, from a wheel already built
#   QUORUS_PYTHON=<interp> scripts/wheel_first_run.sh
#                                         run them against an interpreter that already
#                                         has quorus-ai installed (CI: pipx's venv)
#
# Like scripts/stranger_nightly.sh, the firstrun tests are copied out of the
# checkout so nothing on sys.path can shadow the installed package; the script
# refuses to run if the interpreter's quorus comes from this checkout.
set -eu
wheel="${1:-}"
here="$(cd "$(dirname "$0")/.." && pwd)"
pybase="${PYTHON:-python3}"
work="$(mktemp -d "${TMPDIR:-/tmp}/quorus-wheel-run.XXXXXX")"
trap 'rm -rf "$work"' EXIT

if [ -n "${QUORUS_PYTHON:-}" ]; then
  py="$QUORUS_PYTHON"
  "$py" -c "import pytest" 2>/dev/null || \
    "$py" -m pip install --quiet --no-cache-dir --disable-pip-version-check pytest
else
  if [ -z "$wheel" ]; then
    mkdir -p "$work/dist"
    if command -v uv >/dev/null 2>&1; then
      uv build --wheel --quiet --out-dir "$work/dist" "$here"
    else
      "$pybase" -m pip wheel --quiet --no-deps --no-cache-dir -w "$work/dist" "$here"
    fi
    wheel="$(ls "$work"/dist/quorus_ai-*.whl)"
  fi
  "$pybase" -m venv "$work/venv"
  py="$work/venv/bin/python"
  "$py" -m pip install --quiet --no-cache-dir --disable-pip-version-check "$wheel" pytest
fi

suite="$work/suite/tests/firstrun"
mkdir -p "$suite"
: > "$work/suite/tests/__init__.py"
for f in __init__.py conftest.py pty_session.py stub_agents.py test_f5_fix_lands.py \
         test_gate_first_run.py; do
  cp "$here/tests/firstrun/$f" "$suite/$f"
done
cd "$work/suite"

"$py" - "$here" <<'PY'
import sys
from importlib.metadata import version
from pathlib import Path
import quorus
import quorus_cli
checkout = Path(sys.argv[1]).resolve()
for mod in (quorus, quorus_cli):  # the installed package under test, never a checkout
    where = Path(mod.__file__).resolve()
    assert checkout not in where.parents, f"{mod.__name__} is the checkout: {where}"
print(f"first run against quorus-ai {version('quorus-ai')} at {Path(sys.executable).parent}")
PY

# QUORUS_UNDER_TEST_VERSION stays unset: the package is this checkout, so every episode
# runs whatever the version string says (tests/firstrun/conftest.py).
unset QUORUS_UNDER_TEST_VERSION
"$py" -m pytest -q -p no:cacheprovider -o faulthandler_timeout=600 \
  -W ignore::pytest.PytestUnknownMarkWarning tests/firstrun/test_gate_first_run.py
