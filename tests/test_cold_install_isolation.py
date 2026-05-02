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

  1. Drops sentinel files at the real host paths with random known content.
  2. Runs the smoke in a subprocess (or skips if the binaries aren't
     available — same skip rule as ``test_cold_install.py``).
  3. Asserts the sentinels are byte-for-byte unchanged.
  4. Asserts no new files appeared at any host path that didn't have one
     before the test ran.

If anyone re-introduces the bug — by adding a new mcp writer that
hard-codes ``Path.home()`` and forgetting to honour the smoke's HOME
override — this test fails before the regression hits real users.
"""
from __future__ import annotations

import os
import secrets
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
    silently overwrote the user's ``~/.gemini/settings.json``. We seed
    each host path with a sentinel containing random bytes; if any path
    we seeded changes by even one byte, the test fails.
    """
    assert SMOKE_PY.exists(), f"smoke script missing at {SMOKE_PY}"

    home = Path.home()

    # ── Step 1: snapshot the current state of every host config path.
    #
    # Two states matter:
    #   - existed_before[path] -> Optional[bytes]: original content (if any).
    #   - sentinel_canary[path] -> bytes: a unique marker we'll plant.
    # We restore originals at the end whether or not the test passes.
    existed_before: dict[Path, bytes | None] = {}
    sentinels: dict[Path, bytes] = {}
    for rel in HOST_CONFIG_RELS:
        p = home / rel
        existed_before[p] = p.read_bytes() if p.exists() else None
        # Distinct token per path so we can prove no swapping happened.
        sentinels[p] = (
            f"# cold-install-isolation-canary {rel} "
            f"{secrets.token_hex(16)}\n"
        ).encode("utf-8")

    # ── Step 2: plant sentinels at every host path.
    # We only plant sentinels at paths that DID NOT pre-exist, to avoid
    # corrupting the user's real settings. For paths that DID exist, the
    # snapshot above already captured the canary state — any byte drift
    # is a regression.
    seeded_paths: list[Path] = []
    try:
        for path, original in existed_before.items():
            if original is None:
                # Path didn't exist before — plant a sentinel so we can
                # detect "smoke created a fresh file at a host path".
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(sentinels[path])
                seeded_paths.append(path)

        # Capture post-seed state — this is the "expected" baseline that
        # must hold across the smoke run.
        expected_after: dict[Path, bytes] = {
            p: (p.read_bytes() if p.exists() else b"")
            for p in existed_before
        }

        # ── Step 3: run the smoke in a subprocess.
        port = _free_port()
        result = subprocess.run(
            [
                sys.executable, str(SMOKE_PY),
                "--port", str(port),
                "--timeout", "60",
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )

        # ── Step 4: assert no host config drifted.
        # We do this BEFORE asserting smoke success, so if both fail we
        # surface the more important regression first.
        drift: list[str] = []
        for p, expected in expected_after.items():
            actual = p.read_bytes() if p.exists() else b""
            if actual != expected:
                # Truncate large diffs — we only need enough to identify
                # the offender.
                drift.append(
                    f"{p}\n"
                    f"  expected[:200]={expected[:200]!r}\n"
                    f"  actual[:200]={actual[:200]!r}"
                )

        assert not drift, (
            "cold-install smoke clobbered host config paths — "
            "HOME isolation broken in scripts/cold_install_smoke.py:\n"
            + "\n".join(drift)
            + f"\n\nsmoke stdout:\n{result.stdout.decode(errors='replace')[-2000:]}"
            + f"\n\nsmoke stderr:\n{result.stderr.decode(errors='replace')[-2000:]}"
        )

        # The smoke itself should also pass — but the isolation assertion
        # is the load-bearing one for this test file. Surface smoke failures
        # only if isolation held.
        if result.returncode != 0:
            pytest.fail(
                "smoke isolated correctly but exited non-zero — "
                f"returncode={result.returncode}\n"
                f"stdout:\n{result.stdout.decode(errors='replace')}\n"
                f"stderr:\n{result.stderr.decode(errors='replace')}"
            )

    finally:
        # ── Cleanup: restore every path to its pre-test state.
        for path, original in existed_before.items():
            try:
                if original is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original)
            except OSError:
                # Best effort — never let cleanup mask a real assertion.
                pass


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
