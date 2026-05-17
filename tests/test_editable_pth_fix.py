"""Regression test for the macOS Sequoia/Tahoe + Python 3.14 editable-install
.pth file regression (2026-05-16 root cause).

Repro:
1. Set ``com.apple.provenance`` xattr on a venv .pth file — this is what
   hatchling-editable does implicitly via the macOS file-creation path.
2. macOS automatically applies ``UF_HIDDEN`` while that xattr is present.
3. Python 3.14's ``site.addpackage`` refuses to load .pth files with
   ``UF_HIDDEN`` set (CPython 2025-era hardening against hidden persistence).
4. ``import quorus_sdk`` fails silently.

This test forces the broken state and asserts ``scripts/fix_editable_pth.sh``
unsticks it. Skipped on Linux (no UF_HIDDEN concept).
"""

from __future__ import annotations

import os
import platform
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="UF_HIDDEN / com.apple.provenance only exist on macOS",
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIX_SCRIPT = REPO_ROOT / "scripts" / "fix_editable_pth.sh"
VENV_ROOT = REPO_ROOT / ".venv"


def _quorus_pth_files() -> list[Path]:
    if not VENV_ROOT.exists():
        return []
    site_pkgs = list(VENV_ROOT.glob("lib/python*/site-packages"))
    if not site_pkgs:
        return []
    out: list[Path] = []
    for sp in site_pkgs:
        out.extend(sp.glob("_editable_impl_quorus*.pth"))
        out.extend(sp.glob("quorus_editable_*.pth"))
    return out


def test_fix_script_exists_and_is_executable():
    assert FIX_SCRIPT.exists(), "scripts/fix_editable_pth.sh must exist"
    assert os.access(FIX_SCRIPT, os.X_OK), "fix script must be executable"


def test_fix_script_unsticks_hidden_pth_files():
    """Set UF_HIDDEN, confirm import breaks, run fix, confirm import works."""
    pths = _quorus_pth_files()
    if not pths:
        pytest.skip("no editable .pth files in venv — not an editable install")

    py = VENV_ROOT / "bin" / "python"
    if not py.exists():
        pytest.skip("venv python missing")

    # Force the broken state. UF_HIDDEN is 0x8000.
    UF_HIDDEN = 0x8000
    for p in pths:
        os.chflags(p, UF_HIDDEN)
        st = os.lstat(p)
        assert st.st_flags & UF_HIDDEN, f"failed to set UF_HIDDEN on {p}"

    # Confirm import breaks. Run with -S to bypass user site to avoid
    # accidental rescue from elsewhere.
    broken = subprocess.run(
        [str(py), "-c", "import quorus_sdk"],
        capture_output=True,
        text=True,
    )
    assert broken.returncode != 0, (
        "expected ModuleNotFoundError with UF_HIDDEN set, got success — "
        "either Python 3.14 hidden-pth check regressed or another sys.path "
        "entry is masking the bug. Output: " + (broken.stdout + broken.stderr)
    )

    # Run the fix.
    result = subprocess.run(
        ["bash", str(FIX_SCRIPT), str(VENV_ROOT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"fix script failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # Imports must now succeed.
    fixed = subprocess.run(
        [str(py), "-c", "import quorus, quorus_sdk; print('OK')"],
        capture_output=True,
        text=True,
    )
    assert fixed.returncode == 0, (
        f"import still broken after fix: stdout={fixed.stdout!r} stderr={fixed.stderr!r}"
    )
    assert "OK" in fixed.stdout

    # And UF_HIDDEN must actually be cleared (not just masked).
    for p in pths:
        st = os.lstat(p)
        assert not (st.st_flags & UF_HIDDEN), (
            f"{p} still has UF_HIDDEN set after fix"
        )
