"""Quorus Cloud: the hosted relay (quorus/cloud.py), its caps
(quorus/services/cloud_limits.py) and `quorus cloud` (quorus_cli/cloud_cmd.py)."""
from __future__ import annotations

import argparse
import re

import httpx
import pytest
from fastapi import HTTPException
from quorus_cli import cloud_cmd

from quorus import cloud
from quorus.backends.memory import InMemoryRateLimitBackend
from quorus.profiles import ProfileManager
from quorus.services import cloud_limits
from quorus.services.rate_limit_svc import RateLimitService

CLOUD = "https://cloud.example.test"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}[a-z0-9]$")  # signup's workspace rule


def _resp(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {},
                          request=httpx.Request("POST", f"{CLOUD}/v1/auth/signup"))


# ── where the hosted relay is, and where a code is looked up ────────────────────


def test_public_relay_defaults_to_quorus_cloud_and_can_be_turned_off(monkeypatch):
    monkeypatch.delenv("QUORUS_PUBLIC_RELAY")
    assert cloud.public_relay() == cloud.DEFAULT_PUBLIC_RELAY
    for off in ("off", "", "0", "none"):
        monkeypatch.setenv("QUORUS_PUBLIC_RELAY", off)
        assert cloud.public_relay() == ""
    monkeypatch.setenv("QUORUS_PUBLIC_RELAY", "https://relay.acme.test/")
    assert cloud.public_relay() == "https://relay.acme.test"


def test_a_code_is_looked_up_on_this_machines_relay_then_the_cloud(monkeypatch):
    monkeypatch.setenv("QUORUS_PUBLIC_RELAY", CLOUD)
    assert cloud.code_relays(None, "http://127.0.0.1:8787/") == [
        "http://127.0.0.1:8787", CLOUD]
    assert cloud.code_relays(None, None) == [CLOUD]
    assert cloud.code_relays(None, CLOUD) == [CLOUD]
    # --relay is the only place an explicit address is looked up.
    assert cloud.code_relays("https://their.relay/", "http://127.0.0.1:8787") == [
        "https://their.relay"]
    monkeypatch.setenv("QUORUS_PUBLIC_RELAY", "off")
    assert cloud.code_relays(None, None) == []


@pytest.mark.parametrize("name", ["arav", "Alice_B", "x", "---", "a" * 64])
def test_workspace_names_pass_signups_rule_and_dont_collide(name):
    first, second = cloud.workspace_for(name), cloud.workspace_for(name)
    assert _SLUG.match(first) and len(first) <= 64
    assert first != second


# ── signup ──────────────────────────────────────────────────────────────────────


def test_signup_retries_a_taken_workspace_and_returns_the_key(monkeypatch):
    sent: list[dict] = []
    answers = iter([_resp(409), _resp(200, {"api_key": "mct_synthetic"})])

    def post(url, **kw):
        sent.append({"url": url, **kw})
        return next(answers)

    monkeypatch.setattr(cloud.httpx, "post", post)
    account = cloud.signup(CLOUD + "/", "arav")
    assert account.api_key == "mct_synthetic" and account.relay_url == CLOUD
    assert [s["url"] for s in sent] == [f"{CLOUD}/v1/auth/signup"] * 2
    assert sent[0]["json"]["workspace"] != sent[1]["json"]["workspace"]
    assert sent[1]["json"] == {"name": "arav", "workspace": account.workspace}
    # Only a local-setup client gets the key back in the body.
    assert sent[1]["headers"] == {"X-Quorus-Setup-Local": "1"}


@pytest.mark.parametrize("answer, said", [
    (_resp(400, {"detail": "This relay has no accounts (it runs without a database)"}),
     "doesn't take signups"),
    (_resp(503, {"detail": "This relay is full"}), "This relay is full"),
    (_resp(200, {"api_key": None}), "returned no key"),
])
def test_signup_failures_say_what_happened(monkeypatch, answer, said):
    monkeypatch.setattr(cloud.httpx, "post", lambda url, **kw: answer)
    with pytest.raises(cloud.SignupError, match=said):
        cloud.signup(CLOUD, "arav")


