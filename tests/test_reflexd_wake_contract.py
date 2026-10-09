"""Regression tests for the wake contract (live two-agent run, 2026-10-08).

Each test pins a failure observed with real claude 2.1.295 + codex 0.160.1
woken by reflexd against a local relay. See scripts/reflexd_wake.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _REPO_ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
wake = reflexd.reflexd_wake

# Verbatim shape of `codex exec --json` on codex-cli 0.160.1.
CODEX_REAL_OUTPUT = "\n".join([
    '{"type":"thread.started","thread_id":"01a11e3c-e1b6-7fc2-9686-00460ca4d259"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"looking"}}',
    '{"type":"item.completed","item":{"id":"item_1","type":"command_execution","text":"ls"}}',
    '{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"pong"}}',
    '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
])


def test_codex_parser_reads_real_agent_message_shape() -> None:
    text, tid = reflexd._parse_codex_stream(CODEX_REAL_OUTPUT)
    # The old parser returned "" here, so every codex reply became the
    # "[reflexd] (no reply)" sentinel in the room.
    assert text == "pong"  # last agent message, not narration or tool items
    assert tid == "01a11e3c-e1b6-7fc2-9686-00460ca4d259"


def test_codex_parser_legacy_delta_fallback() -> None:
    out = '{"thread_id":"t1"}\n{"delta":"he"}\n{"delta":"llo"}'
    assert reflexd._parse_codex_stream(out) == ("hello", "t1")


def test_agent_question_mark_does_not_wake_others() -> None:
    # Live: claude's reply ended "...post this for me?" and woke codex.
    res = reflexd.classify_message(
        content="couldn't reach the room. Can someone post this for me?",
        sender="qt-claude", self_name="qt-codex",
    )
    assert res.action == "IGNORE"
    human = reflexd.classify_message(
        content="can anyone check the build?", sender="arav", self_name="qt-codex",
    )
    assert human.action == "RESPOND"


def test_agent_mention_still_hands_off() -> None:
    res = reflexd.classify_message(
        content="Fixed in 31a3ce0. @qt-codex please review that commit.",
        sender="qt-claude", self_name="qt-codex",
    )
    assert res.action == "RESPOND" and res.kind == "mention"


def test_trailing_agent_chain_counts_until_human() -> None:
    hist = [{"from_name": n} for n in ("qt-claude", "arav", "qt-claude", "qt-codex", "qt-claude")]
    assert wake.trailing_agent_chain(hist, reflexd.is_agent_participant) == 3
    assert wake.trailing_agent_chain([], reflexd.is_agent_participant) == 0


def test_wake_flags_bind_identity_without_leaking_secret(tmp_path: Path) -> None:
    spec = wake.quorus_mcp_spec(
        relay_url="http://127.0.0.1:1", api_key="sk-SECRET", participant="qt-codex",
        legacy=False,
    )
    codex = wake.codex_wake_flags(spec)
    assert not any("sk-SECRET" in a for a in codex), "secret must never be in argv"
    assert any("QUORUS_INSTANCE_NAME" in a and "qt-codex" in a for a in codex)
    assert any("env_vars" in a and "QUORUS_API_KEY" in a for a in codex)

    cfg = wake.write_claude_mcp_config(spec, tmp_path / "m.json")
    claude = wake.claude_wake_flags(cfg)
    assert claude == ["--mcp-config", str(cfg)]
    assert (cfg.stat().st_mode & 0o777) == 0o600
    server = json.loads(cfg.read_text())["mcpServers"]["quorus"]
    assert server["env"]["QUORUS_API_KEY"] == "sk-SECRET"
    assert server["env"]["SSE_ENABLED"] == "false"

    env = wake.wake_env(spec)
    assert env["QUORUS_INSTANCE_NAME"] == "qt-codex"
    assert env["PATH"].split(":")[0] == str(Path(sys.executable).parent)


def test_codex_sandbox_is_owner_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "workspace-write")
    flags = wake.codex_wake_flags(None)
    assert flags[:2] == ["-s", "workspace-write"]
    monkeypatch.setattr(wake, "CODEX_SANDBOX", "danger-full-access")
    assert "-s" not in wake.codex_wake_flags(None)  # never full access


def test_wake_flags_do_not_widen_permissions_by_default() -> None:
    spec = wake.quorus_mcp_spec(relay_url="u", api_key="k", participant="a-claude", legacy=True)
    flags = " ".join(wake.codex_wake_flags(spec) + wake.claude_wake_flags(Path("/x")))
    for forbidden in ("--permission-mode", "--sandbox", "-s ", "bypass", "danger",
                      "--allowedTools", "--dangerously"):
        assert forbidden not in flags


def test_codex_extra_flags_precede_resume_subcommand() -> None:
    argv = reflexd.build_codex_argv("hi", resume="tid", extra=["-c", "x=1"])
    assert argv.index("-c") < argv.index("resume") < argv.index("--")


def _daemon(tmp_path: Path, name: str = "qt-claude") -> Any:
    cfg = reflexd.ReflexdConfig(
        relay_url="http://test", api_key="k", participant_name=name,
        runtime_dir=tmp_path, log_path=tmp_path / "l.log",
    )
    return reflexd.Reflexd(cfg)


def test_prompt_lets_mentioned_agent_work(tmp_path: Path) -> None:
    d = _daemon(tmp_path)
    env = {"from_name": "arav", "room": "r", "content": "@qt-claude fix the bug"}
    tri = reflexd.classify_message(content=env["content"], sender="arav", self_name="qt-claude")
    p = d.build_prompt(env, [], triage=tri, teammates=["qt-claude", "qt-codex"])
    assert "do not run any tools" not in p.lower()
    assert "do the work now" in p.lower()
    assert "@qt-codex" in p  # teammates named for handoffs


def test_daemon_wires_wake_spec_into_adapter(tmp_path: Path) -> None:
    d = _daemon(tmp_path, "qt-codex")
    assert d.adapter.wake_spec["env"]["QUORUS_INSTANCE_NAME"] == "qt-codex"
    assert d.adapter.mcp_config_path.parent == tmp_path


def test_busy_agent_bids_low_on_open_work(tmp_path: Path) -> None:
    d = _daemon(tmp_path)
    bids: list[float] = []

    class Relay:
        async def submit_bid(self, **kw: Any) -> None:
            bids.append(kw["bid"])

        async def claim(self, **kw: Any) -> dict[str, Any]:
            return {"claimed": True, "winner": "someone-else"}

        async def post_social_defer(self, **kw: Any) -> None:
            return None

    env = {"from_name": "arav", "room": "r", "message_id": "m1",
           "content": "@open add a docs page", "message_type": "chat"}

    async def go() -> None:
        await d._wake_lock.acquire()  # simulate a wake in progress
        try:
            await d.handle_room_message(Relay(), env)
        finally:
            d._wake_lock.release()

    asyncio.run(go())
    assert bids and bids[0] < 0.3  # an idle teammate (>=0.3) must outbid us


def test_sse_dispatch_does_not_block_on_handler(tmp_path: Path) -> None:
    d = _daemon(tmp_path)
    gate = asyncio.Event()
    started: list[str] = []

    async def slow(relay: Any, data: dict[str, Any]) -> bool:
        started.append(data["id"])
        await gate.wait()
        return True

    d.handle_room_message = slow  # type: ignore[method-assign]

    async def go() -> None:
        for i in ("a", "b"):
            await asyncio.wait_for(
                d._dispatch_event(None, "message", {"id": i, "content": "x"}, background=True),
                timeout=0.5,
            )
        await asyncio.sleep(0)
        assert started == ["a", "b"]  # both running; reader never blocked
        gate.set()
        await asyncio.gather(*d._bg_tasks)

    asyncio.run(go())


@pytest.mark.parametrize("posted,reply,same", [
    ("✅ fixed add() in 31a3ce0", "✅ fixed add(); tests pass", True),
    ("plan: fix add()", "Fixed add() in 31a3ce0", False),
    ("Fixed add() in 31a3ce0", "Fixed  add() in 31a3ce0", True),
])
def test_same_result_dedupe(posted: str, reply: str, same: bool) -> None:
    assert reflexd._same_result(posted, reply) is same


def test_open_task_naming_a_reviewer_stays_open_work() -> None:
    msg = ("@open build a todo app with pytest tests. Commit, then ask "
           "@arav-codex to review the commit.")
    for agent in ("arav-codex", "arav-claude"):
        res = reflexd.classify_message(content=msg, sender="arav", self_name=agent)
        assert res.kind == "open_todo", agent
    bids = {
        h: reflexd.compute_bid_v2(
            kind="open_todo", role=None, description=msg,
            capabilities=reflexd.capabilities_for(f"arav-{h}"), recency_seconds=0.0,
        )[0]
        for h in ("claude", "codex")
    }
    assert bids["claude"] > bids["codex"]  # "tests" routes the build to claude


def test_wake_env_strips_daemon_secret_and_binds_agent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live: the daemon's API_KEY (a legacy relay secret) leaked into the
    # agent, so `quorus say` and the MCP server 401'd "Invalid API key format".
    monkeypatch.setenv("API_KEY", "daemon-secret")
    monkeypatch.setenv("RELAY_SECRET", "daemon-secret")
    d = wake.write_agent_config_dir(
        tmp_path / "a", relay_url="http://r", api_key="s3", participant="x-claude",
        legacy=True,
    )
    env = wake.wake_env(None, d)
    assert "API_KEY" not in env and "RELAY_SECRET" not in env
    assert env["QUORUS_CONFIG_DIR"] == str(d)
    prof = json.loads((d / "profiles" / "x-claude.json").read_text())
    assert prof == {"instance_name": "x-claude", "relay_url": "http://r", "relay_secret": "s3"}
    assert json.loads((d / "config.json").read_text())["current"] == "x-claude"
    assert (d / "profiles" / "x-claude.json").stat().st_mode & 0o777 == 0o600


def test_handled_ids_survive_restart(tmp_path: Path) -> None:
    d1 = _daemon(tmp_path)

    async def first() -> None:
        async def handler(relay: Any, data: dict[str, Any]) -> bool:
            return True
        d1.handle_room_message = handler  # type: ignore[method-assign]
        await d1._dispatch_event(None, "message", {"message_id": "m-1", "content": "x"})

    asyncio.run(first())
    d2 = _daemon(tmp_path)  # "restart": fresh process state, same runtime dir
    seen: list[str] = []

    async def second() -> None:
        async def handler(relay: Any, data: dict[str, Any]) -> bool:
            seen.append(data["message_id"])
            return True
        d2.handle_room_message = handler  # type: ignore[method-assign]
        await d2._dispatch_event(None, "message", {"message_id": "m-1", "content": "x"})
        await d2._dispatch_event(None, "message", {"message_id": "m-2", "content": "y"})

    asyncio.run(second())
    assert seen == ["m-2"]  # m-1 was handled before the restart


def test_agent_plan_line_naming_reviewer_does_not_wake_them() -> None:
    plan = "plan: add count command + tests; commit, then @arav-codex review."
    res = reflexd.classify_message(content=plan, sender="arav-claude", self_name="arav-codex")
    assert res.action == "IGNORE"
    # The real handoff (a result line) still wakes the reviewer.
    done = "✅ 1a2b3c count command, 9 tests pass. @arav-codex please review 1a2b3c."
    res = reflexd.classify_message(content=done, sender="arav-claude", self_name="arav-codex")
    assert res.action == "RESPOND"
    # A human typing "plan: ... @arav-codex" is still a request.
    res = reflexd.classify_message(content="plan: @arav-codex do x", sender="arav",
                                   self_name="arav-codex")
    assert res.action == "RESPOND"
