"""TUI workspace switch lifecycle.

This test doesn't run the full interactive loop (that would require
a pty). It exercises the _run_session helper's early-return path when
a switch is requested via HubState.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from quorus.profiles import ProfileManager


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_hubstate_request_switch_sets_pending(tmp_config_dir: Path) -> None:
    from quorus_tui.hub import HubState
    s = HubState()
    assert s.pending_workspace_switch() is None
    s.request_workspace_switch("personal")
    assert s.pending_workspace_switch() == "personal"


def test_run_session_returns_switch_tuple_on_request(
    tmp_config_dir: Path,
) -> None:
    """When state.request_workspace_switch() is set before the loop
    hits its next tick, _run_session tears down threads and returns
    ("switch", slug)."""
    from quorus_tui.hub import HubState, _run_session

    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {
        "relay_url": "https://work.test",
        "instance_name": "aarya",
        "api_key": "sk_w",
    })

    profile = pm.get("work")
    state = HubState()
    # Pre-seed the switch request so the main loop exits on first tick.
    state.request_workspace_switch("personal")

    # Patch out network-y helpers so the session setup doesn't hit real URLs.
    with patch("quorus_tui.hub._get_auth_token", return_value="fake-jwt"), \
         patch("quorus_tui.hub._fetch_rooms", return_value=[]), \
         patch("quorus_tui.hub._create_room", return_value=("ok", {})), \
         patch("quorus_tui.hub._poll_loop"), \
         patch("quorus_tui.hub._sse_loop"), \
         patch("quorus_tui.hub._read_input",
               side_effect=lambda *a, **kw: ""):
        result = _run_session(profile, state=state)

    assert isinstance(result, tuple)
    assert result[0] == "switch"
    assert result[1] == "personal"


def test_run_session_returns_quit_on_quit_key(tmp_config_dir: Path) -> None:
    from quorus_tui.hub import _KEY_QUIT, HubState, _run_session

    pm = ProfileManager(config_dir=tmp_config_dir)
    pm.save("work", {"relay_url": "https://work.test", "api_key": "sk_w"})
    profile = pm.get("work")
    state = HubState()

    with patch("quorus_tui.hub._get_auth_token", return_value="fake-jwt"), \
         patch("quorus_tui.hub._fetch_rooms", return_value=[]), \
         patch("quorus_tui.hub._create_room", return_value=("ok", {})), \
         patch("quorus_tui.hub._poll_loop"), \
         patch("quorus_tui.hub._sse_loop"), \
         patch("quorus_tui.hub._read_input", return_value=_KEY_QUIT):
        result = _run_session(profile, state=state)

    assert result == "quit"
