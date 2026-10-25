"""A removed agent's name no longer blocks adding it again (take-3 bug 2,
docs/plans/take3-fixes.md): re-add reuses a key saved in any key dir here, and
`agent remove` frees the claim only through a live relay `quorus` started."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from quorus_cli import agent_cmd, agent_identity

from quorus import bundle_client
from quorus.auth.participant_keys import ParticipantKeyStore
from quorus.runtime import local_relay
from tests.test_agent_cmd import _Args, _Console, env  # noqa: F401  (fixture)

URL = "http://127.0.0.1:8080"
NAME = "arav-claude"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("MESSAGES_FILE", raising=False)
    monkeypatch.delenv("QUORUS_HISTORY_DB", raising=False)
    monkeypatch.delenv("MURMUR_HISTORY_DB", raising=False)
    monkeypatch.setattr(agent_cmd, "load_config",
                        lambda: {"instance_name": "arav", "relay_url": URL, "relay_secret": "s"})
    monkeypatch.setattr(agent_cmd, "_launchd_ok", lambda: False)
    monkeypatch.setattr(agent_cmd, "_leave_all_rooms", lambda name: ["build"])
    monkeypatch.setattr(agent_identity, "_relay_http", _NoClaim())
    return tmp_path


class _NoClaim:
    def post(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("claimed a name whose key is saved here")


def _record_state(name: str, state: Path) -> None:
    agent_cmd._state_record(name).write_text(str(state))


def _relay(home: Path, monkeypatch, *, url: str = URL, live: bool = True,
           claims: Path | None = None) -> ParticipantKeyStore:
    """The record `quorus` writes when it starts a relay, with a claim for NAME."""
    claims = claims or home / "relay-data"
    store = ParticipantKeyStore(claims)
    store.claim(NAME)
    rec = {"pid": 4242, "port": 8080, "started": "t", "command": "py -m quorus.relay_cli",
           "url": url, "data_dir": str(claims)}
    (home / "cfg" / "relay.pid").write_text(json.dumps(rec))
    monkeypatch.setattr(local_relay, "_is_our_relay", lambda pid, s, c: live and pid == 4242)
    return store


def _remove() -> list[str]:
    console = _Console()
    agent_cmd.cmd_agent(_Args(agent_action="remove", name=NAME), console)
    return console.lines


def test_re_add_with_a_new_state_dir_reuses_the_saved_key(home) -> None:
    old, new = home / "qd" / "old", home / "qd" / "new"
    _record_state("arav-other-codex", old)  # any recorded state dir is searched
    bundle_client.save_participant_key(URL, NAME, "quorus_pk_saved", agent_identity.key_dir(
        NAME, old))
    assert agent_identity.ensure_key(URL, "s", NAME, new) == "quorus_pk_saved"  # no claim
    assert bundle_client.load_participant_key(
        URL, NAME, agent_identity.key_dir(NAME, new)) == "quorus_pk_saved"


def test_agent_add_puts_the_key_where_its_new_daemon_loads_it(env, tmp_path):  # noqa: F811
    old, new = tmp_path / "qd" / "old", tmp_path / "qd" / "new"
    _record_state(NAME, old)
    bundle_client.save_participant_key("http://r", NAME, "quorus_pk_saved",
                                       agent_identity.key_dir(NAME, old))
    agent_cmd.cmd_agent(_Args(state_dir=str(new)), _Console())
    assert env["daemons"] == [(NAME, "s3", True)]
    assert bundle_client.load_participant_key(
        "http://r", NAME, agent_identity.key_dir(NAME, new)) == "quorus_pk_saved"


def test_remove_frees_the_name_through_a_live_recorded_relay(home, monkeypatch) -> None:
    state = home / "qd" / "claude"
    _record_state(NAME, state)
    bundle_client.save_participant_key(URL, NAME, "quorus_pk_saved",
                                       agent_identity.key_dir(NAME, state))
    store = _relay(home, monkeypatch)
    # The CLI's own data_dir() names another claims file: it's never touched.
    monkeypatch.setenv("MESSAGES_FILE", str(home / "elsewhere" / "relay-state.json"))
    other = ParticipantKeyStore(home / "elsewhere")
    other.claim(NAME)
    lines = _remove()
    assert any(f"freed @{NAME}'s name on {URL}" in line for line in lines)
    assert store.info(NAME) is None and other.info(NAME) is not None
    for where in (agent_identity.key_dir(NAME, state), agent_identity.key_dir(NAME)):
        assert bundle_client.load_participant_key(URL, NAME, where) == ""
    assert store.claim(NAME)  # a fresh add claims cleanly


@pytest.mark.parametrize("case", ["no record", "stale pid", "another url", "no claims file",
                                  "foreign relay"])
def test_remove_releases_nothing_it_cant_prove(home, monkeypatch, case) -> None:
    state = home / "qd" / "claude"
    _record_state(NAME, state)
    bundle_client.save_participant_key(URL, NAME, "quorus_pk_saved",
                                       agent_identity.key_dir(NAME, state))
    store = _relay(home, monkeypatch, live=case != "stale pid",
                   url="http://127.0.0.1:9999" if case == "another url" else URL)
    if case == "no record":
        (home / "cfg" / "relay.pid").unlink()
    if case == "no claims file":
        (home / "relay-data" / "participant_keys.db").unlink()
    if case == "foreign relay":
        foreign = "https://relay.example.com"
        monkeypatch.setattr(agent_cmd, "load_config",
                            lambda: {"instance_name": "arav", "relay_url": foreign})
        bundle_client.save_participant_key(foreign, NAME, "quorus_pk_f",
                                           agent_identity.key_dir(NAME, state))
    lines = _remove()
    assert any("stays claimed on" in line and f"quorus release-name {NAME}" in line
               for line in lines)
    if case != "no claims file":
        assert store.info(NAME) is not None
    # The key moves to the CLI's own dir, so a re-add with a fresh state dir finds it.
    url = "https://relay.example.com" if case == "foreign relay" else URL
    assert agent_identity.adopt_key(url, NAME, home / "qd" / "fresh")


def test_remove_says_nothing_about_a_name_it_never_claimed(home) -> None:
    lines = _remove()
    assert not any("claimed" in line or "freed" in line for line in lines)


def test_a_409_with_no_saved_key_anywhere_says_so(home, monkeypatch) -> None:
    class _Http:
        def post(self, *a: Any, **k: Any) -> Any:
            return type("R", (), {"status_code": 409})()

    monkeypatch.setattr(agent_identity, "_relay_http", _Http())
    with pytest.raises(agent_identity.IdentityError,
                       match="isn't saved anywhere on this machine.*release-name"):
        agent_identity.ensure_key(URL, "s", NAME, home / "qd" / "new")


def test_the_relay_record_keeps_its_url_and_claims_dir(home, monkeypatch) -> None:
    monkeypatch.setattr(local_relay, "_ps", lambda pid, field: "x")
    (home / "cfg").mkdir(parents=True)  # ensure_local_relay creates it first
    claims = local_relay.data_dir({"MESSAGES_FILE": str(home / "r" / "relay-state.json")})
    local_relay._record(home / "cfg", 7, 8080, claims, "http://localhost:8080")
    rec = json.loads((home / "cfg" / "relay.pid").read_text())
    assert rec["url"] == "http://localhost:8080" and rec["data_dir"] == str(home / "r")


def test_a_relative_history_db_is_recorded_where_the_relay_reads_it(home, monkeypatch,
                                                                    tmp_path) -> None:
    """The relay runs in HOME, so it resolves a relative QUORUS_HISTORY_DB there.
    The record resolved it against the CLI's cwd, naming another claims file
    (codex, review of f6028a57)."""
    elsewhere = tmp_path / "cli-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("QUORUS_HISTORY_DB", "relative/history.db")
    seen: dict[str, Any] = {}

    class _Proc:
        pid = 4242

        def poll(self) -> None:
            return None

    def _popen(argv: list[str], **kw: Any) -> _Proc:
        seen.update(env=kw["env"], cwd=kw["cwd"])
        return _Proc()

    answers = iter([False, True])
    monkeypatch.setattr(local_relay, "_healthy", lambda url, t=2.0: next(answers))
    monkeypatch.setattr(local_relay.subprocess, "Popen", _popen)
    monkeypatch.setattr(local_relay, "_ps", lambda pid, field: "x")
    assert local_relay.ensure_local_relay(URL, "s")[0]
    relay_home = Path(seen["cwd"])
    want = (relay_home / "relative").resolve()
    assert seen["env"]["QUORUS_HISTORY_DB"] == str(want / "history.db")
    rec = json.loads((home / "cfg" / "relay.pid").read_text())
    assert rec["data_dir"] == str(want) and not rec["data_dir"].startswith(str(elsewhere))
