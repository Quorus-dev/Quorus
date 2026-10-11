"""Regression test: cold-install smoke must NEVER touch real host configs.

Background — what this guards against
-------------------------------------

Before this test existed, ``scripts/cold_install_smoke.py`` ran ``quorus init``
against a freshly spawned relay. ``quorus init`` calls
``quorus_cli.mcp_writers.register_all`` which writes to:

  ~/.gemini/settings.json
  ~/.claude/settings.json
  ~/.claude.json
  ~/.cursor/mcp.json
  ~/.codex/config.toml
  ~/.codeium/windsurf/mcp_config.json
  ~/.continue/config.json
  ~/.opencode.json
  ~/.config/opencode/opencode.json
  ~/.cline/mcp.json

Every one of those paths is built with ``Path.home() / ...``, which honours
``$HOME``. The smoke isolated ``QUORUS_CONFIG_DIR`` (the Quorus-only config
dir) but NOT ``$HOME``, so the writers happily clobbered the user's real
production config every time the smoke ran. Concretely: a developer's
``~/.gemini/settings.json`` would end up with
``QUORUS_RELAY_URL=http://127.0.0.1:18080`` and
``QUORUS_INSTANCE_NAME=smoke-alice-gemini`` instead of the production relay.

Fix — what this test asserts
----------------------------

The smoke now redirects ``HOME`` (and ``USERPROFILE`` for Windows parity) to
a scratch dir before spawning any subprocess. This test:

  1. Snapshots every real host path (read-only; it never writes to $HOME).
  2. Runs the smoke in a subprocess (or skips if the binaries aren't
     available — same skip rule as ``test_cold_install.py``).
  3. Asserts every path is byte-for-byte unchanged and no new file appeared.

If anyone re-introduces the bug — by adding a new mcp writer that
hard-codes ``Path.home()`` and forgetting to honour the smoke's HOME
override — this test fails before the regression hits real users.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_PY = REPO_ROOT / "scripts" / "cold_install_smoke.py"

# Every host path the mcp writers reach for via ``Path.home() / ...``.
# Keep in sync with ``HOST_CONFIG_PATHS`` in ``cold_install_smoke.py`` and
# the writer registry in ``packages/cli/quorus_cli/mcp_writers.py``.
HOST_CONFIG_RELS: tuple[str, ...] = (
    ".claude/settings.json",
    ".claude.json",
    ".cursor/mcp.json",
    ".codeium/windsurf/mcp_config.json",
    ".gemini/settings.json",
    ".config/opencode/opencode.json",
    ".opencode.json",
    ".continue/config.json",
    ".cline/mcp.json",
    ".codex/config.toml",
)


def _free_port() -> int:
    """Pick an unused localhost port — same trick as test_cold_install.py."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _has_quorus_binaries() -> bool:
    return (
        shutil.which("quorus") is not None
        and shutil.which("quorus-relay") is not None
    )


@pytest.mark.skipif(
    not _has_quorus_binaries(),
    reason="quorus / quorus-relay not on PATH — install with `pipx install -e .`",
)
def test_cold_install_smoke_does_not_touch_host_configs(tmp_path: Path) -> None:
    """Run the cold smoke and assert no real host config drifted.

    This is the gate that would have caught the bug where the smoke
    silently overwrote the user's ``~/.gemini/settings.json``. If any host
    path changes by even one byte, or appears, the test fails.
    """
    assert SMOKE_PY.exists(), f"smoke script missing at {SMOKE_PY}"

    home = Path.home()

    # Snapshot only: never write into the real $HOME. An earlier version
    # planted canary files there and a killed run left them behind, breaking
    # the user's opencode/Cursor/Cline configs (2026-10-11).
    before: dict[Path, bytes | None] = {
        home / rel: ((home / rel).read_bytes() if (home / rel).exists() else None)
        for rel in HOST_CONFIG_RELS
    }

    port = _free_port()
    result = subprocess.run(
        [sys.executable, str(SMOKE_PY), "--port", str(port), "--timeout", "60"],
        capture_output=True,
        timeout=120,
        check=False,
    )

    # Isolation first: if both fail, the clobbered config is the bigger problem.
    drift: list[str] = []
    for p, original in before.items():
        actual = p.read_bytes() if p.exists() else None
        if actual != original:
            drift.append(f"{p}\n  before[:200]={(original or b'')[:200]!r}\n"
                         f"  after[:200]={(actual or b'')[:200]!r}")
    assert not drift, (
        "cold-install smoke clobbered host config paths — "
        "HOME isolation broken in scripts/cold_install_smoke.py:\n"
        + "\n".join(drift)
        + f"\n\nsmoke stderr:\n{result.stderr.decode(errors='replace')[-2000:]}"
    )
    if result.returncode != 0:
        pytest.fail(
            "smoke isolated correctly but exited non-zero — "
            f"returncode={result.returncode}\n"
            f"stdout:\n{result.stdout.decode(errors='replace')}\n"
            f"stderr:\n{result.stderr.decode(errors='replace')}"
        )


def test_cold_install_smoke_has_home_override() -> None:
    """Static guard: the smoke source MUST reference HOME isolation.

    A cheap defence against someone deleting the env override during a
    refactor. If this string ever disappears from the smoke, fail loudly
    so the contributor remembers WHY it's there before removing it.
    """
    src = SMOKE_PY.read_text()
    assert 'env["HOME"]' in src, (
        "scripts/cold_install_smoke.py no longer overrides HOME — "
        "the mcp writers will clobber the user's real ~/.gemini etc. "
        "Restore the HOME isolation block before merging."
    )
    assert "HOST_CONFIG_PATHS" in src, (
        "scripts/cold_install_smoke.py no longer snapshots host configs — "
        "restore the atexit safety net."
    )


@pytest.mark.skipif(
    os.environ.get("RUN_CLAUDE_CANARY") != "1",
    reason="Optional canary check — set RUN_CLAUDE_CANARY=1 to enable. "
           "Avoids interfering with the user's live ~/.claude setup in CI.",
)
def test_no_new_canary_at_claude_mcp() -> None:
    """Optional: verify the smoke does not create ~/.claude/mcp.json.test-canary.

    This path was specifically called out in the bug report. It's a
    canary for "any new write under ~/.claude that wasn't there before",
    not the full isolation assertion.
    """
    canary = Path.home() / ".claude" / "mcp.json.test-canary"
    assert not canary.exists(), (
        f"unexpected canary at {canary} — something is writing to ~/.claude "
        "outside the smoke's HOME-isolated scratch dir"
    )
