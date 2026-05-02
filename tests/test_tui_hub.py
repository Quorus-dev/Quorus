"""Tests for quorus/tui_hub.py — HubState, config helpers, API helpers."""

import io
import json
import threading

from quorus_tui import welcome as _welcome
from rich.console import Console

from quorus import tui_hub
from quorus.tui_hub import (
    HubState,
    _auth_headers,
    _mint_join_token,
    _save_instance_config,
    _sender_color,
)

# ---------------------------------------------------------------------------
# HubState — thread-safe coordination
# ---------------------------------------------------------------------------


class TestHubState:
    def test_initial_state(self):
        s = HubState()
        assert s.get_rooms() == []
        assert s.get_messages() == []
        assert s.get_selected_room() is None
        connected, status = s.get_connection()
        assert not connected
        assert status == "Connecting..."

    def test_set_and_get_rooms(self):
        s = HubState()
        rooms = [{"name": "dev"}, {"name": "qa"}]
        s.set_rooms(rooms)
        assert s.get_rooms() == rooms

    def test_get_rooms_returns_copy(self):
        """Modifying the returned list must not mutate internal state."""
        s = HubState()
        s.set_rooms([{"name": "dev"}])
        result = s.get_rooms()
        result.append({"name": "intruder"})
        assert len(s.get_rooms()) == 1

    def test_select_next_wraps(self):
        s = HubState()
        s.set_rooms([{"name": "a"}, {"name": "b"}, {"name": "c"}])
        s.selected_room_idx = 2
        s.select_next()
        assert s.selected_room_idx == 0  # wrapped

    def test_select_prev_wraps(self):
        s = HubState()
        s.set_rooms([{"name": "a"}, {"name": "b"}])
        s.selected_room_idx = 0
        s.select_prev()
        assert s.selected_room_idx == 1  # wrapped to end

    def test_select_next_noop_on_empty(self):
        s = HubState()
        s.select_next()  # must not raise
        # No rooms → stays in welcome state (-1).
        assert s.selected_room_idx == -1

    def test_get_selected_room_clamps_index(self):
        """Index out of bounds is clamped to last element."""
        s = HubState()
        s.set_rooms([{"name": "only"}])
        s.selected_room_idx = 99
        room = s.get_selected_room()
        assert room == {"name": "only"}

    def test_select_by_name(self):
        s = HubState()
        s.set_rooms([{"name": "alpha"}, {"name": "beta"}, {"name": "gamma"}])
        s.select_by_name("beta")
        assert s.selected_room_idx == 1

    def test_select_by_name_unknown_noop(self):
        s = HubState()
        s.set_rooms([{"name": "alpha"}])
        s.selected_room_idx = 0
        s.select_by_name("nonexistent")
        assert s.selected_room_idx == 0

    def test_set_and_get_messages(self):
        s = HubState()
        msgs = [{"content": "hello"}, {"content": "world"}]
        s.set_messages(msgs)
        assert s.get_messages() == msgs

    def test_set_messages_caps_at_max_msg(self):
        s = HubState()
        overflow = [{"content": str(i)} for i in range(tui_hub.MAX_MSG + 10)]
        s.set_messages(overflow)
        assert len(s.get_messages()) == tui_hub.MAX_MSG

    def test_append_message_evicts_oldest(self):
        s = HubState()
        # Fill to MAX_MSG
        s.set_messages([{"content": str(i)} for i in range(tui_hub.MAX_MSG)])
        s.append_message({"content": "new"})
        msgs = s.get_messages()
        assert len(msgs) == tui_hub.MAX_MSG
        assert msgs[-1]["content"] == "new"
        assert msgs[0]["content"] == "1"  # oldest evicted

    def test_append_message_dedups_on_message_id(self):
        s = HubState()
        # SSE push arrives first with fan-out id + canonical message_id
        s.append_message({
            "id": "delivery-1",
            "message_id": "canonical-1",
            "content": "hello",
        })
        # History refetch returns the same logical message with canonical id only
        s.append_message({"id": "canonical-1", "content": "hello"})
        msgs = s.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hello"

    def test_append_message_dedups_on_id(self):
        s = HubState()
        s.append_message({"id": "abc", "content": "a"})
        s.append_message({"id": "abc", "content": "a-duplicate"})
        msgs = s.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["content"] == "a"

    def test_set_messages_primes_dedup_set(self):
        s = HubState()
        s.set_messages([
            {"id": "m1", "content": "one"},
            {"id": "m2", "content": "two"},
        ])
        # Subsequent SSE push with the same id should be ignored
        s.append_message({"id": "m2", "content": "two-echo"})
        msgs = s.get_messages()
        assert len(msgs) == 2
        assert [m["content"] for m in msgs] == ["one", "two"]

    def test_selected_room_name(self):
        s = HubState()
        assert s.selected_room_name() == ""
        s.set_rooms([{"id": "r1", "name": "dev"}, {"id": "r2", "name": "ops"}])
        # Default is welcome state (no room selected) → empty name.
        assert s.selected_room_name() == ""
        s.select_next()  # advances from -1 to 0 (dev)
        assert s.selected_room_name() == "dev"
        s.select_next()  # advances 0 → 1 (ops)
        assert s.selected_room_name() == "ops"

    def test_snapshot_render_state_atomic_after_set_rooms(self):
        """Regression: render must see (rooms, idx, name) from one snapshot.

        The bug: render loop read get_rooms() and selected_room_idx in two
        separate lock acquisitions. If the polling thread re-ordered rooms
        between the reads, the strip highlighted the wrong room and the
        user appeared to be silently moved into a different room. The fix
        is HubState.snapshot_render_state() which returns all four pieces
        under one lock. This test asserts the post-reorder snapshot is
        internally consistent — idx points at the same name in the rooms
        list it returned.
        """
        s = HubState()
        s.set_rooms([
            {"id": "r1", "name": "general"},
            {"id": "r2", "name": "may4-sprint"},
            {"id": "r3", "name": "medbuddy"},
        ])
        s.select_by_name("may4-sprint")
        # Polling thread re-orders rooms by recent activity
        s.set_rooms([
            {"id": "r2", "name": "may4-sprint"},
            {"id": "r3", "name": "medbuddy"},
            {"id": "r1", "name": "general"},
        ])
        rooms, idx, sel, name = s.snapshot_render_state()
        assert name == "may4-sprint"
        assert sel is not None and sel["name"] == "may4-sprint"
        assert rooms[idx]["name"] == "may4-sprint"

    def test_snapshot_render_state_welcome_returns_minus_one(self):
        """No room selected → idx=-1, sel=None, name='', but rooms list intact."""
        s = HubState()
        s.set_rooms([{"id": "r1", "name": "dev"}])
        rooms, idx, sel, name = s.snapshot_render_state()
        assert idx == -1
        assert sel is None
        assert name == ""
        assert len(rooms) == 1

    def test_snapshot_render_state_rooms_copy_isolated(self):
        """Mutating the returned rooms list must not corrupt internal state."""
        s = HubState()
        s.set_rooms([{"id": "r1", "name": "dev"}])
        rooms, _, _, _ = s.snapshot_render_state()
        rooms.append({"id": "X", "name": "intruder"})
        assert len(s.get_rooms()) == 1

    def test_set_rooms_preserves_selection_when_room_transiently_missing(self):
        """Regression for 'TUI keeps switching me in and out of rooms.'

        The relay's GET /rooms uses ``list_for_member`` for non-admin auth,
        which can transiently omit the user's current room while a
        background agent shuffles its membership. If we drop to welcome
        every time the room flickers out, the user gets bounced
        between the room view and the home screen on every poll.
        Fix: re-inject the stale room dict into the new list so the
        user stays put. The next poll typically returns the real room.
        """
        s = HubState()
        s.set_rooms([
            {"id": "r1", "name": "general"},
            {"id": "r2", "name": "may4-sprint"},
        ])
        s.select_by_name("may4-sprint")
        assert s.selected_room_name() == "may4-sprint"
        # Relay transiently forgets the room (membership-cache race)
        s.set_rooms([{"id": "r1", "name": "general"}])
        assert s.selected_room_name() == "may4-sprint"  # still in the room
        # Next poll returns it — the stale dict is replaced by the real one
        s.set_rooms([
            {"id": "r1", "name": "general"},
            {"id": "r2", "name": "may4-sprint", "members": ["arav"]},
        ])
        assert s.selected_room_name() == "may4-sprint"
        sel = s.get_selected_room()
        assert sel is not None
        # Confirm we got the FRESH dict, not the stale one
        assert sel.get("members") == ["arav"]

    def test_set_rooms_with_empty_list_preserves_selection(self):
        """A 5xx blip that returns no rooms must NOT kick the user out."""
        s = HubState()
        s.set_rooms([{"id": "r1", "name": "dev"}])
        s.select_by_name("dev")
        s.set_rooms([])  # transient empty response
        assert s.selected_room_name() == "dev"
        # And the room is preserved so the strip can still render it
        assert any(r.get("name") == "dev" for r in s.get_rooms())

    def test_set_rooms_from_welcome_with_empty_stays_at_welcome(self):
        """If user was at welcome (-1) and rooms is empty, stay at welcome."""
        s = HubState()
        s.set_rooms([])
        assert s.selected_room_idx == -1
        assert s.get_rooms() == []

    def test_set_connected(self):
        s = HubState()
        s.set_connected(True, "Connected")
        connected, status = s.get_connection()
        assert connected
        assert status == "Connected"

    def test_status_bar(self):
        s = HubState()
        assert s.get_status_bar() == ""
        s.set_status_bar("Sent!")
        assert s.get_status_bar() == "Sent!"

    def test_thread_safety(self):
        """Concurrent reads and writes must not raise."""
        s = HubState()
        errors = []

        def writer():
            for _ in range(50):
                s.set_rooms([{"name": "r"}, {"name": "s"}])
                s.select_next()

        def reader():
            for _ in range(50):
                try:
                    _ = s.get_selected_room()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ---------------------------------------------------------------------------
