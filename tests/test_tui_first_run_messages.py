"""First-run message order + hygiene (weekend brief, findings #3 and #4).

The fresh-user run found two confusing lines in the first-launch wizard:

- #3 the invite prompt rendered an empty default as a bare ``()``
  (``Invite or Enter ():``), because rich shows ``default=""`` unless
  ``show_default=False``;
- #4 ``✓ You're in as @alice.`` printed *before* ``Connecting to …``, so the
  happy path read like a race.

These tests pin both fixes: a source guard for the prompt, and a real
``_run_session`` run that asserts the announce line now lands after the
connect line (and only on a first run).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

ROOMS = [{"name": "general", "id": "r1"}]


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _run(monkeypatch, announce: bool):
    """Run `_run_session` far enough to reach the announce line, then exit."""
    from quorus_tui.hub import HubState, _run_session

    profile = {"relay_url": "https://work.test", "instance_name": "alice",
               "relay_secret": "test-secret"}
    state = HubState()
    state.request_workspace_switch("personal")  # exit on the first tick
    with patch("quorus_tui.hub._get_auth_token", return_value="fake-jwt"), \
         patch("quorus_tui.hub._fetch_rooms", return_value=list(ROOMS)), \
         patch("quorus_tui.hub._away_rows", return_value=[]), \
         patch("quorus_tui.hub._poll_loop"), \
         patch("quorus_tui.hub._sse_loop"), \
         patch("quorus_tui.hub._read_input", return_value=""):
        _run_session(profile, state=state, announce_ready=announce)


def test_invite_prompt_hides_the_empty_default() -> None:
    """rich renders `default=""` as `()`, which reads like a missing value."""
    from quorus_tui.hub import _first_launch_setup

    src = inspect.getsource(_first_launch_setup)
    assert "Invite" in src
    assert "show_default=False" in src


def test_first_launch_setup_does_not_claim_ready_before_connecting() -> None:
    """`You're in as @alice` must come from the session, after the relay
    answered — not from the wizard, which runs before it connects."""
    from quorus_tui.hub import _first_launch_setup

    src = inspect.getsource(_first_launch_setup)
    assert not any(
        "You're in as" in line for line in src.splitlines()
        if "console.print" in line
    )


def test_first_run_announces_ready_after_connecting(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _run(monkeypatch, announce=True)
    out = capsys.readouterr().out
    assert "You're in as" in out
    assert "Connecting to" in out
    assert out.index("Connecting to") < out.index("You're in as")


def test_a_returning_user_gets_no_first_run_banner(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _run(monkeypatch, announce=False)
    assert "You're in as" not in capsys.readouterr().out
