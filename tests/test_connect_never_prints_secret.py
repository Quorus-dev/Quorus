"""0.7.6 security fix (docs/plans/connect-secret-snippets.md): `quorus connect
ollama|antigravity|aider|http` printed the relay secret inside the snippet to paste.
The snippets now read it from the environment, or leave it to `quorus-mcp`, and each
test checks that the generated setup still finds the secret, not just that it ran."""
import ast
import json
import re
import types
from unittest.mock import AsyncMock, patch

import pytest
from quorus_cli import cli

from quorus.config import load_config

SAVED, SHELL = "saved-s3cret-aaa111", "shell-s3cret-bbb222"
RELAY_A, RELAY_B = "http://relay-a.test:8080", "http://relay-b.test:9090"
PLATFORMS = ("ollama", "antigravity", "aider", "http")
_ENV = ("RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME", "QUORUS_PROFILE",
        "QUORUS_RELAY_SECRET")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A saved profile for relay A, in an isolated HOME and config dir."""
    config_dir = tmp_path / ".quorus"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps(
        {"relay_url": RELAY_A, "relay_secret": SAVED, "instance_name": "sam"}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(config_dir))
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "API_KEY", "")
    return config_dir


def _connect(monkeypatch, capsys, platform, relay_url, secret):
    """Run the real `quorus connect`, as loaded for *relay_url* and *secret*."""
    monkeypatch.setattr(cli, "RELAY_URL", relay_url)
    monkeypatch.setattr(cli, "RELAY_SECRET", secret)
    args = types.SimpleNamespace(platform=platform, room="dev", name="bot")
    client = AsyncMock()
    with patch.object(cli, "_get_client", return_value=client):
        cli._cmd_connect(args)
    client.post.assert_awaited()  # it still joins the room
    return capsys.readouterr().out


def _launch_env(monkeypatch, env):
    """The environment quorus-mcp starts with: the pasted block, nothing from here."""
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _json_block(out):
    return json.loads(out[out.index("{"):out.rindex("}") + 1])


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("relay_url,secret", [(RELAY_A, SAVED), (RELAY_B, SHELL)])
def test_no_snippet_prints_the_secret(home, monkeypatch, capsys, platform, relay_url,
                                      secret):
    out = _connect(monkeypatch, capsys, platform, relay_url, secret)
    assert secret not in out and secret not in "".join(out.split())  # rich wraps


def test_antigravity_leaves_a_saved_secret_to_quorus_mcp(home, monkeypatch, capsys):
    env = _json_block(_connect(monkeypatch, capsys, "antigravity", RELAY_A, SAVED))
    env = env["mcpServers"]["quorus"]["env"]
    assert "RELAY_SECRET" not in env
    # What quorus-mcp sees when Antigravity launches it with only this block.
    _launch_env(monkeypatch, env)
    assert load_config(migrate=False)["relay_secret"] == SAVED


def test_antigravity_keeps_a_key_for_a_secret_only_this_shell_has(home, monkeypatch,
                                                                  capsys):
    """Codex on dec213c0: the MCP process never sees this shell's RELAY_SECRET."""
    out = _connect(monkeypatch, capsys, "antigravity", RELAY_B, SHELL)
    env = _json_block(out)["mcpServers"]["quorus"]["env"]
    assert env["RELAY_SECRET"] == "<paste your RELAY_SECRET>"
    assert f"quorus init bot --relay-url {RELAY_B} --secret -" in " ".join(out.split())
    _launch_env(monkeypatch, {k: v for k, v in env.items() if k != "RELAY_SECRET"})
    cfg = load_config(migrate=False)
    assert cfg["relay_secret"] == "" and cfg["credentials_withheld"]  # why the key stays
    _launch_env(monkeypatch, {**env, "RELAY_SECRET": SHELL})  # the user filled it in
    assert load_config(migrate=False)["relay_secret"] == SHELL


def _script(out, first_line):
    start = out.index(first_line)
    end = out.index("\n\n  Before running it")
    return out[start:end]


@pytest.mark.parametrize("platform,first", [("ollama", "import os"),
                                            ("aider", "# In aider")])
def test_python_snippets_read_the_secret_from_the_environment(home, monkeypatch, capsys,
                                                              platform, first):
    out = _connect(monkeypatch, capsys, platform, RELAY_A, SAVED)
    tree = ast.parse(_script(out, first))
    reads = [n for n in ast.walk(tree) if isinstance(n, ast.Subscript)
             and ast.unparse(n) == "os.environ['QUORUS_RELAY_SECRET']"]
    assert reads, "the client must get its secret from QUORUS_RELAY_SECRET"
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "QuorusClient"]
    assert calls and ast.unparse(calls[0].args[1]) == "os.environ['QUORUS_RELAY_SECRET']"
    assert "relay_secret saved in" in out and str(home / "config.json") in "".join(
        out.split())


def test_a_shell_secret_says_where_it_comes_from(home, monkeypatch, capsys):
    out = _connect(monkeypatch, capsys, "ollama", RELAY_B, SHELL)
    assert 'export QUORUS_RELAY_SECRET="$RELAY_SECRET"' in out


def test_every_curl_line_expands_the_variable(home, monkeypatch, capsys):
    out = _connect(monkeypatch, capsys, "http", RELAY_A, SAVED)
    bearers = re.findall(r'Authorization: Bearer [^"\s]*', out)
    assert len(bearers) == 5
    assert all(b == "Authorization: Bearer $QUORUS_RELAY_SECRET" for b in bearers)
    assert out.count('-H "Authorization: Bearer $QUORUS_RELAY_SECRET"') == 5
