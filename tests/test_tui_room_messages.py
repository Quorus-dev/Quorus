"""tui-polish.md defect 7: inside a room, what you type is a message. The home
view's letter shortcuts and English phrases used to eat replies: "l" left the
room, "1" switched rooms, "make room for tests" created a room, and
"invite bob" printed a token carrying the relay secret."""
import io
from unittest.mock import patch

import pytest
from quorus_tui import hub
from rich.console import Console


def _state():
    state = hub.HubState()
    state.set_rooms([{"name": "general", "members": ["alice", "alice-codex"]},
                     {"name": "dev", "members": ["alice"]}])
    state.select_by_name("general")
    state.set_connected(True, "Connected")
    return state


def _type(lines, state=None):
    state = state or _state()
    sent, out = [], io.StringIO()
    with patch.object(hub, "_read_input", side_effect=[*lines, "quit"]), \
         patch.object(hub, "_full_clear"), \
         patch.object(hub, "_join_room"), patch.object(hub, "_load_history_into"), \
         patch.object(hub, "_send_message", side_effect=lambda *a: sent.append(a[-1]) or "m1"), \
         patch.object(hub, "_create_room", side_effect=AssertionError("created a room")), \
         patch.object(hub, "_destroy_room", side_effect=AssertionError("deleted a room")), \
         patch.object(hub, "_mint_join_token", side_effect=AssertionError("minted a token")):
        hub._main_input_loop(console=Console(file=out, width=100), state=state,
                             relay_url="http://relay.test", secret="s3cret-value",
                             agent_name="alice")
    return state, sent, out.getvalue()


@pytest.mark.parametrize("text", ["s", "i", "m", "l", "j", "r", "d", "n", "2",
                                  "join dev", "make room for tests", "create ci",
                                  "invite bob"])
def test_what_you_type_in_a_room_is_sent(text):
    state, sent, out = _type([text])
    assert sent == [text]
    assert state.get_selected_room()["name"] == "general"  # nothing switched or left
    assert "s3cret-value" not in out and "quorus_join_" not in out


def test_slash_commands_still_work_in_a_room():
    state, sent, _ = _type(["/join dev"])
    assert sent == [] and state.get_selected_room()["name"] == "dev"


def test_home_view_letters_still_work_outside_a_room():
    state = _state()
    with state._lock:
        state.selected_room_idx = -1
    state, sent, _ = _type(["2"], state)
    assert sent == [] and state.get_selected_room()["name"] == "dev"
