"""L1/L2 (WAKE_REBUILD_SPEC): live interactive Claude sessions.

L1 — a SessionStart hook registers pid+cwd (never the messaging token) so
reflexd knows a human already has a session open on a repo.
L2 — delivery into that session is the documented Stop hook; reflexd must
then NOT cold-spawn a competing headless agent (double-reply).
"""

from __future__ import annotations

import json
import os

import pytest
from quorus_cli import hooks as qhooks
from quorus_cli import live_sessions as ls


@pytest.fixture(autouse=True)
def registry_in_tmp(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ls, "REGISTRY_PATH", tmp_path / "live-sessions.json")
    return tmp_path


def test_register_stores_no_secrets(registry_in_tmp):
    ls.register(pid=os.getpid(), cwd="/repo/a", participant="arav",
                has_socket=True)
    raw = (registry_in_tmp / "live-sessions.json").read_text()
    assert "token" not in raw and "socket" not in raw.replace("has_socket", "")
    entry = ls.list_live()[0]
    assert entry["cwd"] == "/repo/a" and entry["has_socket"] is True


def test_registry_file_is_0600(registry_in_tmp):
    ls.register(pid=os.getpid(), cwd="/repo/a")
    mode = (registry_in_tmp / "live-sessions.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_dead_pids_are_pruned(registry_in_tmp, monkeypatch):
    ls.register(pid=os.getpid(), cwd="/repo/live")
    # A pid that cannot exist.
    data = json.loads((registry_in_tmp / "live-sessions.json").read_text())
    data["999999"] = {"pid": 999999, "cwd": "/repo/dead", "participant": ""}
    (registry_in_tmp / "live-sessions.json").write_text(json.dumps(data))
    live = ls.list_live()
    assert [e["cwd"] for e in live] == ["/repo/live"]


def test_find_for_cwd_matches_exact_and_sessions_inside_it(
    registry_in_tmp, tmp_path,
):
    """Containment is one-directional on purpose.

    A session working INSIDE a bound workspace counts; a session sitting in
    an ANCESTOR does not. Accepting the ancestor direction meant one Claude
    window open in ``~`` matched every workspace beneath it, so reflexd
    deferred every wake in every room and the product went silent with no
    error anywhere (adversarial review, 2026-08-21).
    """
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    ls.register(pid=os.getpid(), cwd=str(repo / "pkg"))
    # Exact match, and a session deeper inside the workspace.
    assert ls.find_for_cwd(str(repo / "pkg")) is not None
    assert ls.find_for_cwd(str(repo)) is not None
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert ls.find_for_cwd(str(other)) is None


def test_ancestor_session_does_not_claim_sibling_workspaces(
    registry_in_tmp, tmp_path,
):
    """The silencing bug: a session in a common ancestor must not match."""
    home = tmp_path / "home"
    (home / "projA").mkdir(parents=True)
    (home / "projB").mkdir()
    ls.register(pid=os.getpid(), cwd=str(home))
    assert ls.find_for_cwd(str(home / "projA")) is None
    assert ls.find_for_cwd(str(home / "projB")) is None
    assert ls.find_for_cwd(str(home)) is not None  # exact still matches


def test_unregister_removes_entry(registry_in_tmp):
    ls.register(pid=os.getpid(), cwd="/repo/a")
    ls.unregister(os.getpid())
    assert ls.list_live() == []


def test_stop_hook_blocks_with_messages(monkeypatch, capsys):
    """L2: pending room messages → Stop is blocked and the messages are
    handed back so the live session answers them in full context."""
    monkeypatch.setattr(qhooks, "_resolve_auth",
                        lambda: ("http://relay", "arav-claude", {}))
    monkeypatch.setattr(qhooks, "_fetch_unread",
                        lambda *a, **k: [{"id": "m1", "from_name": "arav",
                                          "content": "@arav-claude ping",
                                          "room": "dev"}])
    monkeypatch.setattr(qhooks, "_load_cursors", lambda i: {})
    monkeypatch.setattr(qhooks, "_save_cursors", lambda i, c: None)
    monkeypatch.setattr(qhooks, "_filter_unseen", lambda m, c: m)
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: "{}"})())
    assert qhooks.handle_claude_stop() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "ping" in out["reason"]


def test_stop_hook_silent_when_no_messages(monkeypatch, capsys):
    monkeypatch.setattr(qhooks, "_resolve_auth",
                        lambda: ("http://relay", "arav-claude", {}))
    monkeypatch.setattr(qhooks, "_fetch_unread", lambda *a, **k: [])
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: "{}"})())
    assert qhooks.handle_claude_stop() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_stop_hook_respects_loop_guard(monkeypatch, capsys):
    """stop_hook_active means we already blocked once — never loop."""
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("must not fetch when stop_hook_active")

    monkeypatch.setattr(qhooks, "_resolve_auth", boom)
    monkeypatch.setattr(
        "sys.stdin",
        type("S", (), {"read": lambda self: '{"stop_hook_active": true}'})(),
    )
    assert qhooks.handle_claude_stop() == 0
    assert json.loads(capsys.readouterr().out) == {}
