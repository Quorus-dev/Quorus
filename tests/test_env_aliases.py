"""The CLI honors QUORUS_* relay settings (FRICTION 2026-11-02) without ever
sending one relay's credential to another, or leaking into child processes."""
from __future__ import annotations

import io

from quorus_cli import env_aliases

A = "https://a.test"
B = "https://b.test"


def fill(env):
    err = io.StringIO()
    return env_aliases.aliases(env, err), err.getvalue()


def test_a_full_quorus_set_fills_the_cli_names():
    env = {"QUORUS_RELAY_URL": A, "QUORUS_RELAY_SECRET": "s",
           "QUORUS_API_KEY": "quorus_pk_a", "QUORUS_INSTANCE_NAME": "arav"}
    got, err = fill(env)
    assert got == {"RELAY_URL": A, "RELAY_SECRET": "s", "API_KEY": "quorus_pk_a",
                   "INSTANCE_NAME": "arav"} and err == ""


def test_a_different_relay_url_blocks_every_quorus_credential():
    """Reviewer B1: A's key must never reach B's /v1/auth/token."""
    env = {"RELAY_URL": B, "QUORUS_RELAY_URL": A, "QUORUS_API_KEY": "quorus_pk_forA",
           "QUORUS_RELAY_SECRET": "secret-for-a"}
    got, err = fill(env)
    assert got == {}
    assert "different relays" in err and "quorus_pk_forA" not in err
    assert "secret-for-a" not in err


def test_same_relay_spelled_differently_still_fills_credentials():
    env = {"RELAY_URL": "https://a.test:443/", "QUORUS_RELAY_URL": A,
           "QUORUS_API_KEY": "quorus_pk_a"}
    got, err = fill(env)
    assert got == {"API_KEY": "quorus_pk_a"} and err == ""


def test_lone_secret_or_key_or_name_is_ignored():
    """Reviewer A1 and FRICTION #104: a shell-wide export stays inert."""
    for name in ("QUORUS_RELAY_SECRET", "QUORUS_API_KEY", "QUORUS_INSTANCE_NAME"):
        assert fill({name: "x"}) == ({}, "")


def test_unprefixed_credential_wins_and_conflict_names_no_values():
    env = {"QUORUS_RELAY_URL": A, "RELAY_URL": A, "QUORUS_RELAY_SECRET": "secret-a",
           "RELAY_SECRET": "secret-b"}
    got, err = fill(env)
    assert got == {}
    assert "QUORUS_RELAY_SECRET and RELAY_SECRET" in err
    assert "secret-a" not in err and "secret-b" not in err


def test_blank_values_are_ignored():
    assert fill({"QUORUS_RELAY_URL": "  ", "QUORUS_INSTANCE_NAME": ""}) == ({}, "")


def test_applied_removes_its_copies_so_children_inherit_nothing():
    """Reviewer A2: reflexd and agents copy os.environ; the aliases must be gone."""
    env = {"QUORUS_RELAY_URL": A, "QUORUS_API_KEY": "quorus_pk_a", "PATH": "/bin"}
    with env_aliases.applied(env, io.StringIO()) as names:
        assert env["RELAY_URL"] == A and env["API_KEY"] == "quorus_pk_a"
        assert names == ["API_KEY", "RELAY_URL"]
    assert env == {"QUORUS_RELAY_URL": A, "QUORUS_API_KEY": "quorus_pk_a", "PATH": "/bin"}


def test_applied_restores_even_when_loading_fails():
    env = {"QUORUS_RELAY_URL": A}
    try:
        with env_aliases.applied(env, io.StringIO()):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert "RELAY_URL" not in env


def test_applied_never_touches_a_preexisting_unprefixed_value():
    env = {"QUORUS_RELAY_URL": A, "RELAY_URL": A, "INSTANCE_NAME": "keep"}
    with env_aliases.applied(env, io.StringIO()):
        pass
    assert env == {"QUORUS_RELAY_URL": A, "RELAY_URL": A, "INSTANCE_NAME": "keep"}