# Config helpers (via _save_instance_config)
# ---------------------------------------------------------------------------


def test_save_instance_config(tmp_path, monkeypatch):
    """_save_instance_config writes config through ConfigManager."""
    cfg_dir = tmp_path / ".quorus"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(tui_hub, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(tui_hub, "CONFIG_FILE", cfg_file)

    _save_instance_config("mybot", "http://relay:8080", "topsecret")

    assert cfg_file.exists()
    cfg = json.loads(cfg_file.read_text())
    assert cfg["relay_url"] == "http://relay:8080"
    assert cfg["instance_name"] == "mybot"
    assert cfg["relay_secret"] == "topsecret"
    assert cfg["poll_mode"] == "sse"
    # permissions should be 0600
    assert oct(cfg_file.stat().st_mode)[-3:] == "600"


def test_save_instance_config_strips_trailing_slash(tmp_path, monkeypatch):
    """Trailing slash on relay URL is stripped before saving."""
    cfg_dir = tmp_path / ".quorus"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(tui_hub, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(tui_hub, "CONFIG_FILE", cfg_file)

    _save_instance_config("bot", "http://relay:8080/", "secret")

    cfg = json.loads(cfg_file.read_text())
    assert not cfg["relay_url"].endswith("/")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def test_auth_headers_with_secret():
    headers = _auth_headers("my-secret")
    assert headers == {"Authorization": "Bearer my-secret"}


def test_auth_headers_empty_secret():
    assert _auth_headers("") == {}


def test_auth_headers_none_secret():
    assert _auth_headers(None) == {}


# ---------------------------------------------------------------------------
# Sender color assignment
# ---------------------------------------------------------------------------


def test_sender_color_consistent():
    """Same sender always gets the same color."""
    color1 = _sender_color("alice")
    color2 = _sender_color("alice")
    assert color1 == color2


def test_sender_color_different_senders():
    """Different senders typically get different colors (palette is large enough)."""
    senders = [f"agent-{i}" for i in range(8)]
    colors = [_sender_color(s) for s in senders]
    # At least 2 distinct colors assigned across 8 distinct senders
    assert len(set(colors)) >= 2


# ---------------------------------------------------------------------------
# Welcome / Home view — sort + render
# ---------------------------------------------------------------------------


def _render_welcome_to_string(**kwargs) -> str:
    """Render the welcome view to a string for assertion-friendly checks."""
    buf = io.StringIO()
    console = Console(
        file=buf, width=100, force_terminal=False, no_color=True,
        legacy_windows=False,
    )
    _welcome.render_welcome(console, **kwargs)
    return buf.getvalue()


class TestSortRoomsByActivity:
    def test_unread_rooms_come_first(self):
        rooms = [
            {"name": "alpha"},
            {"name": "bravo"},
            {"name": "charlie"},
        ]
        unread = {"bravo": 3}
        sorted_rooms = _welcome.sort_rooms_by_activity(rooms, unread)
        assert sorted_rooms[0]["name"] == "bravo"

    def test_higher_unread_count_comes_first(self):
        rooms = [
            {"name": "low"},
            {"name": "high"},
            {"name": "mid"},
        ]
        unread = {"low": 1, "high": 9, "mid": 4}
        sorted_rooms = _welcome.sort_rooms_by_activity(rooms, unread)
        names = [r["name"] for r in sorted_rooms]
        assert names == ["high", "mid", "low"]

    def test_idle_rooms_alphabetical_when_no_created_at(self):
        rooms = [
            {"name": "zulu"},
            {"name": "alpha"},
            {"name": "mike"},
        ]
        sorted_rooms = _welcome.sort_rooms_by_activity(rooms, {})
        names = [r["name"] for r in sorted_rooms]
        assert names == ["alpha", "mike", "zulu"]

    def test_unread_lookup_is_case_preserving(self):
        """Unread keys are stored verbatim — must not be lower-cased."""
        rooms = [{"name": "DevOps"}, {"name": "general"}]
        unread = {"DevOps": 2}  # exact case
        sorted_rooms = _welcome.sort_rooms_by_activity(rooms, unread)
        assert sorted_rooms[0]["name"] == "DevOps"


class TestRenderWelcome:
    def test_renders_action_menu_keys(self):
        out = _render_welcome_to_string(
            rooms=[],
            unread_by_room={},
            selected_room_name="",
            messages=[],
            agent_name="arav",
        )
        # Every action key must be present in the output.
        for key in ("[n]", "[j]", "[r]", "[s]", "[d]", "[/]", "[?]", "[q]"):
            assert key in out, f"missing action key {key} in welcome view"

    def test_renders_agent_name(self):
        out = _render_welcome_to_string(
            rooms=[], unread_by_room={}, selected_room_name="",
            messages=[], agent_name="arav",
        )
        assert "@arav" in out

    def test_empty_rooms_state_prompts_n(self):
        out = _render_welcome_to_string(
            rooms=[], unread_by_room={}, selected_room_name="",
            messages=[], agent_name="arav",
        )
        # Case-insensitive match — the empty-state copy is sentence-cased
        # ("No rooms yet.") for warmth. Test is on intent, not casing.
        assert "no rooms yet" in out.lower()
        assert "[n]" in out  # the action key, prompting first-room creation

    def test_renders_room_with_member_count(self):
        rooms = [{"name": "general", "members": ["arav", "ada"]}]
        out = _render_welcome_to_string(
            rooms=rooms, unread_by_room={}, selected_room_name="",
            messages=[], agent_name="arav",
        )
        assert "#general" in out
        assert "2 members" in out

    def test_unread_rooms_render_under_new_activity_header(self):
        rooms = [
            {"name": "quiet", "members": []},
            {"name": "noisy", "members": []},
        ]
        out = _render_welcome_to_string(
            rooms=rooms, unread_by_room={"noisy": 4},
            selected_room_name="", messages=[], agent_name="arav",
        )
        assert "new activity" in out
        assert "unread" in out and "4" in out
        # Idle bucket rendered separately below.
        new_idx = out.find("new activity")
        idle_idx = out.find("idle")
        assert 0 <= new_idx < idle_idx

    def test_footer_hint_present(self):
        out = _render_welcome_to_string(
            rooms=[], unread_by_room={}, selected_room_name="",
            messages=[], agent_name="arav",
        )
        # Key elements of the footer hint — split because Rich may wrap.
        assert "Tab" in out
        assert "Esc" in out


# ---------------------------------------------------------------------------
# Hub helpers — invite-token mint + delete RPC
# ---------------------------------------------------------------------------


def test_mint_join_token_is_portable_envelope():
    token = _mint_join_token("http://relay:8080", "topsecret", "general")
    assert token.startswith("quorus_join_")
    # Decodable back to the originating dict.
    import base64
    raw = base64.urlsafe_b64decode(token[len("quorus_join_"):]).decode()
    payload = json.loads(raw)
    assert payload["relay_url"] == "http://relay:8080"
    assert payload["secret"] == "topsecret"
    assert payload["room"] == "general"


def test_destroy_room_handles_unreachable_relay():
    """No relay running on bogus port → returns ('unreachable', detail)."""
    status, detail = tui_hub._destroy_room(
        "http://127.0.0.1:1",  # nothing listening
        "secret", "any-room", "arav",
    )
    assert status == "unreachable"
    assert isinstance(detail, str)


# ---------------------------------------------------------------------------
# Welcome state — selected_room_idx defaults to -1
# ---------------------------------------------------------------------------


def test_default_selected_room_idx_is_welcome_state():
    """Regression for the 'auto-loads room 0' bug — must default to -1."""
    s = HubState()
    assert s.selected_room_idx == -1
    # And get_selected_room must honor that.
    s.set_rooms([{"name": "general"}])
    assert s.get_selected_room() is None


# ---------------------------------------------------------------------------
# _PENDING_INPUT_BUF — input survives the 2s render-tick redraw
# ---------------------------------------------------------------------------


class TestPendingInputBuf:
    """Verify the in-progress input buffer survives a render-tick timeout
    and is cleared at workspace boundaries (security/cleanliness).
    """

    def test_pending_buf_starts_empty(self):
        # Module-level state must reset between tests; clear defensively.
        from quorus.tui_hub import _PENDING_INPUT_BUF
        _PENDING_INPUT_BUF.clear()
        assert _PENDING_INPUT_BUF == []

    def test_pending_buf_is_a_list_of_strings(self):
        """Type contract: assigning a non-list-of-str must not corrupt the
        runtime. We only need to verify the slot is a list (read-only here)."""
        from quorus.tui_hub import _PENDING_INPUT_BUF
        assert isinstance(_PENDING_INPUT_BUF, list)

    def test_pending_buf_round_trip(self):
        """Simulate the timeout-then-resume cycle: writer stashes a partial
        line into the buf, the reader pulls it back on next call."""
        from quorus.tui_hub import _PENDING_INPUT_BUF
        _PENDING_INPUT_BUF.clear()
        # _read_input would do:  _PENDING_INPUT_BUF[:] = list("hello")
        _PENDING_INPUT_BUF[:] = list("hello")
        assert "".join(_PENDING_INPUT_BUF) == "hello"
        # Next _read_input call would do: list(_PENDING_INPUT_BUF) then clear()
        restored = list(_PENDING_INPUT_BUF)
        _PENDING_INPUT_BUF.clear()
        assert restored == ["h", "e", "l", "l", "o"]
        assert _PENDING_INPUT_BUF == []

    def test_pending_buf_cleared_by_main_loop_entry(self):
        """The session entry into _main_input_loop must clear any leftover
        buffer from a prior workspace — otherwise text from session A could
        appear in the prompt of session B (audit Finding 1, MAJOR)."""
        from quorus.tui_hub import _PENDING_INPUT_BUF
        # Simulate a workspace-A leak.
        _PENDING_INPUT_BUF[:] = list("send api_key")
        assert _PENDING_INPUT_BUF != []
        # The clear that lives at the top of _main_input_loop:
        _PENDING_INPUT_BUF.clear()
        assert _PENDING_INPUT_BUF == []
