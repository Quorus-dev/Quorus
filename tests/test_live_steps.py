"""Live steps on the working line: "@a-codex editing relay.py 40s" (2026-10-17).

The working line showed that an agent was alive, not what it was doing. Every
harness streams its tool calls as JSON while it works; the daemon turns them
into short phrases and sends them with its busy presence. The stream lines
below are trimmed from real runs of each CLI on the same task.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from quorus_tui import working

from quorus.dashboard import DASHBOARD_JS
from quorus.relay import _reset_state, app

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
steps = reflexd.reflexd_steps

P = "/work/repo/calc.py"
CLAUDE = [
    {"type": "system", "subtype": "init", "session_id": "s1"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": P}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "def add"}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": P, "old_string": "-"}}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {
        "command": "python3 -m pytest -q", "description": "Run tests"}}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed."}]}},
    {"type": "result", "subtype": "success", "result": "Fixed add().", "session_id": "s1"},
]
CODEX = [
    {"type": "thread.started", "thread_id": "t1"},
    {"type": "item.started", "item": {"type": "command_execution",
                                      "command": "/bin/zsh -lc 'cat calc.py'"}},
    {"type": "item.started", "item": {"type": "file_change",
                                      "changes": [{"path": P, "kind": "update"}]}},
    {"type": "item.started", "item": {"type": "command_execution",
                                      "command": "/bin/zsh -lc 'pnpm test --maxWorkers=2'"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "Fixed."}},
]
OPENCODE = [
    {"type": "tool_use", "part": {"type": "tool", "tool": "read",
                                  "state": {"input": {"path": P}}}},
    {"type": "tool_use", "part": {"type": "tool", "tool": "edit",
                                  "state": {"input": {"path": P}}}},
    {"type": "tool_use", "part": {"type": "tool", "tool": "shell",
                                  "state": {"input": {"command": "git commit -qm fix"}}}},
    {"type": "text", "part": {"messageID": "m1", "text": "Fixed."}},
]
GEMINI = [
    {"type": "init", "session_id": "g1", "model": "gemini-3.5-flash-lite"},
    {"type": "tool_use", "tool_name": "update_topic", "parameters": {"title": "Fix"}},
    {"type": "tool_use", "tool_name": "read_file", "parameters": {"file_path": P}},
    {"type": "message", "role": "assistant", "content": "Let me fix it.", "delta": True},
    {"type": "tool_use", "tool_name": "replace", "parameters": {"file_path": P}},
    {"type": "tool_use", "tool_name": "run_shell_command",
     "parameters": {"command": "pytest -q", "dir_path": "/work/repo"}},
    {"type": "message", "role": "assistant", "content": "Fixed ", "delta": True},
    {"type": "message", "role": "assistant", "content": "add().", "delta": True},
    {"type": "result", "status": "success", "stats": {}},
]


def _steps(events: list[dict[str, Any]]) -> list[str]:
    return [s for s in (steps.step_of(json.dumps(e)) for e in events) if s]


@pytest.mark.parametrize("events,expected", [
    (CLAUDE, ["reading calc.py", "editing calc.py", "running tests"]),
    (CODEX, ["reading calc.py", "editing calc.py", "running tests"]),
    (OPENCODE, ["reading calc.py", "editing calc.py", "committing"]),
    (GEMINI, ["reading calc.py", "editing calc.py", "running tests"]),
])
def test_every_harness_stream_gives_the_same_steps(events, expected) -> None:
    assert _steps(events) == expected


def test_shell_steps_never_show_arguments() -> None:
    step = steps.shell_step("API_TOKEN=sk-live-123 curl -H 'Authorization: Bearer abc' https://x")
    assert step == "running curl"
    assert "sk-live" not in step and "abc" not in step
    # Compound commands name the real command (live run showed "running ls;").
    assert steps.shell_step("ls; cat calc.py") == "looking around the repo"
    assert steps.shell_step("cd /repo && make build") == "running make"
    assert steps.shell_step("/bin/zsh -lc 'cd app && cat calc.py'") == "reading calc.py"
    assert steps.step_of("not json") is None
    assert steps.step_of('{"type": "assistant", "message": {"content": "x"}}') is None


def test_claude_and_gemini_stream_replies_parse() -> None:
    out = "\n".join(json.dumps(e) for e in CLAUDE)
    assert reflexd._parse_claude_json(out) == ("Fixed add().", "s1")
    # The single-envelope format still parses (older wakes, test fakes).
    assert reflexd._parse_claude_json('{"result": "hi", "session_id": "s2"}') == ("hi", "s2")
    out = "\n".join(json.dumps(e) for e in GEMINI)
    # Only the text after the last tool call is the reply.
    assert reflexd.reflexd_models.parse_gemini_json(out) == ("Fixed add().", "g1", None)


def test_claude_and_gemini_now_stream() -> None:
    argv = reflexd.build_claude_argv("hi")
    assert argv[:5] == ["claude", "--print", "--output-format", "stream-json", "--verbose"]
    flags = reflexd.reflexd_models.gemini_flags(mode="autonomous")
    assert flags[flags.index("--output-format") + 1] == "stream-json"


def test_lines_reach_the_handler_while_the_process_runs() -> None:
    """A real subprocess: each line arrives before the process exits, an
    oversized line (a tool result) is skipped, and stdout is kept whole."""
    big = "x" * (reflexd.STEP_LINE_MAX + 10)
    script = ("import sys, time\n"
              "print('one', flush=True); time.sleep(0.3)\n"
              f"print('{big}', flush=True)\n"
              "print('two', flush=True); time.sleep(0.3)\n"
              "sys.stderr.write('err')\n")
    seen: list[tuple[str, bool]] = []

    async def run() -> tuple[bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        return await reflexd._collect(
            proc, lambda raw: seen.append((raw.decode()[:10], proc.returncode is None)))

    out, err = asyncio.run(run())
    assert seen == [("one", True), ("two", True)]
    assert out.decode().split("\n")[1] == big and err == b"err"


class _Relay:
    def __init__(self) -> None:
        self.beats: list[dict[str, str]] = []

    async def post_heartbeat(self, **kw: str) -> None:
        self.beats.append(kw)


def test_daemon_sends_the_latest_step_with_busy_presence(monkeypatch) -> None:
    monkeypatch.setattr(reflexd, "STEP_POST_MIN_S", 0.05)
    cfg = reflexd.ReflexdConfig(relay_url="http://r", api_key="k",
                                participant_name="arav-codex", spawn_enabled=False)
    d = reflexd.Reflexd(cfg)
    assert d.adapter.on_step == d._on_step
    relay = _Relay()

    async def wake(*_a: Any, **_k: Any) -> None:
        d._on_step("reading calc.py")
        await asyncio.sleep(0.01)
        for s in ("editing calc.py", "editing calc.py", "running tests"):
            d._on_step(s)  # a burst is throttled; its last step always lands
        await asyncio.sleep(0.3)

    d._wake_and_reply_inner = wake  # type: ignore[method-assign]
    asyncio.run(d._wake_and_reply(relay, {"room": "build"}))
    acts = [b.get("activity") for b in relay.beats]
    assert relay.beats[0] == {"participant": "arav-codex", "status": "busy", "room": "build"}
    assert acts[1] == "reading calc.py" and acts[-2] == "running tests"
    assert len(relay.beats) <= 4
    # Done: back to active, no stale step.
    assert relay.beats[-1] == {"participant": "arav-codex", "status": "active", "room": ""}
    d._on_step("editing x.py")  # a step after the wake is ignored
    assert d._activity == ""


@pytest.fixture
async def client():
    _reset_state()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    _reset_state()


async def test_relay_shows_the_step_only_while_busy(client: AsyncClient) -> None:
    h = {"Authorization": "Bearer test-secret"}
    await client.post("/heartbeat", headers=h, json={
        "instance_name": "a-codex", "status": "busy", "room": "build",
        "activity": "editing relay.py\x1b[31m" + "y" * 200})
    (p,) = [x for x in (await client.get("/presence", headers=h)).json()
            if x["name"] == "a-codex"]
    assert p["activity"].startswith("editing relay.py[31m")  # control chars gone
    assert len(p["activity"]) <= 80
    await client.post("/heartbeat", headers=h, json={
        "instance_name": "a-codex", "status": "active", "activity": "stale"})
    (p,) = [x for x in (await client.get("/presence", headers=h)).json()
            if x["name"] == "a-codex"]
    assert p["activity"] == ""


def test_tui_and_web_show_the_step() -> None:
    presence = [{"name": "a-codex", "online": True, "status": "busy", "room": "build",
                 "activity": "editing relay.py"},
                {"name": "a-claude", "online": True, "status": "busy", "room": "build"}]
    t = working.WorkingTracker()
    t.update(working.busy_in_room(presence, "build"), now=0,
             activity=working.activity_in_room(presence, "build"))
    line = working.render_working_line(t.snapshot(), now=75, activity=t.activity()).plain
    assert "@a-codex editing relay.py 1m 15s" in line
    assert "@a-claude working 1m 15s" in line
    assert 'p.activity || "working"' in DASHBOARD_JS
