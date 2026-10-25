"""`quorus connect ollama|antigravity|aider|http` on a relay that uses API keys
(docs/plans/connect-secret-snippets.md, "relays that use API keys"): the agent gets
its own child key, saved in the profile and never printed, and never the user's."""
import ast
import json
import re
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from quorus_cli import cli

from quorus.profiles import ProfileManager

PARENT, CHILD = "mct_parent_key_aaa111", "mct_child_key_bbb222"
RELAY_A, RELAY_B = "http://relay-a.test:8080", "http://relay-b.test:9090"
PLATFORMS = ("ollama", "antigravity", "aider", "http")
_ENV = ("RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME", "QUORUS_PROFILE",
        "QUORUS_API_KEY")


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """The user `sam` on API-key relay A, in an isolated HOME. Returns the file."""
    config_dir = tmp_path / ".quorus"
    config_dir.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(config_dir))
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    pm = ProfileManager()
    pm.save("work", {"relay_url": RELAY_A, "api_key": PARENT, "instance_name": "sam"})
    pm.set_current("work")
    for attr, value in (("RELAY_URL", RELAY_A), ("RELAY_SECRET", ""), ("API_KEY", PARENT),
                        ("INSTANCE_NAME", "sam")):
        monkeypatch.setattr(cli, attr, value)
    return config_dir / "profiles" / "work.json"


@pytest.fixture
def register(monkeypatch):
    """The relay's /v1/auth/register-agent: names the child `sam-<suffix>`."""
    fake = MagicMock(side_effect=lambda *, relay_url, parent_api_key, suffix:
                     (f"sam-{suffix}", CHILD))
    monkeypatch.setattr(cli, "_register_agent_identity_full", fake)
    return fake


@pytest.fixture(autouse=True)
def relay(monkeypatch):
    """Every way `connect` reaches the relay, recorded: the async client the join
    uses, the user's own sign-in, and the agent's token exchange."""
    client = AsyncMock()
    token = MagicMock(status_code=200)
    token.json.return_value = {"token": "agent-jwt"}
    calls = types.SimpleNamespace(client=client, get_client=MagicMock(return_value=client),
                                  auth_headers=MagicMock(return_value={}),
                                  post=MagicMock(return_value=token))
    monkeypatch.setattr(cli, "_get_client", calls.get_client)
    monkeypatch.setattr(cli, "_auth_headers", calls.auth_headers)
    monkeypatch.setattr(cli._relay_http, "post", calls.post)
    return calls


def _connect(capsys, platform, name="sam-bot"):
    cli._cmd_connect(types.SimpleNamespace(platform=platform, room="dev", name=name))
    return capsys.readouterr().out


def _saved(profile):
    return json.loads(profile.read_text()).get("agent_api_keys", {})


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_agent_gets_its_own_key_and_no_key_is_printed(profile, register, capsys,
                                                          platform):
    out = _connect(capsys, platform)
    flat = "".join(out.split())  # rich wraps
    assert PARENT not in flat and CHILD not in flat
    assert _saved(profile) == {"sam-bot": CHILD}
    register.assert_called_once_with(relay_url=RELAY_A, parent_api_key=PARENT, suffix="bot")
    assert 'agent_api_keys["sam-bot"]' in flat and str(profile) in flat


@pytest.mark.parametrize("platform,first", [("ollama", "import os"), ("aider", "# In aider")])
def test_python_snippets_sign_in_with_the_agent_key(profile, register, capsys, platform,
                                                    first):
    out = _connect(capsys, platform)
    tree = ast.parse(out[out.index(first):out.index("\n\n  Before running it")])
    call = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and ast.unparse(n.func) == "QuorusClient")
    kwargs = {k.arg: ast.unparse(k.value) for k in call.keywords}
    assert kwargs == {"name": "'sam-bot'", "api_key": "os.environ['QUORUS_API_KEY']"}


def test_antigravity_asks_for_the_agent_key(profile, register, capsys):
    out = _connect(capsys, "antigravity")
    env = json.loads(out[out.index("{"):out.rindex("}") + 1])["mcpServers"]["quorus"]["env"]
    assert env == {"INSTANCE_NAME": "sam-bot", "RELAY_URL": RELAY_A,
                   "API_KEY": "<paste the agent key>"}


def test_http_exchanges_the_agent_key_for_a_token(profile, register, capsys):
    out = _connect(capsys, "http")
    assert '-d "{\\"api_key\\":\\"$QUORUS_API_KEY\\"}"' in out
    assert out.count('-H "Authorization: Bearer $QUORUS_TOKEN"') == 5
    assert re.findall(r"Bearer \S+", out) == ['Bearer $QUORUS_TOKEN"'] * 5


def test_a_second_connect_reuses_the_saved_key(profile, register, capsys):
    """The relay revokes earlier child keys on every register call."""
    _connect(capsys, "ollama")
    _connect(capsys, "aider")
    assert register.call_count == 1


def test_the_parents_own_name_still_gets_a_child_key(profile, register, capsys):
    out = _connect(capsys, "ollama", name="sam")
    assert _saved(profile) == {"sam-ollama": CHILD}
    assert 'name="sam-ollama"' in out and PARENT not in "".join(out.split())
    _connect(capsys, "ollama", name="sam")  # Codex on 319ab1dd: no rotation
    assert register.call_count == 1


def test_a_failed_mint_prints_no_snippet(profile, register, capsys):
    register.side_effect = None
    register.return_value = None
    with pytest.raises(SystemExit) as exc:
        _connect(capsys, "ollama")
    out = capsys.readouterr().out
    assert exc.value.code == 1 and "QuorusClient" not in out
    assert _saved(profile) == {}


@pytest.mark.parametrize("attr,value", [("RELAY_URL", RELAY_B), ("API_KEY", "mct_other_ccc")])
def test_a_shell_for_another_relay_changes_nothing(profile, register, relay, capsys,
                                                   monkeypatch, attr, value):
    """Codex on 1bb20571: B's child key saved in A's profile breaks the binding.
    Codex on 1ae50ab7: nothing may reach a relay first, not even the room join."""
    monkeypatch.setattr(cli, attr, value)
    before = profile.read_bytes()
    with pytest.raises(SystemExit):
        _connect(capsys, "http")
    out = capsys.readouterr().out
    register.assert_not_called()
    relay.get_client.assert_not_called()
    relay.auth_headers.assert_not_called()
    relay.post.assert_not_called()
    assert profile.read_bytes() == before
    flat = "".join(out.split())
    assert PARENT not in flat and CHILD not in flat
    assert attr != "API_KEY" or value not in flat
    assert "--api-key -" in out and "QUORUS_TOKEN" not in out


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_room_join_signs_in_as_the_agent(profile, register, relay, capsys, platform):
    _connect(capsys, platform)
    relay.post.assert_called_once()
    assert relay.post.call_args.kwargs["json"] == {"api_key": CHILD}
    assert relay.post.call_args.kwargs["follow_redirects"] is False
    relay.auth_headers.assert_not_called()  # never the user's own sign-in
    url, = relay.client.post.await_args.args
    assert url == f"{RELAY_A}/rooms/dev/join"
    assert relay.client.post.await_args.kwargs["json"] == {"participant": "sam-bot"}
    assert relay.client.post.await_args.kwargs["headers"] == {
        "Authorization": "Bearer agent-jwt"}


def test_a_failed_mint_joins_nothing(profile, register, relay, capsys):
    register.side_effect = None
    register.return_value = None
    with pytest.raises(SystemExit):
        _connect(capsys, "http")
    relay.get_client.assert_not_called()
    relay.post.assert_not_called()
