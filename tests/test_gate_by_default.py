"""Gate by default, slice B: the first run's second vendor reviews before main
(docs/plans/gate-by-default.md, Codex plan GO b4b34727)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from quorus_cli import agent_cmd, agent_identity, gate_setup, quickstart

from quorus.runtime import review_gate as rg


class _C:
    def __init__(self) -> None:
        self.out: list[str] = []

    def print(self, *a: Any, **_k: Any) -> None:
        self.out.append(" ".join(map(str, a)))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    return tmp_path


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(quickstart, "_codex_logged_in", lambda: True)
    monkeypatch.setattr(quickstart, "_claude_logged_in", lambda: True)
    monkeypatch.setattr(agent_cmd, "_gemini_has_key", lambda: False)
    monkeypatch.setattr(agent_cmd, "_opencode_version", lambda: "2.0.30")
    return lambda only: (lambda b: f"/bin/{b}" if b in only else None)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _prep(t: str, **kw: Any) -> dict:
    return {"cfg": {"relay_url": "http://r"}, "me": "a", "name": f"a-{t}", "room": "general",
            "repo": "/p", "model": None, "state_dir": None, "existing": None,
            "cred": "sec", "legacy": True, "bearer": "sec", **kw}


def _offer(c, repo, which, events, *, gate=True, pin=None):
    def prepare(args, _c):
        events.append(("prepare", args.tool))
        return _prep(args.tool)

    def start(prep, _c):
        events.append(("start", prep["name"]))

    def do_pin(prep):
        events.append(("pin", prep["name"]))
        if pin:
            pin(prep)

    return quickstart.offer(c, "general", cwd=repo, ask=lambda q: True, which=which,
                            ask_write=lambda q: True, ask_gate=lambda q: gate,
                            prepare=prepare, start=start, pin=do_pin)


def test_pick_reviewer_is_the_best_other_vendor():
    order = quickstart.LEAD_ORDER
    assert gate_setup.pick_reviewer(["claude", "codex", "opencode"], "claude", order) == "codex"
    assert gate_setup.pick_reviewer(["claude", "opencode"], "claude", order) == "opencode"
    assert gate_setup.pick_reviewer(["codex", "gemini"], "codex", order) == "gemini"
    assert gate_setup.pick_reviewer(["claude"], "claude", order) is None
    assert gate_setup.pick_reviewer(["claude", "codex"], None, order) is None


def test_prepare_all_then_pin_then_start_all(cfg, tools, monkeypatch):
    posted: list[str] = []
    monkeypatch.setattr(gate_setup, "post", lambda prep, text: posted.append(text))
    events: list[tuple] = []
    c = _C()
    added = _offer(c, _repo(cfg), tools(("claude", "codex")), events)
    assert added == ["claude", "codex"]
    # No daemon starts before the pin exists (codex, review of 0e361b2d).
    assert events == [("prepare", "claude"), ("prepare", "codex"), ("pin", "a-codex"),
                      ("start", "a-claude"), ("start", "a-codex")]
    [line] = posted
    assert line.startswith("Review gate on: commits in ")
    assert "wait for @a-codex's GO before they reach main" in line
    assert "`quorus room reviewer general --none`" in line
    assert any("Review gate on" in o for o in c.out)


@pytest.mark.parametrize("only", [("claude",), ("codex",)])
def test_one_agent_means_no_gate(cfg, tools, only):
    events: list[tuple] = []
    asked: list[str] = []
    quickstart.offer(_C(), "general", cwd=_repo(cfg), ask=lambda q: True, which=tools(only),
                     ask_write=lambda q: True, ask_gate=lambda q: asked.append(q) or True,
                     prepare=lambda a, _c: _prep(a.tool), start=lambda p, _c: None,
                     pin=lambda p: events.append("pin"))
    assert asked == [] and events == []


def test_no_repo_means_no_gate_question(cfg, tools):
    events: list[str] = []
    asked: list[str] = []
    empty = cfg / "not-a-repo"
    empty.mkdir()
    quickstart.offer(_C(), "general", cwd=empty, ask=lambda q: True,
                     which=tools(("claude", "codex")), ask_write=lambda q: True,
                     ask_gate=lambda q: asked.append(q) or True,
                     prepare=lambda a, _c: _prep(a.tool), start=lambda p, _c: None,
                     pin=lambda p: events.append("pin"))
    assert asked == [] and events == []


def test_saying_no_adds_the_agents_without_a_gate(cfg, tools, monkeypatch):
    posted: list[str] = []
    monkeypatch.setattr(gate_setup, "post", lambda prep, text: posted.append(text))
    events: list[tuple] = []
    added = _offer(_C(), _repo(cfg), tools(("claude", "codex")), events, gate=False)
    assert added == ["claude", "codex"]
    assert ("pin", "a-codex") not in events and posted == []


def test_a_failed_pin_starts_everyone_without_a_gate_and_says_so(cfg, tools, monkeypatch):
    posted: list[str] = []
    monkeypatch.setattr(gate_setup, "post", lambda prep, text: posted.append(text))
    events: list[tuple] = []
    c = _C()

    def boom(prep):
        raise agent_identity.IdentityError("the relay wouldn't claim @a-codex (HTTP 503)")

    added = _offer(c, _repo(cfg), tools(("claude", "codex")), events, pin=boom)
    assert added == ["claude", "codex"]
    assert [e for e in events if e[0] == "start"] == [("start", "a-claude"), ("start", "a-codex")]
    assert posted == []
    text = " ".join(c.out)
    assert "Couldn't turn on the review gate (the relay wouldn't claim @a-codex (HTTP 503))" in text
    assert "agents publish when a turn ends, as before" in text


# ── pin_reviewer: the key the agent actually signs in with ──────────────


def test_a_new_reviewer_is_pinned_from_its_claimed_key(cfg, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(agent_identity, "ensure_key",
                        lambda url, cred, name, where: calls.append(("claim", name, cred))
                        or "quorus_pk_new")
    monkeypatch.setattr(agent_identity, "identity",
                        lambda url, key: {"tenant": "_legacy", "claim_id": f"c-{key}"})
    gate_setup.pin_reviewer(_prep("codex"))
    assert calls == [("claim", "a-codex", "sec")]
    assert rg.reviewer_for("general") == {"name": "a-codex", "tenant": "_legacy",
                                          "claim_id": "c-quorus_pk_new"}


def test_a_running_reviewer_is_pinned_to_its_saved_key_without_a_new_claim(cfg, monkeypatch):
    from quorus import bundle_client

    bundle_client.save_participant_key("http://r", "a-codex", "quorus_pk_saved",
                                       agent_identity.key_dir("a-codex"))
    monkeypatch.setattr(agent_identity, "ensure_key",
                        lambda *a: pytest.fail("no new key for a running daemon"))
    monkeypatch.setattr(agent_identity, "identity",
                        lambda url, key: {"tenant": "_legacy", "claim_id": f"c-{key}"})
    gate_setup.pin_reviewer(_prep("codex", existing="pid 42"))
    assert rg.reviewer_for("general")["claim_id"] == "c-quorus_pk_saved"


def test_a_running_reviewer_with_no_saved_key_is_refused(cfg, monkeypatch):
    monkeypatch.setattr(agent_identity, "ensure_key",
                        lambda *a: pytest.fail("no new key for a running daemon"))
    with pytest.raises(agent_identity.IdentityError, match="couldn't confirm which identity"):
        gate_setup.pin_reviewer(_prep("codex", existing="pid 42"))
    assert rg.reviewer_for("general") is None


def test_an_existing_gate_is_kept(cfg, monkeypatch):
    rg.save_reviewer("general", "a-gemini", tenant="_legacy", claim_id="c-old")
    with pytest.raises(agent_identity.IdentityError, match="already has @a-gemini"):
        gate_setup.pin_reviewer(_prep("codex"))
    assert rg.reviewer_for("general")["name"] == "a-gemini"


def test_a_failure_with_replace_keeps_the_old_gate(cfg, monkeypatch):
    rg.save_reviewer("general", "a-gemini", tenant="_legacy", claim_id="c-old")

    def boom(*a):
        raise agent_identity.IdentityError("relay down")
    monkeypatch.setattr(agent_identity, "ensure_key", boom)
    with pytest.raises(agent_identity.IdentityError):
        gate_setup.pin_reviewer(_prep("codex"), replace=True)
    assert rg.reviewer_for("general")["claim_id"] == "c-old"


def test_a_relay_you_dont_run_is_refused(cfg):
    with pytest.raises(agent_identity.IdentityError, match="isn't one you run"):
        gate_setup.pin_reviewer(_prep("codex", legacy=False))
    assert rg.reviewer_for("general") is None


# ── agent add --reviewer ─────────────────────────────────────────────────


def test_agent_add_reviewer_pins_before_it_starts(cfg, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(agent_cmd, "_prepare", lambda a, c: events.append("prepare")
                        or _prep("codex"))
    monkeypatch.setattr(agent_cmd, "_start", lambda p, c: events.append("start"))
    monkeypatch.setattr(gate_setup, "pin_reviewer",
                        lambda p, replace=False: events.append(f"pin:{replace}"))
    monkeypatch.setattr(gate_setup, "post", lambda p, t: events.append("post"))
    agent_cmd._add(SimpleNamespace(reviewer=True, replace=False), _C())
    assert events == ["prepare", "pin:False", "start", "post"]


def test_agent_add_reviewer_failure_starts_nothing(cfg, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(agent_cmd, "_prepare", lambda a, c: _prep("codex"))
    monkeypatch.setattr(agent_cmd, "_start", lambda p, c: events.append("start"))

    def boom(p, replace=False):
        raise agent_identity.IdentityError("couldn't confirm which identity @a-codex posts with")
    monkeypatch.setattr(gate_setup, "pin_reviewer", boom)
    with pytest.raises(SystemExit, match="no review gate"):
        agent_cmd._add(SimpleNamespace(reviewer=True, replace=False), _C())
    assert events == []


def test_plain_agent_add_is_unchanged(cfg, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(agent_cmd, "_prepare", lambda a, c: _prep("codex"))
    monkeypatch.setattr(agent_cmd, "_start", lambda p, c: events.append("start"))
    monkeypatch.setattr(gate_setup, "pin_reviewer", lambda *a, **k: events.append("pin"))
    agent_cmd._add(SimpleNamespace(), _C())
    assert events == ["start"]
