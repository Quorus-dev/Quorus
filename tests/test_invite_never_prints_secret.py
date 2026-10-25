"""0.7.5 security fix: typing `invite <name>` in a room printed a quorus_join_ token
that base64-encoded the relay secret, and told the user to hand it to someone.
No typed input may print the secret or a secret-bearing token."""
import base64
import io
from unittest.mock import patch

import pytest
from quorus_tui import hub
from rich.console import Console

SECRET = "relay-s3cret-value-123"
ENCODED = base64.urlsafe_b64encode(SECRET.encode()).decode().rstrip("=")


def _type(lines, sent=None):
    state = hub.HubState()
    state.set_rooms([{"name": "general", "members": ["alice", "alice-codex"]}])
    state.select_by_name("general")
    state.set_connected(True, "Connected")
    out = io.StringIO()
    with patch.object(hub, "_read_input", side_effect=[*lines, "quit"]), \
         patch.object(hub, "_full_clear"), \
         patch.object(hub, "_join_room"), patch.object(hub, "_load_history_into"), \
         patch.object(hub, "_send_message",
                      side_effect=lambda *a: (sent if sent is not None else []).append(a[-1])
                      or "m1"), \
         patch.object(hub, "_mint_join_token", return_value="ABCD-EFGH"), \
         patch("quorus_tui.slash.run_share_flow", return_value="Share card closed"), \
         patch("builtins.input", return_value=""):
        hub._main_input_loop(console=Console(file=out, width=100), state=state,
                             relay_url="http://relay.test", secret=SECRET,
                             agent_name="alice")
    return state, out.getvalue()


@pytest.mark.parametrize("text", ["invite bob", "invite alice-codex", "invite", "/invite",
                                  "/share", "s", "i", "m", "help", "?", "hello"])
def test_no_typed_input_prints_the_secret_or_a_join_token(text):
    _, out = _type([text])
    assert SECRET not in out and ENCODED not in out
    assert "quorus_join_" not in out


def test_invite_in_a_room_is_just_a_message():
    """0.7.5 points `invite <name>` to /share; on this line typed room text is a
    message (tui-polish.md defect 7), so it is sent and prints nothing more."""
    sent = []
    _, out = _type(["invite bob"], sent)
    assert sent == ["invite bob"] and "quorus_join_" not in out
