"""A saved credential only ever goes to the relay it was saved for.

Reproduced on the 0.5.1 wheel: ~/.quorus/config.json held relay A's api_key,
QUORUS_RELAY_URL pointed at relay B, and quorus-mcp POSTed A's key to B's
/v1/auth/token. The same held for the legacy RELAY_URL override and for
`quorus join --relay B` without --secret.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from quorus.config import load_config, same_relay

RELAY_A = "http://127.0.0.1:8080"
RELAY_B = "https://relay-b.example"
KEY_A = "mct_aa_bb"
SECRET_A = "secret-for-a"
_ENV = ("QUORUS_RELAY_URL", "QUORUS_RELAY_SECRET", "QUORUS_API_KEY", "QUORUS_INSTANCE_NAME",
        "QUORUS_PROFILE", "RELAY_URL", "RELAY_SECRET", "API_KEY", "INSTANCE_NAME",
        "MCP_TUNNEL_CONFIG_DIR")


@pytest.fixture
def saved(tmp_path, monkeypatch):
    """A profile saved for relay A, with both an API key and a shared secret."""
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config.json").write_text(json.dumps(
        {"relay_url": RELAY_A, "api_key": KEY_A, "relay_secret": SECRET_A,
         "instance_name": "alice"}))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def no_network(monkeypatch):
    """Record (and refuse) every outbound request, to assert zero sends."""
    sent: list[tuple[str, str]] = []

    def refuse(method):
        def call(url, *a, **kw):
            sent.append((method, str(url)))
            raise AssertionError(f"unexpected {method} {url}")
        return call

    for method in ("get", "post", "request"):
        monkeypatch.setattr(httpx, method, refuse(method))
    return sent


# ── core config resolution ───────────────────────────────────────────────


def test_legacy_relay_url_override_withholds_saved_credentials(saved, monkeypatch):
    monkeypatch.setenv("RELAY_URL", RELAY_B)
    cfg = load_config()
    assert cfg["relay_url"] == RELAY_B
    assert cfg["api_key"] == "" and cfg["relay_secret"] == ""
    assert cfg["credentials_withheld"] and cfg["saved_relay_url"] == RELAY_A


@pytest.mark.parametrize("url", [None, RELAY_A, RELAY_A + "/", "HTTP://127.0.0.1:8080"])
def test_matching_relay_keeps_saved_credentials(saved, monkeypatch, url):
    if url:
        monkeypatch.setenv("RELAY_URL", url)
    cfg = load_config()
    assert cfg["api_key"] == KEY_A and cfg["relay_secret"] == SECRET_A
    assert not cfg["credentials_withheld"]


def test_env_credentials_for_another_relay_still_work(saved, monkeypatch):
    monkeypatch.setenv("RELAY_URL", RELAY_B)
    monkeypatch.setenv("API_KEY", "mct_bb_cc")
    cfg = load_config()
    assert cfg["api_key"] == "mct_bb_cc" and cfg["relay_secret"] == ""


def test_same_relay():
    assert same_relay("https://h.example", "https://h.example:443/")
    assert not same_relay("https://h.example/team-a", "https://h.example/team-b")
    assert not same_relay("http://h.example", "https://h.example")
    assert not same_relay("http://h:99999", "http://h")


# ── quorus-mcp ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("override", ["QUORUS_RELAY_URL", "RELAY_URL"])
def test_mcp_refuses_before_sending_anything(saved, no_network, monkeypatch, override):
    from quorus_mcp import runtime

    monkeypatch.setenv(override, RELAY_B)
    monkeypatch.setenv("QUORUS_API_KEY", "")  # a plugin's blank field
    with pytest.raises(SystemExit, match=f"no credential saved for {RELAY_B}"):
        runtime.load_runtime_config()
    assert no_network == []


def test_mcp_uses_explicit_credentials_for_the_new_relay(saved, monkeypatch):
    from quorus_mcp import runtime

    monkeypatch.setenv("QUORUS_RELAY_URL", RELAY_B)
    monkeypatch.setenv("QUORUS_RELAY_SECRET", "secret-for-b")
    settings = runtime.load_runtime_config()
    assert settings["relay_secret"] == "secret-for-b" and settings["api_key"] == ""


def test_mcp_matching_relay_works(saved, monkeypatch):
    from quorus_mcp import runtime

    monkeypatch.setenv("QUORUS_RELAY_URL", RELAY_A + "/")
    settings = runtime.load_runtime_config()
    assert settings["api_key"] == KEY_A


def test_mcp_profile_is_bound_the_same_way(tmp_path, monkeypatch, no_network):
    from quorus_mcp import runtime

    from quorus.config import ConfigManager

    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    ConfigManager(profile="work").save({"relay_url": RELAY_A, "api_key": KEY_A,
                                        "instance_name": "alice"})
    monkeypatch.setenv("QUORUS_PROFILE", "work")
    assert runtime.load_runtime_config()["api_key"] == KEY_A
    monkeypatch.setenv("QUORUS_RELAY_URL", RELAY_B)
    with pytest.raises(SystemExit, match="no credential saved"):
        runtime.load_runtime_config()
    assert no_network == []


# ── quorus join ──────────────────────────────────────────────────────────


def _join_args(**kw):
    base = dict(token="", relay_url=None, secret=None, api_key=None, room="dev",
                name="alice")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def cli_saved(monkeypatch):
    from quorus_cli import cli

    monkeypatch.setattr(cli, "RELAY_URL", RELAY_A)
    monkeypatch.setattr(cli, "RELAY_SECRET", SECRET_A)
    monkeypatch.setattr(cli, "API_KEY", "")
    monkeypatch.setattr(cli, "_config", {"relay_url": RELAY_A, "saved_relay_url": RELAY_A,
                                         "relay_secret": SECRET_A, "api_key": ""})
    return cli


def test_join_with_another_relay_never_sends_the_saved_secret(cli_saved, no_network,
                                                              capsys):
    with pytest.raises(SystemExit) as exit_:
        cli_saved._cmd_join(_join_args(relay_url=RELAY_B))
    assert exit_.value.code == 3
    assert no_network == []
    assert "no credential saved for" in capsys.readouterr().out.replace("\n", " ")


# ── the wake daemon ──────────────────────────────────────────────────────


def _reflexd():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "reflexd.py"
    spec = importlib.util.spec_from_file_location("reflexd_relay_bound", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reflexd_relay_bound"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("override", ["REFLEXD_RELAY_URL", "RELAY_URL"])
def test_reflexd_never_takes_the_saved_key_to_another_relay(saved, monkeypatch, override):
    reflexd = _reflexd()
    monkeypatch.setenv(override, RELAY_B)
    with pytest.raises(SystemExit, match=f"no credential saved for {RELAY_B}"):
        reflexd.ReflexdConfig.from_env()
    monkeypatch.setenv("REFLEXD_API_KEY", "mct_bb_cc")
    assert reflexd.ReflexdConfig.from_env().api_key == "mct_bb_cc"
    monkeypatch.delenv("REFLEXD_API_KEY")
    monkeypatch.setenv(override, RELAY_A + "/")
    assert reflexd.ReflexdConfig.from_env().api_key == KEY_A


# ── servers a client starts read config without writing it (codex) ───────


def _legacy_flat(tmp_path, monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"relay_url": RELAY_A, "api_key": KEY_A,
                                "instance_name": "alice"}))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path))
    return path


def _snapshot(root):
    return sorted((str(p.relative_to(root)), p.read_bytes()) for p in root.rglob("*")
                  if p.is_file())


def test_mcp_startup_never_migrates_or_writes_config(tmp_path, monkeypatch):
    from quorus_mcp import runtime

    _legacy_flat(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)
    assert runtime.load_runtime_config()["api_key"] == KEY_A
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "profiles").exists()


def test_reflexd_startup_never_migrates_or_writes_config(tmp_path, monkeypatch):
    reflexd = _reflexd()
    _legacy_flat(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)
    assert reflexd.ReflexdConfig.from_env().api_key == KEY_A
    assert _snapshot(tmp_path) == before


def test_cli_load_still_migrates(tmp_path, monkeypatch):
    path = _legacy_flat(tmp_path, monkeypatch)
    assert load_config()["api_key"] == KEY_A  # explicit `quorus` commands migrate
    assert (tmp_path / "profiles" / "default.json").exists()
    assert "relay_url" not in json.loads(path.read_text())


# ── quorus init switching a profile to another relay (codex, 84bbb9b2) ──────


@pytest.fixture
def hub_on_relay_a(tmp_path, monkeypatch):
    from pathlib import Path
    from unittest.mock import MagicMock

    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(tmp_path / ".quorus"))
    monkeypatch.setattr("quorus.cli.shutil.which", lambda _: None)
    monkeypatch.setattr("quorus.cli._is_platform_installed", lambda p: False)
    monkeypatch.setattr("quorus.cli.httpx.get", lambda *a, **kw: MagicMock(status_code=200))
    cfg = tmp_path / ".quorus"
    (cfg / "profiles").mkdir(parents=True)
    (cfg / "config.json").write_text(json.dumps({"current": "work", "profiles": ["work"]}))
    (cfg / "profiles" / "work.json").write_text(json.dumps({
        "instance_name": "alice", "relay_url": RELAY_A, "api_key": KEY_A,
        "agent_api_keys": {"alice-claude": "mct_agent_for_a"}}))
    return cfg


def _init(relay_url):
    from unittest.mock import MagicMock

    from quorus_cli.cli import _cmd_init

    args = MagicMock()
    args.name, args.relay_url, args.secret, args.api_key = "alice", relay_url, "secret-b", None
    _cmd_init(args)


def test_init_to_another_relay_drops_every_saved_credential(hub_on_relay_a):
    from quorus_cli.cli import _resolve_manager_participants

    _init(RELAY_B)
    profile = json.loads((hub_on_relay_a / "profiles" / "work.json").read_text())
    assert profile["relay_url"] == RELAY_B and profile["relay_secret"] == "secret-b"
    assert "agent_api_keys" not in profile and "api_key" not in profile
    _names, keys, relay = _resolve_manager_participants(None)
    assert relay == RELAY_B and "mct_agent_for_a" not in keys.values()


def test_init_on_the_same_relay_keeps_its_agent_keys(hub_on_relay_a):
    _init(RELAY_A + "/")
    profile = json.loads((hub_on_relay_a / "profiles" / "work.json").read_text())
    assert profile["agent_api_keys"] == {"alice-claude": "mct_agent_for_a"}


# ── credential exchanges never follow a redirect (codex, c0a0a097) ──────────


def test_cli_token_exchange_never_follows_a_redirect_to_another_relay(monkeypatch):
    from quorus_cli import cli

    seen_b: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "relay-a.example":
            return httpx.Response(307, headers={"location": "https://relay-b.example/steal"})
        seen_b.append(request.content)
        return httpx.Response(200, json={"token": "x"})

    transport = httpx.MockTransport(handler)

    def post(url, **kw):
        with httpx.Client(transport=transport) as c:
            return c.post(url, **kw)

    monkeypatch.setattr(cli.httpx, "post", post)
    monkeypatch.setattr(cli, "RELAY_URL", "https://relay-a.example")
    monkeypatch.setattr(cli, "API_KEY", KEY_A)
    monkeypatch.setattr(cli, "_cached_jwt", None)
    with pytest.raises(SystemExit) as exit_:
        cli._auth_headers()
    assert exit_.value.code == 3
    assert seen_b == []  # A's key never reached B


@pytest.mark.parametrize("argv", [["rooms"], ["say", "dev", "hi"], ["inbox"],
                                  ["join", "--room", "dev"]])
def test_commands_against_a_redirecting_relay_send_the_key_nowhere(monkeypatch, capsys,
                                                                   argv):
    """Arav's live check: relay A answers 307 → B for everything; B must get no key."""
    import sys

    from quorus_cli import cli

    reached_b: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "relay-a.example":
            return httpx.Response(307, headers={"location": "https://relay-b.example"
                                                + request.url.path})
        reached_b.append(f"{request.method} {request.url.path} "
                         f"{request.headers.get('authorization', '')} {request.content!r}")
        return httpx.Response(200, json={"token": "x", "rooms": [], "messages": []})

    transport = httpx.MockTransport(handler)

    def sync(method):
        def call(url, **kw):
            with httpx.Client(transport=transport) as c:
                return c.request(method, url, **kw)
        return call

    monkeypatch.setattr(cli.httpx, "post", sync("POST"))
    monkeypatch.setattr(cli.httpx, "get", sync("GET"))
    monkeypatch.setattr(cli, "_get_client",
                        lambda: httpx.AsyncClient(transport=transport, follow_redirects=True))
    monkeypatch.setattr(cli, "RELAY_URL", "https://relay-a.example")
    monkeypatch.setattr(cli, "API_KEY", KEY_A)
    monkeypatch.setattr(cli, "RELAY_SECRET", "")
    monkeypatch.setattr(cli, "_cached_jwt", None)
    monkeypatch.setattr(sys, "argv", ["quorus", *argv])
    with pytest.raises(SystemExit):
        cli.main()
    assert not any(KEY_A in hit for hit in reached_b), reached_b


def test_no_credential_post_follows_redirects():
    """Any POST that carries a key or invite in its body keeps follow_redirects off."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in [*(root / "packages").rglob("*.py"), *(root / "quorus").rglob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            url = ast.unparse(node.args[0])
            if not any(p in url for p in ("/v1/auth/token", "/v1/auth/signup")):
                continue
            for kw in node.keywords:
                if kw.arg == "follow_redirects" and ast.unparse(kw.value) != "False":
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []
