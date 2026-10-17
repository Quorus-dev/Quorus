"""A room lead (`quorus agent add ... --lead`), 2026-10-17.

Without one, every agent answers the human (by design) and each tends to
start the same work. The lead splits the request and assigns it; the others
answer briefly and wait for an assignment; results flow back to the lead.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from quorus_cli import agent_cmd

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
wake = reflexd.reflexd_wake
TEAM = ["a-claude", "a-codex", "a-opencode"]


def _text(participant: str, lead: str | None, kind: str = "room") -> str:
    return wake.wake_instructions(participant=participant, room="build", sender="arav",
                                  kind=kind, teammates=TEAM, has_workspace=True, lead=lead)


def test_lead_splits_and_assigns() -> None:
    text = _text("a-claude", "a-claude")
    assert "You LEAD this room" in text and "@-mention" in text
    assert "tell `arav` when everything is done" in text


def test_others_answer_briefly_and_wait_for_the_lead() -> None:
    text = _text("a-codex", "a-claude")
    assert "@a-claude leads this room" in text and "don't start work unless" in text
    assert "@-mention @a-claude with the result" in text


def test_no_lead_keeps_everyone_answering() -> None:
    text = _text("a-codex", None)
    assert "LEAD" not in text and "leads this room" not in text
    assert "each one answers it" in text
    # A lead that isn't in the room (removed agent, typo) is ignored.
    assert "leads this room" not in _text("a-codex", "ghost-claude")


def test_lead_for_reads_the_leads_file(tmp_path: Path) -> None:
    path = tmp_path / "room-leads.json"
    assert reflexd.lead_for("build", leads_path=path) is None
    path.write_text(json.dumps({"build": "a-claude"}))
    assert reflexd.lead_for("build", leads_path=path) == "a-claude"
    assert reflexd.lead_for("other", leads_path=path) is None


def test_cli_sets_lead_and_remove_clears_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent_cmd.Path, "home", staticmethod(lambda: tmp_path))
    agent_cmd._set_lead("build", "arav-claude")
    agent_cmd._set_lead("ops", "arav-claude")
    agent_cmd._set_lead("chat", "arav-codex")
    agent_cmd._drop_lead_everywhere("arav-claude")
    data = json.loads((tmp_path / ".quorus" / "room-leads.json").read_text())
    assert data == {"chat": "arav-codex"}
