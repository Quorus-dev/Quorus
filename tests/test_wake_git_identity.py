"""A harness's own commits must never guess the host's Git identity."""

import asyncio
import os
import subprocess

import pytest

from tests.test_reflexd_wake_contract import reflexd, wake


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
@pytest.mark.parametrize("resume", [None, "previous-session"])
@pytest.mark.parametrize("inherited_identity", [False, True])
def test_harness_commits_as_its_agent_with_strict_git_identity(
    tmp_path, monkeypatch, harness, resume, inherited_identity,
):
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    for key, value in {
        "GIT_AUTHOR_NAME": "Host User", "GIT_AUTHOR_EMAIL": "host@machine.invalid",
        "GIT_COMMITTER_NAME": "Host User", "GIT_COMMITTER_EMAIL": "host@machine.invalid",
        "EMAIL": "host@machine.invalid",
    }.items():
        if inherited_identity:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("REFLEXD_STUB_REPLY", raising=False)
    monkeypatch.setattr(reflexd, "is_stub_mode", lambda: False)
    participant = f"lead-{harness}"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    adapter = reflexd.HeadlessAdapter()
    adapter.wake_spec = wake.quorus_mcp_spec(
        relay_url="http://unused.invalid", api_key="test-key", participant=participant,
        legacy=False,
    )
    adapter.mcp_config_path = tmp_path / "wake.json"
    adapter.model = "paid/test-model"  # no free-model discovery or real harness
    calls = []

    async def commit_from_harness(argv, *, env, **kwargs):
        # Replace only the vendor process: Git really executes with the exact
        # environment the adapter supplies, with no -c identity or daemon commit.
        subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull,
             "commit", "-q", "--allow-empty", "-m", "test: agent commit"],
            cwd=repo, env=env, check=True, capture_output=True, text=True,
        )
        identity = subprocess.check_output(
            ["git", "log", "-1", "--format=%an <%ae>|%cn <%ce>"],
            cwd=repo, env=env, text=True,
        ).strip()
        expected = f"{participant} <{participant}@agents.quorus.local>"
        assert identity == f"{expected}|{expected}"
        calls.append(argv)
        return "committed"

    monkeypatch.setattr(adapter, "_run_subprocess", commit_from_harness)
    assert asyncio.run(adapter.run(
        harness, context="make a commit", cwd=repo, resume=resume,
    )) == "committed"
    assert len(calls) == 1
