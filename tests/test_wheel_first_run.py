"""scripts/wheel_first_run.sh: the Show HN path on the installed package, in CI.

The gate episodes in tests/firstrun/test_gate_first_run.py run the worktree's
code. The script runs the same episodes against an installed quorus-ai (a
wheel built from the checkout, or the interpreter CI's pipx install made), and
the cold-install workflow runs it on every PR. These tests pin the wiring, so
the path can't quietly drop out of CI; the episodes themselves are the gate.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "wheel_first_run.sh"
NIGHTLY = ROOT / "scripts" / "stranger_nightly.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "cold-install.yml"


def test_the_script_is_executable_posix_sh() -> None:
    assert SCRIPT.stat().st_mode & 0o111, "not executable"
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no sh")
    subprocess.run([sh, "-n", str(SCRIPT)], check=True)
    assert SCRIPT.read_text().startswith("#!/bin/sh\n")


def test_the_script_runs_the_gate_episodes_on_the_installed_package() -> None:
    text = SCRIPT.read_text()
    # Both episodes, with the helpers they import, copied out of the checkout.
    for f in ("pty_session.py", "stub_agents.py", "test_f5_fix_lands.py",
              "test_gate_first_run.py"):
        assert f in text, f
    assert "tests/firstrun/test_gate_first_run.py" in text
    # Never the checkout: the interpreter's quorus must come from the install.
    assert "checkout not in where.parents" in text
    # Three ways in: build, a prebuilt wheel, or an interpreter that has it.
    assert "QUORUS_PYTHON" in text and 'wheel="${1:-}"' in text
    assert "pip wheel" in text and "uv build" in text


def test_cold_install_runs_it_on_every_pr_from_pipxs_venv() -> None:
    wf = WORKFLOW.read_text()
    step = re.search(r"- name: First run with the default gate \(Unix\)\n(.*?)\n\n", wf, re.S)
    assert step, "the cold-install workflow lost the first-run step"
    body = step.group(1)
    assert "if: runner.os != 'Windows'" in body  # a pty: not on Windows
    assert "pipx inject" in body and "pytest" in body
    assert "QUORUS_PYTHON=" in body and "PIPX_LOCAL_VENVS" in body
    assert "scripts/wheel_first_run.sh" in body
    assert "timeout-minutes:" in body


def test_the_nightly_against_pypi_runs_the_gate_episodes_too() -> None:
    if not NIGHTLY.exists():  # the nightly against PyPI lives on the development branch
        pytest.skip("no stranger_nightly.sh on this branch")
    text = NIGHTLY.read_text()
    assert "test_gate_first_run.py" in text
    # Episodes for fixes newer than the PyPI release are skipped there, not failed:
    # the nightly copies the gating conftest and tells it the installed version.
    assert "conftest.py" in text
    assert 'QUORUS_UNDER_TEST_VERSION="$(' in text and "export QUORUS_UNDER_TEST_VERSION" in text
    assert "import version; print(version(\"quorus-ai\"))" in text


def test_the_wheel_run_never_skips_by_version() -> None:
    """The checkout's wheel carries every fix whatever its version string says."""
    text = SCRIPT.read_text()
    assert "conftest.py" in text and "unset QUORUS_UNDER_TEST_VERSION" in text


@pytest.mark.parametrize("marker, installed, skipped", [
    ("0.7.12", "0.7.11", True),    # the nightly on PyPI before the fix ships
    ("0.7.12", "0.7.12", False),   # shipped
    ("0.7.12", "0.8.0", False),
    ("0.7.12", None, False),       # the worktree suite, or the checkout's wheel
    ("0.7.12", "", False),
    ("0.7.12", "not-a-version", False),
])
def test_since_skips_only_older_installed_releases(marker, installed, skipped) -> None:
    from tests.firstrun.conftest import skip_reason

    why = skip_reason(marker, installed)
    assert bool(why) is skipped, why
    if skipped:
        assert "0.7.12" in why and installed in why
