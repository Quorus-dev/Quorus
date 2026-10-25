"""Review gate slice 4a: the owner's CLI (docs/plans/review-gate.md)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from quorus_cli import review_cmd

from quorus.runtime import review_gate as rg


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    return tmp_path / "cfg"


def _m(name: str, auth: str, **stamp: str) -> dict:
    return {"from_name": name, "content": "x", "sender_auth": auth,
            "sender_tenant": "t1", **stamp}


def test_latest_pin_takes_the_reviewers_own_key():
    hist = [_m("rev", "key", sender_claim_id="old"), _m("other", "key", sender_claim_id="o"),
            _m("rev", "key", sender_claim_id="c-rev")]
    assert review_cmd.latest_pin(hist, "rev") == {"tenant": "t1", "claim_id": "c-rev"}


def test_latest_pin_hosted_login():
    hist = [_m("rev", "jwt", sender_participant_id="p-9")]
    assert review_cmd.latest_pin(hist, "rev") == {"tenant": "t1", "participant_id": "p-9"}


@pytest.mark.parametrize("hist", [
    [],                                                     # never posted
    [_m("rev", "secret")],                                  # the relay secret proves nothing
    [_m("rev", "key", sender_claim_id="c"), _m("rev", "secret")],  # latest is a secret post
    [_m("rev", "key")],                                     # stamped key, no claim
])
def test_latest_pin_refuses_what_it_cannot_trust(hist):
    assert review_cmd.latest_pin(hist, "rev") is None


def _args(**kw):
    return SimpleNamespace(**({"room": "r", "name": None, "none": False, "unpinned": False}
                              | kw))


def test_room_reviewer_pins_and_shows(cfg, monkeypatch, capsys):
    monkeypatch.setattr(review_cmd, "_history",
                        lambda room: [_m("rev", "key", sender_claim_id="c-rev")])
    review_cmd.cmd_room_reviewer(_args(name="rev"))
    assert rg.reviewer_for("r") == {"name": "rev", "tenant": "t1", "claim_id": "c-rev"}
    review_cmd.cmd_room_reviewer(_args())
    out = " ".join(capsys.readouterr().out.split())  # Rich wraps long lines
    assert "pinned to c-rev" in out and "wait for its GO before main" in out


def test_room_reviewer_refuses_a_secret_only_reviewer(cfg, monkeypatch):
    monkeypatch.setattr(review_cmd, "_history", lambda room: [_m("rev", "secret")])
    with pytest.raises(SystemExit):
        review_cmd.cmd_room_reviewer(_args(name="rev"))
    assert rg.reviewer_for("r") is None  # nothing saved


def test_room_reviewer_unpinned_gates_with_override_only(cfg, monkeypatch, capsys):
    monkeypatch.setattr(review_cmd, "_history", lambda room: [])
    review_cmd.cmd_room_reviewer(_args(name="rev", unpinned=True))
    pin = rg.reviewer_for("r")
    assert pin is not None and not rg.pinned(pin)
    assert "every publish waits for your override" in " ".join(capsys.readouterr().out.split())


def test_room_reviewer_none_removes_the_gate(cfg, monkeypatch):
    rg.save_reviewer("r", "rev", tenant="t1", claim_id="c")
    review_cmd.cmd_room_reviewer(_args(none=True))
    assert rg.reviewer_for("r") is None


def test_unreachable_relay_saves_nothing(cfg, monkeypatch):
    monkeypatch.setattr(review_cmd, "_history", lambda room: None)
    with pytest.raises(SystemExit):
        review_cmd.cmd_room_reviewer(_args(name="rev"))
    assert rg.reviewer_for("r") is None


def _hold(cfg, tip: str, branch: str = "quorus/x") -> None:
    repo = cfg.parent / "repo"
    repo.mkdir(exist_ok=True)
    rg.save_marker(cfg / "runtime", repo, branch, room="r", tip=tip, base="0" * 40, count=1,
                   reviewer="rev", now=1.0)


def test_review_publish_sets_the_local_override(cfg):
    tip = "a" * 40
    _hold(cfg, tip)
    review_cmd.cmd_review_publish(SimpleNamespace(sha=tip[:9], unreviewed=True))
    [(path, marker)] = review_cmd._markers()
    assert marker["override_tip"] == tip
    assert (path.stat().st_mode & 0o077) == 0


def test_review_publish_needs_the_flag(cfg):
    _hold(cfg, "a" * 40)
    with pytest.raises(SystemExit):
        review_cmd.cmd_review_publish(SimpleNamespace(sha="a" * 9, unreviewed=False))
    assert "override_tip" not in review_cmd._markers()[0][1]


@pytest.mark.parametrize("sha", ["aaaaaa", "bbbbbbbb", "aaaaaaa"])
def test_review_publish_refuses_short_unknown_or_ambiguous(cfg, sha):
    _hold(cfg, "a" * 40, "quorus/x")
    _hold(cfg, "a" * 39 + "b", "quorus/y")  # "aaaaaaa" matches both
    with pytest.raises(SystemExit):
        review_cmd.cmd_review_publish(SimpleNamespace(sha=sha, unreviewed=True))
    assert all("override_tip" not in m for _p, m in review_cmd._markers())


def test_review_status_lists_held_tips(cfg, capsys):
    _hold(cfg, "c" * 40)
    review_cmd.cmd_review_status(SimpleNamespace())
    out = capsys.readouterr().out
    assert "c" * 12 in out and "waiting for @rev" in out


@pytest.mark.parametrize("argv, target, want", [
    (["room", "reviewer", "r", "rev"], "cmd_room_reviewer", ("reviewer", "r", "rev")),
    (["review", "publish", "abcdef1", "--unreviewed"], "cmd_review_publish",
     ("publish", "abcdef1", True)),
])
def test_cli_routes_the_new_commands(cfg, monkeypatch, argv, target, want):
    import sys

    import quorus_cli.cli as cli
    seen = []
    monkeypatch.setattr(review_cmd, target, lambda args: seen.append(args))
    monkeypatch.setattr(sys, "argv", ["quorus", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        assert not exc.code
    [args] = seen
    if target == "cmd_room_reviewer":
        assert (args.room_action, args.room, args.name) == want
    else:
        assert (args.review_action, args.sha, args.unreviewed) == want
    assert json.dumps(vars(args), default=str)


# ── take-3 bug 3: the pin reaches every daemon's file (docs/plans/take3-fixes.md) ──


def _reflexd():
    import importlib.util
    import sys
    from pathlib import Path

    if "reflexd" not in sys.modules:
        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location("reflexd", root / "scripts" / "reflexd.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["reflexd"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["reflexd"]


def _bind(base, binds):
    """*base*'s bindings; None for the CLI's own (where `quorus room bind` writes)."""
    from quorus.runtime import bindings

    path = bindings.bindings_path() if base is None else base / bindings.FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({room: {"path": str(repo)} for room, repo in binds.items()}))


def _builder(cfg, name, binds):
    from quorus_cli import agent_cmd

    state = cfg.parent / f"{name}-state"
    _bind(state, binds)
    (agent_cmd._agents_dir() / f"{name}.state-dir").write_text(str(state))
    return state


def _daemon_gate(monkeypatch, state, room, repo):
    """What the daemon started with --state-dir *state* decides for *room*."""
    from quorus.runtime import bindings

    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(state))
    return _reflexd()._review_gate_for(room, repo, bindings_path=state / bindings.FILENAME)


@pytest.fixture
def pinning(cfg, monkeypatch):
    repo = cfg.parent / "repo"
    repo.mkdir()
    monkeypatch.setattr(review_cmd, "_history",
                        lambda room: [_m("rev", "key", sender_claim_id="c-rev")])
    return repo.resolve()


def test_a_state_dir_agent_gets_the_pin_and_its_daemon_holds(cfg, pinning, monkeypatch, capsys):
    state = _builder(cfg, "a-claude", {"r": pinning})
    review_cmd.cmd_room_reviewer(_args(name="rev"))
    out = "".join(capsys.readouterr().out.split())  # Rich wraps inside the path
    assert str((state / "room-reviewers.json").resolve()) in out and "@a-claude" in out
    assert rg.reviewer_for("r")["name"] == "rev"  # the CLI's own file too
    assert _daemon_gate(monkeypatch, state, "r", pinning) == (
        "r", {"name": "rev", "tenant": "t1", "claim_id": "c-rev"})


def test_a_sibling_room_daemon_on_the_same_repo_holds(cfg, pinning, monkeypatch):
    _bind(None, {"r": pinning})
    state = _builder(cfg, "a-claude", {"side": pinning})  # never binds "r"
    review_cmd.cmd_room_reviewer(_args(name="rev"))
    gate = _daemon_gate(monkeypatch, state, "side", pinning)
    assert gate is not None and gate[0] == "r" and gate[1]["name"] == "rev"


def test_an_agent_on_another_repo_is_left_alone(cfg, pinning, monkeypatch):
    other = cfg.parent / "other"
    other.mkdir()
    _bind(None, {"r": pinning})
    state = _builder(cfg, "b-claude", {"x": other.resolve()})
    review_cmd.cmd_room_reviewer(_args(name="rev"))
    assert not (state / "room-reviewers.json").exists()


def test_none_clears_every_dir(cfg, pinning):
    state = _builder(cfg, "a-claude", {"r": pinning})
    review_cmd.cmd_room_reviewer(_args(name="rev"))
    review_cmd.cmd_room_reviewer(_args(none=True))
    assert rg.reviewer_for("r") is None
    assert rg.reviewer_for("r", state / "room-reviewers.json") is None


def test_a_file_that_cant_be_written_is_a_cross_and_changes_nothing(cfg, pinning, capsys):
    state = _builder(cfg, "a-claude", {"r": pinning})
    (state / "room-reviewers.json").write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        review_cmd.cmd_room_reviewer(_args(name="rev"))
    assert exc.value.code == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "✗" in out and "gate is unchanged" in out and "✓" not in out
    assert rg.reviewer_for("r") is None  # the CLI's file was put back


def test_disagreeing_dirs_are_reported(cfg, pinning, capsys):
    state = _builder(cfg, "a-claude", {"r": pinning})
    rg.save_reviewer("r", "rev", tenant="t1", claim_id="c-rev")
    rg.save_reviewer("r", "old", tenant="t1", claim_id="c-old",
                     path=state / "room-reviewers.json")
    review_cmd.cmd_room_reviewer(_args())
    out = " ".join(capsys.readouterr().out.split())
    assert "differs" in out and "@rev" in out and "@old" in out
