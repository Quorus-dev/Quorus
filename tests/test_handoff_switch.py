"""Never Stuck slice 2c-1: the per-room switch and machine paths out of the tail.

Dogfood plan §1 (docs/internal/bets/never-stuck-dogfood.md, Codex and Claude GO
on cfd69e70): live handoff is off unless the owner turned it on for one room,
beside a check. No global switch, no environment override; read on every call;
a flip is said in the room. The check tail loses the worktree and home paths
before it is hashed, the same way on both sides.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from quorus.runtime import check_config, handoff_runtime, handoff_sender
from quorus.runtime.checkpoint import local_paths

ROOT = Path(__file__).resolve().parents[1]
CHECK = {"argv": ["pytest", "-q"], "cwd": ".", "timeout_s": 45.0, "max_output_bytes": 65536}


def _bindings(tmp_path: Path, value: Any) -> Path:
    path = tmp_path / "room-bindings.json"
    path.write_text(json.dumps({"build": value}))
    return path


# ── the switch ──────────────────────────────────────────────────────────


def test_off_unless_the_owner_turned_it_on_beside_a_check(tmp_path) -> None:
    assert handoff_sender.enabled("build", None) is False
    assert handoff_sender.enabled("build", tmp_path / "missing.json") is False
    for value in ("/repo", {"path": "/repo", "check": CHECK},
                  {"path": "/repo", "handoff": True},
                  {"path": "/repo", "handoff": "true", "check": CHECK},
                  {"path": "/repo", "handoff": 1, "check": CHECK},
                  {"path": "/repo", "handoff": True, "check": {**CHECK, "timeout_s": 900}}):
        assert handoff_sender.enabled("build", _bindings(tmp_path, value)) is False, value
    on = _bindings(tmp_path, {"path": "/repo", "handoff": True, "check": CHECK})
    assert handoff_sender.enabled("build", on) is True
    assert handoff_sender.enabled("other-room", on) is False
    (tmp_path / "room-bindings.json").write_text("{not json")
    assert handoff_sender.enabled("build", on) is False  # read every call; broken is off


def test_there_is_no_global_switch_left_anywhere() -> None:
    assert not hasattr(handoff_sender, "ENABLED")
    for path in [*(ROOT / "quorus").rglob("*.py"), *(ROOT / "scripts").glob("*.py")]:
        assert not re.search(r"handoff_sender\.ENABLED", path.read_text()), path


def test_switch_state_names_the_check_it_runs(tmp_path) -> None:
    on = _bindings(tmp_path, {"path": "/repo", "handoff": True, "check": CHECK})
    state = handoff_runtime.switch_state(on, "build")
    assert state[0] is True
    assert state[1] == check_config.CheckConfig.from_record(CHECK).config_id
    assert handoff_runtime.switch_state(None, "build") == (False, "")


def _args(**kw: Any) -> Namespace:
    return Namespace(**{"check": None, "check_timeout": None, "check_output_kb": None,
                        "handoff": None, **kw})


def test_binding_turns_it_on_only_beside_a_check_and_off_with_the_check(tmp_path) -> None:
    ws = tmp_path
    with pytest.raises(ValueError, match="needs a check"):
        check_config.configure_binding({"path": str(ws)}, None, _args(handoff="on"), ws)
    a = _args(handoff="on")
    a.check = "pytest -q"
    on = check_config.configure_binding({"path": str(ws)}, None, a, ws)
    assert on["handoff"] is True and on["check"]["argv"] == ["pytest", "-q"]
    kept = check_config.configure_binding(dict(on), on, _args(), ws)
    assert kept["handoff"] is True  # rebinding without --handoff keeps it
    assert "handoff" not in check_config.configure_binding(dict(on), on, _args(handoff="off"), ws)
    removed = _args()
    removed.check = ""
    assert "handoff" not in check_config.configure_binding(dict(on), on, removed, ws)
    moved = check_config.configure_binding(dict(on), on, _args(), tmp_path / "other")
    assert "handoff" not in moved and "check" not in moved  # another repo: both go


def test_turning_it_on_is_typed_at_a_terminal(monkeypatch) -> None:
    import quorus_cli.cli as cli

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        cli._confirm_handoff_on("build")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "buidl")
    with pytest.raises(SystemExit):
        cli._confirm_handoff_on("build")
    monkeypatch.setattr("builtins.input", lambda _prompt: "build")
    cli._confirm_handoff_on("build")  # no exit


# ── the daemon says when it flips ───────────────────────────────────────


def test_a_flip_is_said_in_the_room_once(monkeypatch, tmp_path) -> None:
    from tests.test_unfinished_wakes import _daemon, _repo, reflexd

    repo = _repo(tmp_path)
    d = _daemon(monkeypatch, tmp_path, repo)
    bindings = reflexd.ROOM_BINDINGS_PATH
    said: list[str] = []

    async def post(_relay, _room, _env, text):
        said.append(text)

    monkeypatch.setattr(d, "_post_status", post)

    def note() -> None:
        asyncio.run(d._note_handoff_switch(None, "build", {}))

    note()
    assert said == []  # off from the start: nothing to say
    bindings.write_text(json.dumps({"build": {"path": str(repo), "mode": "autonomous",
                                              "handoff": True, "check": CHECK}}))
    note()
    note()
    cid = check_config.CheckConfig.from_record(CHECK).config_id
    assert said == [f"handoff is ON for this room (check {cid[:12]})"]
    bindings.write_text(json.dumps({"build": {"path": str(repo), "mode": "autonomous",
                                              "handoff": True,
                                              "check": {**CHECK, "argv": ["pytest", "-x"]}}}))
    note()
    assert said[-1].startswith("handoff is ON") and cid[:12] not in said[-1]  # check changed
    bindings.write_text(json.dumps({"build": {"path": str(repo), "mode": "autonomous"}}))
    note()
    assert said[-1] == "handoff is OFF for this room" and len(said) == 3


# ── machine paths out of the check tail ─────────────────────────────────


def test_worktree_and_home_paths_leave_the_tail_the_same_way_on_both_sides() -> None:
    home = "/Users/al"

    def tail(worktree: str) -> str:
        return (f'File "{worktree}/tests/test_x.py", line 3, in test_add\n'
                f"{home}/dev/.venv/lib/python3.13/site-packages/_pytest/x.py:9: in run\n"
                f"rootdir: {worktree}\nFAILED tests/test_x.py::test_add - assert 1 == 2")

    sender = local_paths(tail(f"{home}/dev/wt/arav-handoff-claude"),
                         Path(f"{home}/dev/wt/arav-handoff-claude"), home)
    receiver = local_paths(tail(f"{home}/dev/wt/arav-handoff-codex"),
                           Path(f"{home}/dev/wt/arav-handoff-codex"), home)
    assert sender == receiver
    assert hashlib.sha256(sender.encode()).digest() == hashlib.sha256(receiver.encode()).digest()
    assert "/Users/" not in sender and 'File "./tests/test_x.py"' in sender
    assert "~/dev/.venv/lib/python3.13" in sender and "rootdir: ." in sender


def test_only_whole_path_components_are_replaced() -> None:
    out = local_paths("/Users/alice/x and /Users/al/y and (/Users/al)", Path("/nowhere"),
                      "/Users/al")
    assert out == "/Users/alice/x and ~/y and (~)"
    assert local_paths("/x/y", Path("/repo"), None) == "/x/y"  # no HOME: nothing guessed
