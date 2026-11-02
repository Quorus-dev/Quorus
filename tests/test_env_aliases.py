"""The CLI honors QUORUS_* relay settings (FRICTION 2026-11-02)."""
from __future__ import annotations

import io

from quorus_cli import env_aliases


def run(env):
    err = io.StringIO()
    applied = env_aliases.apply(env, err)
    return applied, err.getvalue()


def test_prefixed_names_fill_the_cli_names():
    env = {"QUORUS_RELAY_URL": "https://relay.test", "QUORUS_RELAY_SECRET": "s",
           "QUORUS_INSTANCE_NAME": "arav"}
    applied, err = run(env)
    assert env["RELAY_URL"] == "https://relay.test"
    assert env["RELAY_SECRET"] == "s" and env["INSTANCE_NAME"] == "arav"
    assert set(applied) == {"RELAY_URL", "RELAY_SECRET", "INSTANCE_NAME"} and err == ""


def test_unprefixed_wins_and_a_conflict_is_named_without_values():
    env = {"QUORUS_RELAY_URL": "https://a.test", "RELAY_URL": "https://b.test",
           "QUORUS_RELAY_SECRET": "secret-a", "RELAY_SECRET": "secret-b"}
    applied, err = run(env)
    assert env["RELAY_URL"] == "https://b.test" and env["RELAY_SECRET"] == "secret-b"
    assert applied == []
    assert "QUORUS_RELAY_URL and RELAY_URL" in err
    assert "secret-a" not in err and "secret-b" not in err


def test_same_value_in_both_is_silent():
    env = {"QUORUS_RELAY_URL": "https://a.test", "RELAY_URL": "https://a.test"}
    assert run(env) == ([], "")


def test_shell_wide_api_key_alone_is_ignored():
    env = {"QUORUS_API_KEY": "quorus_pk_x"}
    applied, _ = run(env)
    assert "API_KEY" not in env and applied == []


def test_api_key_applies_with_its_relay():
    env = {"QUORUS_RELAY_URL": "https://a.test", "QUORUS_API_KEY": "quorus_pk_x"}
    run(env)
    assert env["API_KEY"] == "quorus_pk_x"


def test_blank_values_are_ignored():
    env = {"QUORUS_RELAY_URL": "  ", "QUORUS_INSTANCE_NAME": ""}
    assert run(env) == ([], "") and "RELAY_URL" not in env
