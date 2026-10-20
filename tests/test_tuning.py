"""Per-user tuning store and `quorus tune`."""

from __future__ import annotations

import io
import json
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from quorus_cli.tune_cmd import cmd_tune
from rich.console import Console

from quorus import tuning

SCOPE = "relay.test|arav|/repo"


@pytest.fixture
def store(monkeypatch, tmp_path: Path) -> Path:
    p = tmp_path / "tuning.json"
    monkeypatch.setenv("QUORUS_TUNING_FILE", str(p))
    return p


def _adapt(subject: str = "a-gemini", scope: str = SCOPE):
    return tuning.adapt("bid", subject=subject, scope=scope,
                        change=f"@open bids x0.67 for @{subject}",
                        because="x0.67 · 2 vendor fails, 0 clean/24h")


def _run(**kw) -> str:
    out = io.StringIO()
    args = SimpleNamespace(action=kw.pop("action", "list"), id=kw.pop("id", None),
                           json=kw.pop("json", False))
    cmd_tune(args, Console(file=out, width=200, color_system=None))
    return out.getvalue()


def test_store_is_one_private_file_per_user_not_per_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("QUORUS_TUNING_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "agent-identity"))
    assert tuning.path() == tmp_path / ".quorus" / "tuning.json"
    assert _adapt() is not None
    assert stat.S_IMODE(tuning.path().stat().st_mode) == 0o600


def test_one_live_adaptation_per_subject_and_scope(store):
    first = _adapt()
    assert first is not None and first.id == "t1" and first.scope == SCOPE
    assert _adapt() is None  # still active
    assert _adapt(scope="other|arav|/repo").id == "t2"
    assert _adapt(subject="a-codex").id == "t3"


def test_undo_blocks_new_adaptations_until_redo(store):
    _adapt()
    assert tuning.undo("t1").state == "undone"
    assert tuning.undo("t1") is None
    assert _adapt() is None  # the user's no holds
    assert tuning.redo("t1").state == "active"
    assert tuning.retire("t1").state == "retired"
    assert _adapt().id == "t2"  # retired by evidence: a new one may start


def test_off_stops_new_adaptations(store):
    tuning.set_enabled(False)
    assert not tuning.enabled() and _adapt() is None
    tuning.set_enabled(True)
    assert _adapt() is not None


def test_unreadable_file_fails_closed_and_is_not_overwritten(store):
    store.write_text("{not json")
    assert tuning.is_invalid() and not tuning.enabled() and tuning.adaptations() == []
    assert _adapt() is None and tuning.undo("t1") is None
    assert store.read_text() == "{not json"
    assert "can't be read" in _run()
    tuning.set_enabled(True)  # an explicit tune on/off starts a fresh file
    assert tuning.enabled() and not tuning.is_invalid()
    store.write_text(json.dumps({"adaptations": [{"id": "x", "kind": "evil"}]}))
    assert tuning.adaptations() == [] and tuning.enabled()


def test_concurrent_daemons_never_lose_a_write(store):
    threads = [threading.Thread(target=_adapt, args=(f"a{i}-claude",)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [a.id for a in tuning.adaptations()]
    assert sorted(ids, key=lambda i: int(i[1:])) == [f"t{i}" for i in range(1, 13)]


def test_cli_lists_undoes_redoes_and_switches_off(store):
    assert "Nothing learned yet" in _run()
    _adapt()
    listed = _run()
    assert "t1" in listed and "x0.67" in listed and "relay.test · arav · /repo" in listed
    assert "undone t1" in _run(action="undo", id="t1")
    assert "re-enabled t1" in _run(action="redo", id="t1")
    with pytest.raises(SystemExit):
        _run(action="redo", id="t1")
    assert "back to stock" in _run(action="off")
    assert "tuning is off" in _run()
    assert json.loads(_run(json=True))["enabled"] is False
    assert "adapt to you again" in _run(action="on")


def test_ids_name_the_owner_when_given(store):
    """0.6.3 multi-person audit, BUG 18: two owners' agents both announced "t1" in a
    shared room, so `quorus tune undo t1` was ambiguous to read."""
    a = tuning.adapt("bid", subject="a-gemini", scope=SCOPE, change="c", because="b",
                     owner="arav")
    assert a.id == "arav.t1"
    assert tuning.undo("arav.t1").state == "undone"
    b = tuning.adapt("bid", subject="b-codex", scope=SCOPE, change="c", because="b")
    assert b.id == "t2"  # no owner: the plain form still works
