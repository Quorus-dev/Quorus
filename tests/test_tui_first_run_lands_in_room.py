"""New-user pass 1, F1: the first run must end inside a room.

After the wizard a new user landed on the hub home with no room selected: what
they typed went nowhere ("No room selected" in the status bar), @-complete said
"(no matches)", and Enter did nothing, while the wizard's last line told them to
say something in #general.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from quorus_tui import hub
from rich.console import Console


@pytest.fixture(autouse=True)
def _config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))


def _session(rooms, *, announce, room=None):
    profile = {"relay_url": "https://work.test", "instance_name": "alice",
               "relay_secret": "test-secret"}
    if room:
        profile["room"] = room
    state = hub.HubState()
    state.request_workspace_switch("personal")  # leave on the first tick
    with patch("quorus_tui.hub._get_auth_token", return_value="jwt"), \
         patch("quorus_tui.hub._fetch_rooms", return_value=list(rooms)), \
         patch("quorus_tui.hub._away_rows", return_value=[]), \
         patch("quorus_tui.hub._poll_loop"), patch("quorus_tui.hub._sse_loop"), \
         patch("quorus_tui.hub._join_room") as joined, \
         patch("quorus_tui.hub._load_history_into"), \
         patch("quorus_tui.hub._read_input", return_value=""):
        hub._run_session(profile, state=state, announce_ready=announce)
    return state, joined


ROOMS = [{"name": "general", "id": "r1"}, {"name": "build", "id": "r2"}]


def test_a_first_run_lands_inside_general() -> None:
    state, joined = _session(ROOMS, announce=True)
    assert state.get_selected_room()["name"] == "general"
    assert joined.call_args.args[2] == "general"


def test_a_first_run_from_an_invite_lands_in_the_invited_room() -> None:
    state, _ = _session(ROOMS, announce=True, room="build")
    assert state.get_selected_room()["name"] == "build"


def test_a_returning_user_still_picks_from_the_room_list() -> None:
    state, joined = _session(ROOMS, announce=False)
    assert state.get_selected_room() is None and not joined.called


def _loop(rooms, keys):
    """Drive the real input loop with *keys*; return (messages sent, console text)."""
    state = hub.HubState()
    state.set_rooms(list(rooms))
    sent, out = [], io.StringIO()
    console = Console(file=out, width=120, force_terminal=False)

    def send(relay, secret, room, sender, text):
        sent.append((room, text))
        return "m1"

    with patch("quorus_tui.hub._read_input", side_effect=list(keys) + ["quit"]), \
         patch("quorus_tui.hub._send_message", side_effect=send), \
         patch("quorus_tui.hub._join_room"), patch("quorus_tui.hub._load_history_into"), \
         patch("quorus_tui.hub._full_clear"):
        hub._main_input_loop(console=console, state=state, relay_url="https://work.test",
                             secret="jwt", agent_name="alice")
    return sent, out.getvalue(), state


def test_text_typed_on_the_home_view_reaches_the_only_room() -> None:
    sent, _, state = _loop([{"name": "general", "id": "r1"}], ["hello everyone"])
    assert sent == [("general", "hello everyone")]
    assert state.get_selected_room()["name"] == "general"


def test_with_several_rooms_it_says_which_key_opens_one() -> None:
    sent, text, _ = _loop(ROOMS, ["hello"])
    assert sent == []
    assert "Not sent" in text and "Enter opens the first room" in text


def test_enter_on_the_home_view_opens_a_room() -> None:
    sent, _, state = _loop(ROOMS, ["", "hi"])
    assert state.get_selected_room()["name"] == "general"
    assert sent == [("general", "hi")]
