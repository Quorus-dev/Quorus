"""Tests for the new slash-command surface (packages/tui/quorus_tui/slash.py)."""

from __future__ import annotations

import io

from rich.console import Console

from quorus_tui import chat as _chat
from quorus_tui import slash as _slash
from quorus.tui_hub import HubState


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf, width=100, force_terminal=False,
        no_color=True, legacy_windows=False,
    )
    return console, buf


# ── run_share_flow ───────────────────────────────────────────────────────────


def test_run_share_flow_renders_card_and_returns_dismiss_status(monkeypatch):
    console, buf = _console()
    # Stub clipboard so the test never touches the real one.
    monkeypatch.setattr(_chat, "copy_to_clipboard", lambda _t: True)
    status = _slash.run_share_flow(
        console=console,
        room_name="general",
        code="MJN2-EWVT",
        install_url="curl -sSL https://q.dev/r/MJN2-EWVT.sh | sh",
        read_one_key=lambda: "x",   # any non-c key dismisses
    )
    out = buf.getvalue()
    assert "Share #general" in out
    assert "MJN2-EWVT" in out
    assert "closed" in status.lower()


def test_run_share_flow_copy_path(monkeypatch):
    console, _ = _console()
    captured: dict = {}
    monkeypatch.setattr(_chat, "copy_to_clipboard", lambda t: captured.setdefault("copy", t) or True)
    status = _slash.run_share_flow(
        console=console,
        room_name="dev",
        code="ABCD-EFGH",
        read_one_key=lambda: "c",
    )
    assert captured["copy"] == "ABCD-EFGH"
    assert "copied" in status.lower()


def test_run_share_flow_copy_failure_shows_code(monkeypatch):
    console, _ = _console()
    monkeypatch.setattr(_chat, "copy_to_clipboard", lambda _t: False)
    status = _slash.run_share_flow(
        console=console,
        room_name="dev",
        code="ABCD-EFGH",
        read_one_key=lambda: "c",
    )
    assert "ABCD-EFGH" in status


# ── /leave ───────────────────────────────────────────────────────────────────


def test_slash_leave_returns_to_welcome():
    state = HubState()
    state.set_rooms([{"name": "dev"}, {"name": "ops"}])
    state.select_by_name("dev")
    assert state.selected_room_name() == "dev"

    console, _ = _console()
    _slash.slash_leave("", state, "http://relay", "secret", "arav", console)
    assert state.selected_room_name() == ""
    assert "Left #dev" in state.get_status_bar()


def test_slash_leave_no_room_selected_is_a_noop():
    state = HubState()
    console, _ = _console()
    # Should not raise, status bar stays empty.
    _slash.slash_leave("", state, "http://relay", "secret", "arav", console)
    assert state.selected_room_name() == ""


# ── /info ────────────────────────────────────────────────────────────────────


def test_slash_info_prints_member_roster():
    state = HubState()
    state.set_rooms([{"name": "dev", "members": ["arav", "ada", "bob"]}])
    state.select_by_name("dev")
    console, buf = _console()
    _slash.slash_info("", state, "http://relay", "secret", "arav", console)
    out = buf.getvalue()
    assert "#dev" in out
    assert "@arav" in out
    assert "@ada" in out
    assert "@bob" in out
    assert "3 total" in out


def test_slash_info_handles_no_room():
    state = HubState()
    console, _ = _console()
    _slash.slash_info("", state, "http://relay", "secret", "arav", console)
    assert "No room selected" in state.get_status_bar()


# ── /me ──────────────────────────────────────────────────────────────────────


def test_slash_me_requires_action_text():
    state = HubState()
    state.set_rooms([{"name": "dev"}])
    state.select_by_name("dev")
    console, _ = _console()
    _slash.slash_me("", state, "http://relay", "secret", "arav", console)
    assert "usage: /me" in state.get_status_bar()


def test_slash_me_requires_room():
    state = HubState()
    console, _ = _console()
    _slash.slash_me("is debugging", state, "http://relay", "secret", "arav", console)
    assert "No room selected" in state.get_status_bar()


# ── /mute ────────────────────────────────────────────────────────────────────


def test_slash_mute_renders_coming_soon_badge():
    state = HubState()
    state.set_rooms([{"name": "dev"}])
    state.select_by_name("dev")
    console, _ = _console()
    _slash.slash_mute("2h", state, "http://relay", "secret", "arav", console)
    bar = state.get_status_bar()
    assert "muted" in bar.lower()
    assert "2h" in bar
    assert "coming soon" in bar.lower()


def test_slash_mute_default_duration():
    state = HubState()
    state.set_rooms([{"name": "dev"}])
    state.select_by_name("dev")
    console, _ = _console()
    _slash.slash_mute("", state, "http://relay", "secret", "arav", console)
    assert "1h" in state.get_status_bar()
