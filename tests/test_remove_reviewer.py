"""`quorus agent remove` on a pinned reviewer keeps the gate and says what it
leaves behind (docs/plans/remove-reviewer.md, plan GO edba2161)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from quorus_cli import agent_cmd, gate_setup

from quorus.runtime import review_gate as rg
from tests.test_agent_cmd import _Args, _Console

CODEX = "alice-codex"
LEAD = {"name": "alice-claude", "tenant": "t", "claim_id": "c-lead"}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = {"instance_name": "alice", "relay_url": "http://127.0.0.1:9", "relay_secret": "s"}
    monkeypatch.setattr(agent_cmd, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(agent_cmd, "_launchd_ok", lambda: False)
    monkeypatch.setattr(agent_cmd, "_leave_all_rooms", lambda name: ["general"])
    posted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(gate_setup, "post", lambda prep, text: posted.append(
        (prep["room"], prep["bearer"], text)))
    return {"tmp": tmp_path, "posted": posted}


def _remove(name: str = CODEX) -> list[str]:
    console = _Console()
    agent_cmd.cmd_agent(_Args(agent_action="remove", name=name), console)
    return console.lines


def test_removing_the_reviewer_keeps_the_gate_and_says_how_to_get_through(home):
    rg.save_reviewer("general", CODEX, tenant="t", claim_id="c-codex", second=LEAD)
    before = rg.reviewers_path().read_bytes()
    lines = _remove()
    assert rg.reviewers_path().read_bytes() == before  # never quietly un-gated
    said = [ln for ln in lines if "wait for a reviewer that's gone (@alice-codex)" in ln]
    assert said and "quorus room reviewer general <name>" in said[0]
    assert "quorus room reviewer general --none" in said[0]
    [(room, bearer, text)] = home["posted"]
    assert room == "general" and bearer == "s" and "#general's commits" in text


def test_removing_the_second_says_whose_commits_now_wait_for_you(home):
    rg.save_reviewer("general", CODEX, tenant="t", claim_id="c-codex", second=LEAD)
    lines = _remove("alice-claude")
    assert any("@alice-codex's own commits in #general now wait for you" in ln
               and "--second <name>" in ln for ln in lines)
    assert rg.reviewer_for("general")["second"]["name"] == "alice-claude"  # kept


def test_an_agent_in_no_pin_gets_no_gate_line(home):
    rg.save_reviewer("general", CODEX, tenant="t", claim_id="c-codex")
    lines = _remove("alice-gemini")
    assert not any("review" in ln for ln in lines) and home["posted"] == []


def test_a_pin_only_in_a_state_dir_is_found_before_its_record_goes(home):
    state = home["tmp"] / "qd" / "codex"
    state.mkdir(parents=True)
    agent_cmd._state_record(CODEX).write_text(str(state))
    rg.save_reviewer("general", CODEX, tenant="t", claim_id="c", path=state /
                     "room-reviewers.json")
    lines = _remove()
    assert any("reviewer that's gone" in ln for ln in lines)
    assert not agent_cmd._state_record(CODEX).exists()  # removal still completed


def test_an_unreadable_reviewers_file_is_said_and_the_removal_completes(home):
    rg.reviewers_path().parent.mkdir(parents=True, exist_ok=True)
    rg.reviewers_path().write_text("{not json")
    lines = _remove()
    assert any("couldn't check the review gate in" in ln for ln in lines)
    assert any("stopped" in ln or "left #general" in ln for ln in lines)


def test_warnings_still_come_when_the_agent_already_left_and_the_relay_is_down(
        home, monkeypatch):
    """codex, plan review of edba2161: discovery doesn't depend on room lookup."""
    rg.save_reviewer("general", CODEX, tenant="t", claim_id="c-codex")
    monkeypatch.setattr(agent_cmd, "_leave_all_rooms", lambda name: [])  # lookup failed
    console = _Console()
    with pytest.raises(SystemExit):  # nothing running, no rooms left: "no agent here"
        agent_cmd.cmd_agent(_Args(agent_action="remove", name=CODEX), console)
    assert any("reviewer that's gone" in ln for ln in console.lines)


def test_no_sign_in_still_prints_the_line(home, monkeypatch):
    rg.save_reviewer("general", CODEX, tenant="t", claim_id="c-codex")
    monkeypatch.setattr(agent_cmd, "load_config", lambda: {
        "instance_name": "alice", "relay_url": "http://127.0.0.1:9", "api_key": "k"})

    def down(*_a: Any) -> str:
        raise OSError("relay down")
    monkeypatch.setattr(agent_cmd, "_bearer", down)
    lines = _remove()
    assert any("reviewer that's gone" in ln for ln in lines) and home["posted"] == []
