"""Regression test for the macOS Python-3.14 / Spotlight UF_HIDDEN .pth bug.

Background: hatchling editable installs write a `.pth` file in site-packages.
On macOS, Spotlight (mdworker) repeatedly applies the UF_HIDDEN flag to that
file via the `com.apple.provenance` extended attribute. CPython 3.14's
site.py silently skips hidden .pth files (CPython gh-117983). Result: every
entry-point script (`quorus`, `quorus-relay`, `quorus-mcp`,
`quorus-analytics`) crashes with `ModuleNotFoundError: No module named
'quorus'` — which is exactly what failed at the April 23 2026 hackathon.

The fix lives in `scripts/patch_entry_points.sh`: it rewrites each
entry-point script to inject the 5 package source dirs into `sys.path`
BEFORE the first `import quorus*` line, so we never depend on .pth file
processing. setup.sh runs the patcher automatically.

This test asserts the patch is in place and prevents regression.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_BIN = REPO_ROOT / ".venv" / "bin"
ENTRY_POINTS = ("quorus", "quorus-relay", "quorus-mcp", "quorus-analytics")
MARKER = "# quorus-bootstrap-marker-v1"


def _has_venv() -> bool:
    return VENV_BIN.is_dir() and all((VENV_BIN / ep).exists() for ep in ENTRY_POINTS)


@pytest.mark.skipif(not _has_venv(), reason=".venv with entry-points not present")
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_entry_point_has_bootstrap_marker(entry_point: str) -> None:
    """Every entry-point script must contain the bootstrap marker.

    If this fails, run `bash scripts/patch_entry_points.sh` to re-patch.
    """
    f = VENV_BIN / entry_point
    content = f.read_text()
    assert MARKER in content, (
        f"{f} is missing the bootstrap prelude. "
        f"Run `bash scripts/patch_entry_points.sh` to fix. "
        f"This is what broke at the April 23 2026 hackathon."
    )


@pytest.mark.skipif(not _has_venv(), reason=".venv with entry-points not present")
def test_entry_point_bootstrap_inserts_package_paths() -> None:
    """The bootstrap prelude must list all 5 Quorus package dirs."""
    content = (VENV_BIN / "quorus-relay").read_text()
    expected = ("packages', 'sdk", "packages', 'cli", "packages', 'mcp", "packages', 'tui")
    for snippet in expected:
        assert snippet in content, f"bootstrap missing {snippet} in quorus-relay"


@pytest.mark.skipif(not _has_venv(), reason=".venv with entry-points not present")
def test_patcher_script_is_executable() -> None:
    """patch_entry_points.sh must exist and be executable."""
    patcher = REPO_ROOT / "scripts" / "patch_entry_points.sh"
    assert patcher.is_file(), f"missing {patcher}"
    assert patcher.stat().st_mode & 0o111, f"{patcher} is not executable"


@pytest.mark.skipif(not _has_venv() or sys.platform != "darwin", reason="macOS-only")
def test_patcher_is_idempotent(tmp_path: Path) -> None:
    """Running the patcher twice must not duplicate the prelude.

    Builds a fake repo layout with .venv/bin/quorus and invokes the patcher
    with the fake repo as its argument, matching the real call signature.
    """
    fake_repo = tmp_path / "fake_repo"
    fake_bin = fake_repo / ".venv" / "bin"
    fake_bin.mkdir(parents=True)
    fake_ep = fake_bin / "quorus"
    fake_ep.write_text(
        "#!/usr/bin/env python3.14\n"
        "from quorus_cli.cli import main\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    fake_ep.chmod(0o755)

    import subprocess
    patcher = REPO_ROOT / "scripts" / "patch_entry_points.sh"

    subprocess.run(["bash", str(patcher), str(fake_repo)], check=True, capture_output=True)
    once = fake_ep.read_text()
    subprocess.run(["bash", str(patcher), str(fake_repo)], check=True, capture_output=True)
    twice = fake_ep.read_text()

    assert MARKER in once, "patcher did not insert marker on first run"
    assert once == twice, "patcher is not idempotent"
    assert once.count(MARKER) == 1, "marker duplicated on second run"

    shutil.rmtree(fake_repo, ignore_errors=True)