def test_an_unreachable_relay_is_named(monkeypatch):
    def post(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(cloud.httpx, "post", post)
    with pytest.raises(cloud.SignupError, match="can't reach https://cloud.example.test"):
        cloud.signup(CLOUD, "arav")


def test_an_invite_joins_the_inviters_workspace(monkeypatch):
    sent: list[dict] = []

    def post(url, **kw):
        sent.append(kw["json"])
        return _resp(200, {"api_key": "mct_bob"})

    monkeypatch.setattr(cloud.httpx, "post", post)
    assert cloud.join_workspace(CLOUD, "bob", "tenant-1", "invite-jwt") == "mct_bob"
    assert sent[0]["join_tenant_id"] == "tenant-1" and sent[0]["invite_token"] == "invite-jwt"
    monkeypatch.setattr(cloud.httpx, "post", lambda url, **kw: _resp(409))
    with pytest.raises(cloud.SignupError, match="@bob is taken"):
        cloud.join_workspace(CLOUD, "bob", "tenant-1", "invite-jwt")


# ── caps on a public relay ──────────────────────────────────────────────────────


def _limiter() -> RateLimitService:
    return RateLimitService(InMemoryRateLimitBackend(), window=60, max_count=60)


async def test_caps_are_off_unless_set(monkeypatch):
    for var in ("SIGNUPS_PER_IP_PER_DAY", "MAX_TENANTS", "MAX_ROOMS_PER_TENANT"):
        monkeypatch.delenv(var, raising=False)

    async def count() -> int:
        raise AssertionError("no count query when MAX_TENANTS is unset")

    limiter = _limiter()
    for _ in range(50):
        await cloud_limits.check_new_workspace(limiter, "203.0.113.5", count)


async def test_one_address_gets_its_daily_workspaces_then_waits(monkeypatch):
    monkeypatch.setenv("SIGNUPS_PER_IP_PER_DAY", "3")

    async def count() -> int:
        return 0

    limiter = _limiter()
    for _ in range(3):
        await cloud_limits.check_new_workspace(limiter, "203.0.113.5", count)
    with pytest.raises(HTTPException) as exc:
        await cloud_limits.check_new_workspace(limiter, "203.0.113.5", count)
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"] == "86400"
    await cloud_limits.check_new_workspace(limiter, "198.51.100.7", count)


async def test_a_full_relay_says_so(monkeypatch):
    monkeypatch.setenv("MAX_TENANTS", "100")

    async def count() -> int:
        return 100

    with pytest.raises(HTTPException) as exc:
        await cloud_limits.check_new_workspace(_limiter(), "203.0.113.5", count)
    assert exc.value.status_code == 503 and "full" in exc.value.detail


@pytest.mark.parametrize("bad", ["-4", "lots", ""])
def test_a_malformed_cap_is_off(monkeypatch, bad):
    monkeypatch.setenv("MAX_TENANTS", bad)
    assert cloud_limits.env_cap("MAX_TENANTS") == 0


class _Rooms:
    def __init__(self, n: int) -> None:
        self.n = n

    async def list_all(self, tenant_id: str) -> list:
        return [{}] * self.n


async def test_a_workspace_can_hold_only_so_many_rooms(monkeypatch):
    monkeypatch.setenv("MAX_ROOMS_PER_TENANT", "2")
    await cloud_limits.check_room_quota(_Rooms(1), "t")
    with pytest.raises(HTTPException) as exc:
        await cloud_limits.check_room_quota(_Rooms(2), "t")
    assert exc.value.status_code == 403 and "quorus destroy" in exc.value.detail


def test_the_signup_route_checks_caps_after_the_name_and_only_for_new_workspaces():
    """A taken name (which clients retry) mustn't spend the daily allowance, and
    joining someone's workspace never counts."""
    import inspect

    from quorus.auth import routes

    source = inspect.getsource(routes.signup)
    joined, created = source.split("# Check workspace uniqueness")
    assert "check_new_workspace" not in joined
    assert created.index("already taken") < created.index("check_new_workspace")


# ── `quorus cloud` ──────────────────────────────────────────────────────────────


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *parts, **kw) -> None:
        self.lines.append(" ".join(str(p) for p in parts))


class _UI:
    def error(self, message, hint=None):
        raise SystemExit(f"{message} | {hint}")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUORUS_CONFIG_DIR", str(home / ".quorus"))
    monkeypatch.setenv("QUORUS_PUBLIC_RELAY", CLOUD)
    return home / ".quorus"


def _cloud(monkeypatch, *, name="arav", current="", key="mct_synthetic"):
    signups: list[tuple] = []

    def signup(relay_url, who):
        signups.append((relay_url, who))
        return cloud.Account(relay_url, who, "arav-abc123", key)

    monkeypatch.setattr(cloud_cmd, "signup", signup)
    inits: list[argparse.Namespace] = []
    console = _Console()
    cloud_cmd.run(argparse.Namespace(name=name, relay=None), console=console, ui=_UI(),
                  current_name=current, init=inits.append)
    return signups, inits, "\n".join(console.lines)


def test_on_a_fresh_machine_quorus_cloud_runs_the_full_setup(cfg, monkeypatch):
    signups, [init], out = _cloud(monkeypatch)
    assert signups == [(CLOUD, "arav")]
    assert (init.name, init.relay_url, init.api_key) == ("arav", CLOUD, "mct_synthetic")
    assert init.no_smoke and init.no_launchd and not init.secret
    # init prints create / agent add itself; this adds only the invite.
    assert "quorus share myroom" in out and "quorus create myroom" not in out


def test_an_existing_setup_is_kept_and_the_cloud_is_its_own_profile(cfg, monkeypatch):
    pm = ProfileManager(cfg)
    local = {"instance_name": "arav", "relay_url": "http://127.0.0.1:8787",
             "relay_secret": "synthetic-local-secret"}
    pm.save("default", local)
    pm.set_current("default")
    signups, inits, out = _cloud(monkeypatch, name=None, current="arav")
    assert signups == [(CLOUD, "arav")] and inits == []
    assert pm.current() == "default" and pm.get("default") == local
    [slug] = [s for s in pm.list() if s != "default"]
    saved = pm.get(slug)
    assert saved["relay_url"] == CLOUD and saved["api_key"] == "mct_synthetic"
    assert "relay_secret" not in saved
    assert f"quorus profile use {slug}" in out
    assert "mct_synthetic" not in out


def test_running_it_again_reuses_the_account(cfg, monkeypatch):
    pm = ProfileManager(cfg)
    pm.save("default", {"instance_name": "arav", "relay_url": "http://127.0.0.1:8787",
                        "relay_secret": "synthetic-local-secret"})
    pm.save("cloud", {"instance_name": "arav", "relay_url": CLOUD, "api_key": "mct_old"})
    pm.set_current("default")
    signups, inits, out = _cloud(monkeypatch)
    assert signups == [] and inits == []
    assert "already on" in out and "quorus profile use cloud" in out


def test_no_name_no_signup(cfg, monkeypatch):
    with pytest.raises(SystemExit, match="quorus cloud --name"):
        _cloud(monkeypatch, name=None, current="")
