"""Pasted text reaches the composer whole, through a real terminal.

New-user pass 2 on 0.5.2, F3: pasting "@newbie-opencode what does calc.py do?"
sent nothing. `sys.stdin.read(1)` pulled the whole paste into Python's buffer,
so select() on the fd saw nothing pending and the rest of the burst sat there
until the next keypress; the mention picker held the '@'. These tests paste a
burst into `_read_input` over a pty, the way a terminal delivers it.

F4: the @ picker labelled opencode agents "(human)".
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import time

import pytest

pty = pytest.importorskip("pty")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATHS = [_ROOT] + [os.path.join(_ROOT, "packages", p) for p in ("cli", "mcp", "sdk", "tui")]

# The composer, in a fresh process whose terminal is the pty (a fork of the
# multi-threaded pytest process could deadlock).
_CHILD = """
import json, sys
from quorus_tui import hub
from quorus_tui.autocomplete import AutocompletePopover

hub.RENDER_TICK_S = 0.2
pop = AutocompletePopover(
    mention_items_provider=lambda: [("@newbie-opencode", "(agent)")],
    slash_items_provider=lambda: [("/help", "show help"), ("/quit", "leave")])
for _ in range(50):
    line = hub._read_input("> ", popover=pop, on_popover_change=lambda b, p: None)
    if line != hub._KEY_TIMEOUT:
        break
open(sys.argv[1], "w").write(json.dumps([line]))
"""


def _readable(fd: int, timeout: float = 0.05) -> bool:
    """select() refuses fds >= 1024 (CI hit that once fds leaked elsewhere);
    the platform selector (epoll/kqueue) doesn't."""
    with selectors.DefaultSelector() as sel:
        sel.register(fd, selectors.EVENT_READ)
        return bool(sel.select(timeout))


def _paste(burst: "str | list[str]", tmp_path) -> list[str]:
    """Paste *burst* into `_read_input` over a pty and return what it submitted.
    A list is typed as separate bursts, 0.3 s apart (a lone Esc must not be read
    as the start of an escape sequence)."""
    out = tmp_path / "result.json"
    primary, secondary = pty.openpty()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(_PATHS), "PYTHONIOENCODING": "utf-8"}
    child = subprocess.Popen([sys.executable, "-c", _CHILD, str(out)], stdin=secondary,
                             stdout=secondary, stderr=secondary, env=env, close_fds=True)
    os.close(secondary)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            # The composer redraws its prompt once it has the tty in raw mode.
            if _readable(primary) and b">" in os.read(primary, 4096):
                break
        for i, chunk in enumerate([burst] if isinstance(burst, str) else burst):
            if i:
                time.sleep(0.3)
            os.write(primary, chunk.encode())
        while time.monotonic() < deadline and child.poll() is None:
            if _readable(primary):
                try:
                    os.read(primary, 4096)
                except OSError:
                    break
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        os.close(primary)
    assert out.exists(), "the composer never returned: the paste was stuck"
    return json.loads(out.read_text())


@pytest.mark.parametrize("burst, want", [
    ("@newbie-opencode what does calc.py do?\r", "@newbie-opencode what does calc.py do?"),
    ("hello there, what does calc.py do?\r", "hello there, what does calc.py do?"),
    ("héllo ✓ ünïcode\r", "héllo ✓ ünïcode"),
    ("first line\nsecond line\r", "first line\nsecond line"),
])
def test_a_pasted_burst_is_submitted_whole(burst, want, tmp_path):
    assert _paste(burst, tmp_path) == [want]


def test_picker_labels_opencode_agents_as_agents():
    from quorus_tui import chat_widgets, hub
    from quorus_tui.autocomplete import mention_items_from_room

    items = mention_items_from_room(["newbie", "newbie-opencode", "newbie-claude"],
                                    is_human=chat_widgets.is_human_sender)
    assert items == [("@newbie", "(human)"), ("@newbie-opencode", "(agent)"),
                     ("@newbie-claude", "(agent)")]
    assert hub._name_has_agent_keyword("newbie-opencode")


def test_esc_on_the_slash_menu_drops_the_half_typed_command(tmp_path):
    """New-user pass 3: Esc closed the / menu but left "/h", so typing /help next
    sent "/h/help", an unknown command."""
    # Enter on the open menu accepts "/help"; the second Enter sends it.
    got = _paste(["/h", "\x1b", "/help", "\r", "\r"], tmp_path)
    assert [line.strip() for line in got] == ["/help"]
