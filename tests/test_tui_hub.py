"""Tests for quorus/tui_hub.py — HubState, config helpers, API helpers."""

import json
import threading

from quorus import tui_hub
from quorus.tui_hub import (
    HubState,
    _auth_headers,
    _load_config,
    _sender_color,
    _write_config,
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
        assert s.selected_room_idx == 0

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
        assert s.selected_room_name() == "dev"
        s.select_next()
        assert s.selected_room_name() == "ops"

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
# Config helpers
# ---------------------------------------------------------------------------


def test_load_config_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(tui_hub, "CONFIG_FILE", tmp_path / "nonexistent.json")
    assert _load_config() is None


def test_load_config_returns_dict(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"relay_url": "http://localhost:8080", "instance_name": "bot"}')
    monkeypatch.setattr(tui_hub, "CONFIG_FILE", cfg_file)
    result = _load_config()
    assert result["relay_url"] == "http://localhost:8080"
    assert result["instance_name"] == "bot"


def test_load_config_returns_none_on_invalid_json(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("not-json{{{")
    monkeypatch.setattr(tui_hub, "CONFIG_FILE", cfg_file)
    assert _load_config() is None


def test_write_config(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".quorus"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(tui_hub, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(tui_hub, "CONFIG_FILE", cfg_file)

    _write_config("mybot", "http://relay:8080", "topsecret")

    assert cfg_file.exists()
    cfg = json.loads(cfg_file.read_text())
    assert cfg["relay_url"] == "http://relay:8080"
    assert cfg["instance_name"] == "mybot"
    assert cfg["relay_secret"] == "topsecret"
    assert cfg["poll_mode"] == "sse"
    # permissions should be 0600
    assert oct(cfg_file.stat().st_mode)[-3:] == "600"


def test_write_config_strips_trailing_slash(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".quorus"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(tui_hub, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(tui_hub, "CONFIG_FILE", cfg_file)

    _write_config("bot", "http://relay:8080/", "secret")

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
