"""Any-model agents (gemini, opencode) get the same wake as Claude and Codex.

Before 2026-10-16 gemini and opencode were woken bare: no Quorus room tools,
no room permission mode, no model choice, no session resume, and opencode
wrote files into the daemon's $PWD (`/` under launchd) instead of the repo.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("reflexd", _ROOT / "scripts" / "reflexd.py")
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules["reflexd"] = reflexd
_spec.loader.exec_module(reflexd)
models = reflexd.reflexd_models
wake = reflexd.reflexd_wake

# Real `opencode run --format json` events (opencode 2.0.30), trimmed.
OPENCODE_OUT = "\n".join(json.dumps(e) for e in (
    {"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}},
    {"type": "text", "sessionID": "ses_1", "part": {"messageID": "m1", "text": "Posting plan"}},
    {"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool"}},
    {"type": "text", "sessionID": "ses_1", "part": {"messageID": "m2", "text": "Wrote "}},
    {"type": "text", "sessionID": "ses_1", "part": {"messageID": "m2", "text": "hello.txt"}},
))


class _Proc:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self._out, self.returncode = stdout.encode(), returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, b""

    def kill(self) -> None:  # pragma: no cover
        pass


@pytest.fixture
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("REFLEXD_STUB_REPLY", raising=False)
    a = reflexd.HeadlessAdapter(timeout_s=5)
    a.wake_spec = wake.quorus_mcp_spec(relay_url="http://r", api_key="agent-key",
                                       participant="a-opencode", legacy=True,
                                       config_dir=tmp_path / "cfg")
    a.mcp_config_path = tmp_path / "wake-mcp-a-opencode.json"
    a.agent_config_dir = tmp_path / "cfg"
    monkeypatch.setattr(reflexd.shutil, "which", lambda b: f"/bin/{b}")

    async def ollama_up(*_a: Any, **_k: Any) -> None:  # no real Ollama in tests
        return None
    monkeypatch.setattr(reflexd, "ollama_problem", ollama_up)
    return a


def _capture(monkeypatch: pytest.MonkeyPatch, outputs: list[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_exec(*argv: str, **kw: Any) -> _Proc:
        calls.append({"argv": list(argv), **kw})
        return _Proc(outputs[min(len(calls), len(outputs)) - 1])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def test_opencode_reply_is_the_last_message() -> None:
    reply, sid, err = models.parse_opencode_stream(OPENCODE_OUT)
    assert (reply, sid, err) == ("Wrote hello.txt", "ses_1", None)
    err_out = json.dumps({"type": "error", "sessionID": "s", "error": {
        "name": "APIError", "data": {"message": "model not found"}}})
    assert models.parse_opencode_stream(err_out) == ("", "s", "model not found")
    assert models.parse_opencode_stream("plain text\n") == ("plain text", None, None)


def test_opencode_wake_gets_room_tools_mode_and_model(
    adapter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter.model = "ollama/qwen3:4b"
    calls = _capture(monkeypatch, [OPENCODE_OUT])
    seen: list[str] = []
    repo = tmp_path / "repo"
    reply = asyncio.run(adapter.run("opencode", context="ctx", cwd=repo,
                                    on_session=seen.append, mode="autonomous"))
    assert reply == "Wrote hello.txt" and seen == ["ses_1"]
    argv, env = calls[0]["argv"], calls[0]["env"]
    assert argv[-2:] == ["--", "ctx"] and "--standalone" in argv
    assert argv[argv.index("--model") + 1] == "ollama/qwen3:4b"
    # Files land in the repo, not in the daemon's $PWD.
    assert env["PWD"] == str(repo) and calls[0]["cwd"] == str(repo)
    # Inline config outranks a project's own opencode.json.
    cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    mcp = cfg["mcp"]["quorus"]
    # The human's secret in ~/.config/opencode must not merge in under ours,
    # and the agent's own secret stays out of the config: blank → profile.
    assert mcp["environment"]["QUORUS_RELAY_SECRET"] == ""
    assert mcp["environment"]["QUORUS_CONFIG_DIR"]
    assert "agent-key" not in env["OPENCODE_CONFIG_CONTENT"]
    assert mcp["command"][0] == sys.executable and "quorus_mcp.server" in mcp["command"]
    assert mcp["environment"]["QUORUS_INSTANCE_NAME"] == "a-opencode"
    assert mcp["environment"]["SSE_ENABLED"] == "false"
    assert cfg["permission"]["edit"] == "allow" and cfg["permission"]["bash"] == "allow"
    assert cfg["provider"]["ollama"]["options"]["baseURL"].startswith("http://127.0.0.1")
    assert "agent-key" not in " ".join(argv)  # secrets never in argv


def test_opencode_manual_room_is_read_only() -> None:
    cfg = models.opencode_config(None, mode="manual")
    assert cfg["permission"] == {"edit": "deny", "bash": "deny", "webfetch": "deny"}
    # opencode 2.x ignores `permission`: a manual room still wrote files.
    denied = {r["action"] for r in cfg["permissions"] if r["effect"] == "deny"}
    assert {"edit", "write", "bash", "shell"} <= denied
    default = models.opencode_config(None)
    assert "permission" not in default and "permissions" not in default  # user's own


def test_opencode_resumes_room_session_and_retries_fresh(
    adapter: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = json.dumps({"type": "error", "error": {"data": {"message": "session not found"}}})
    calls = _capture(monkeypatch, [bad, OPENCODE_OUT])
    reply = asyncio.run(adapter.run("opencode", context="ctx", resume="ses_old"))
    assert reply == "Wrote hello.txt"
    assert calls[0]["argv"][calls[0]["argv"].index("--session") + 1] == "ses_old"
    assert "--session" not in calls[1]["argv"]


def test_opencode_vendor_error_reaches_the_room(
    adapter: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = json.dumps({"type": "error", "error": {"data": {
        "message": "OpenCode 1.18.0 or newer is required to use the free tier"}}})
    _capture(monkeypatch, [bad])
    reply = asyncio.run(adapter.run("opencode", context="ctx"))
    assert reply.startswith("⚠ opencode:") and "1.18.0" in reply


def test_gemini_wake_gets_room_tools_and_approval_mode(
    adapter: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter.model = "gemini-2.5-pro"
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    out = 'MCP issues detected.\n{"response": "done", "session_id": "g1", "stats": {}}'
    calls = _capture(monkeypatch, [out])
    seen: list[str] = []
    reply = asyncio.run(adapter.run("gemini", context="-x ctx", on_session=seen.append,
                                    mode="autonomous"))
    assert reply == "done" and seen == ["g1"]
    argv, env = calls[0]["argv"], calls[0]["env"]
    assert argv[-1] == "--prompt=-x ctx"  # leading dash can't become a flag
    assert argv[argv.index("--approval-mode") + 1] == "yolo"
    assert argv[argv.index("--model") + 1] == "gemini-2.5-pro"
    assert "--skip-trust" in argv
    # The agent's own Gemini home: the user's ~/.gemini (retired Google
    # login, stale hooks) is never read.
    settings_path = Path(env["GEMINI_CLI_HOME"]) / ".gemini" / "settings.json"
    assert stat.S_IMODE(os.stat(settings_path).st_mode) == 0o600
    settings = json.loads(settings_path.read_text())
    quorus = settings["mcpServers"]["quorus"]
    assert quorus["trust"] is True and quorus["env"]["QUORUS_INSTANCE_NAME"] == "a-opencode"
    assert settings["security"]["auth"]["selectedType"] == "gemini-api-key"
    assert env["GEMINI_API_KEY"] == "gk-test" and "gk-test" not in " ".join(argv)


def test_gemini_modes_and_errors() -> None:
    assert "--approval-mode" not in models.gemini_flags()
    plan = models.gemini_flags(mode="manual")
    assert plan[plan.index("--approval-mode") + 1] == "plan"
    err = '{"error": {"type": "AuthError", "message": "API key not valid"}}'
    assert models.parse_gemini_json(err) == ("", None, "API key not valid")
    assert models.parse_gemini_json("not json") == ("not json", None, None)


def test_model_reaches_claude_and_codex(adapter: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter.model = "sonnet"
    calls = _capture(monkeypatch, ['{"result": "ok", "session_id": "c"}'])
    asyncio.run(adapter.run("claude", context="ctx"))
    assert calls[0]["argv"][calls[0]["argv"].index("--model") + 1] == "sonnet"
    adapter.model = "gpt-5.5"
    calls = _capture(monkeypatch, [""])
    asyncio.run(adapter.run("codex", context="ctx"))
    assert 'model="gpt-5.5"' in calls[0]["argv"]


def test_model_comes_from_daemon_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFLEXD_MODEL", "ollama/qwen3:4b")
    assert reflexd.HeadlessAdapter().model == "ollama/qwen3:4b"
    monkeypatch.delenv("REFLEXD_MODEL")
    assert reflexd.HeadlessAdapter().model is None


def test_labelled_agents_route_by_their_tool() -> None:
    # `--name backend` → arav-backend-claude: still a Claude agent, still an
    # agent (so it can be woken and never mistaken for a human).
    for name, harness in (("arav-backend-claude", "claude"),
                          ("arav-review-codex", "codex"),
                          ("arav-local-opencode", "opencode"),
                          ("arav-gemini", "gemini")):
        assert reflexd.detect_harness(name) == harness
        assert reflexd.is_agent_participant(name)


def test_failed_start_is_retried_once(adapter: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # Two opencode agents woken by one message on a fresh install race its
    # first-run setup; one exits 1 ("Standalone server exited before
    # reporting readiness"). The room got "harness errored" instead of a reply.
    outs = [("", 1), (OPENCODE_OUT, 0)]
    calls: list[list[str]] = []

    async def fake_exec(*argv: str, **_kw: Any) -> _Proc:
        calls.append(list(argv))
        out, code = outs[len(calls) - 1]
        return _Proc(out, code)

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(reflexd.asyncio, "sleep", no_sleep)
    assert asyncio.run(adapter.run("opencode", context="ctx")) == "Wrote hello.txt"
    assert len(calls) == 2


def test_opencode_v1_runs_without_standalone(
    adapter: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `npm i -g opencode-ai` still installs 1.x, which rejects --standalone:
    # every wake posted "harness errored" (Linux run, 2026-10-16).
    assert models.is_opencode_v1("1.19.3") and not models.is_opencode_v1("opencode v2.0.30")
    assert not models.is_opencode_v1(None)
    adapter.opencode_v1 = True
    calls = _capture(monkeypatch, [OPENCODE_OUT])
    assert asyncio.run(adapter.run("opencode", context="ctx")) == "Wrote hello.txt"
    assert "--standalone" not in calls[0]["argv"]


def test_gemini_key_is_read_from_the_users_gemini_env(tmp_path: Path) -> None:
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / ".env").write_text("OTHER=1\nGEMINI_API_KEY='AQ.abc'\n")
    assert models.gemini_api_key({"HOME": str(tmp_path)}) == "AQ.abc"
    assert models.gemini_api_key({"HOME": str(tmp_path), "GEMINI_API_KEY": "e"}) == "e"
    assert models.gemini_api_key({"HOME": str(tmp_path / "none")}) is None
    assert "security" not in models.gemini_settings(None)  # no key: user's own sign-in


def test_retired_gemini_model_tells_the_room_to_update() -> None:
    # gemini-cli 0.40 defaulted to a model Google retired for new users.
    err = ("ModelNotFoundError: This model models/gemini-2.5-flash-lite is no longer "
           "available to new users.")
    msg = reflexd.diagnose_harness_failure("gemini", err)
    assert msg and "npm i -g @google/gemini-cli@latest" in msg


# ── opencode free-model failover (2026-10-17) ───────────────────────────


def _rotating_adapter(limited: set[str | None]) -> tuple[Any, list[str | None]]:
    import time as _time

    adapter = reflexd.HeadlessAdapter()
    tried: list[str | None] = []

    async def once(*_a: Any, **_k: Any) -> str:
        tried.append(adapter.model)
        if adapter.model in limited:
            adapter.limited_until = _time.time() + 1800
            return "⚠ opencode: the vendor rate-limited this account; the wake was dropped."
        return f"fixed it on {adapter.model}"

    adapter._opencode_once = once  # type: ignore[method-assign]
    return adapter, tried


def test_a_limited_free_model_moves_to_the_next_free_one() -> None:
    free = reflexd.reflexd_models.OPENCODE_FREE_MODELS
    adapter, tried = _rotating_adapter({free[0]})
    adapter.model = free[0]
    out = asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert out == f"fixed it on {free[1]}" and tried == [free[0], free[1]]
    assert adapter.model == free[1] and adapter.limited_until == 0.0  # sticks with what works


def test_every_free_model_limited_cools_down_on_the_original() -> None:
    free = reflexd.reflexd_models.OPENCODE_FREE_MODELS
    adapter, tried = _rotating_adapter({None, *free})
    out = asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert "rate-limited" in out and tried == [None, *free]
    assert adapter.model is None and adapter.limited_until > 0


def test_rotation_reaches_a_live_free_model_the_tuple_never_had(monkeypatch) -> None:
    """Release check: every tuple model was limited while nemotron-3.5-lightning
    answered. The rotation now walks the live `opencode models` catalog."""
    import subprocess

    from quorus.runtime import opencode_free

    free = reflexd.reflexd_models.OPENCODE_FREE_MODELS
    catalog = "\n".join([*free, "opencode/nemotron-3.5-lightning-free"])
    monkeypatch.setattr(opencode_free, "_default_run", lambda argv, **_k:
                        subprocess.CompletedProcess(argv, 0, stdout=catalog, stderr=""))
    adapter, tried = _rotating_adapter({None, *free})
    out = asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert out == "fixed it on opencode/nemotron-3.5-lightning-free"
    assert tried == [None, *free, "opencode/nemotron-3.5-lightning-free"]


def test_a_paid_model_is_never_swapped() -> None:
    adapter, tried = _rotating_adapter({"openrouter/qwen/qwen3-coder"})
    adapter.model = "openrouter/qwen/qwen3-coder"
    asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert tried == ["openrouter/qwen/qwen3-coder"]


def test_a_failed_turns_leftover_work_gets_a_real_commit_message() -> None:
    """Gemini's timed-out work was committed as "[reflexd] harness timed out"."""
    msg = reflexd.reflexd_wake.commit_message_from
    assert msg("[reflexd] harness timed out", "a-gemini") == \
        "chore: unfinished work from a cut-off turn"
    assert msg("⚠ opencode: rate-limited", "a-opencode").startswith("chore: unfinished")
    assert msg("✅ fix: add() in calc.py", "a-codex") == "fix: add() in calc.py"
    # A chat-style reply ("@arav-claude I have created...") still gets a
    # conventional subject (BetU commits, 2026-10-17).
    assert msg("@arav-claude I have created docs/x.md", "a-gemini").startswith("chore: ")


def test_timeout_notice_says_what_happened() -> None:
    assert reflexd._timed_out_line().startswith("⚠ ran out of time")
    # A cut-off turn never publishes (test_unfinished_wakes.py), so it never says so.
    assert "published" not in reflexd._timed_out_line()


def test_failover_goes_back_to_the_agents_own_model(monkeypatch) -> None:
    """It moved to the slowest free model at 09:29 and stayed there, timing
    out two 30-minute turns (2026-10-17)."""
    free = reflexd.reflexd_models.OPENCODE_FREE_MODELS
    adapter, tried = _rotating_adapter({free[0]})
    adapter.model = free[0]
    asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert adapter.model == free[1]
    import time as _time

    real = _time.time
    monkeypatch.setattr(reflexd.time, "time",
                        lambda: real() + reflexd.reflexd_models.ROTATE_BACK_S + 1)
    tried.clear()

    async def ok(*_a: Any, **_k: Any) -> str:  # the limit has cleared
        tried.append(adapter.model)
        return "done"

    adapter._opencode_once = ok  # type: ignore[method-assign]
    asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert tried == [free[0]] and adapter.model == free[0]  # back on its own model


def test_nemotron_is_not_a_fallback() -> None:
    assert not any("nemotron" in m for m in reflexd.reflexd_models.OPENCODE_FREE_MODELS)


def test_woken_claude_keeps_skills_but_runs_no_hooks(tmp_path) -> None:
    """Hooks ran on every wake and slowed the laptop; the first fix also hid
    274 of 293 skills. Now: the owner's settings load, hooks don't run."""
    import json as _json

    flags = reflexd.reflexd_wake.claude_isolation_flags(tmp_path / "agent-settings.json")
    assert flags == ["--settings", str(tmp_path / "agent-settings.json")]
    assert "--setting-sources" not in flags  # user settings (skills, plugins) still load
    assert _json.loads((tmp_path / "agent-settings.json").read_text()) == {
        "disableAllHooks": True}


# ── the vendor's own error instead of "exited 1 with no known cause" ────
# Real output captured 2026-10-17 (opencode 2.0.30, codex, claude).

OPENCODE_NO_ROUTE = ('{"type":"error","timestamp":1792278500185,"sessionID":"ses_x",'
                     '"error":{"type":"provider.no-route",'
                     '"message":"Model unavailable: xai/grok-code-fast-1"}}')
CODEX_BAD_MODEL = "\n".join([
    '{"type":"thread.started","thread_id":"01a1"}',
    '{"type":"turn.started"}',
    '{"type":"turn.failed","error":{"message":"{\\"type\\":\\"error\\",\\"status\\":400,'
    '\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The '
    '\'no-such-model-xyz\' model is not supported when using Codex with a ChatGPT '
    'account.\\"}}"}}'])
CLAUDE_BAD_MODEL = ('{"type":"result","is_error":true,"terminal_reason":"api_error",'
                    '"result":"There\'s an issue with the selected model (no-such-model-xyz). '
                    'It may not exist or you may not have access to it."}')


def test_opencode_no_route_names_the_missing_key() -> None:
    line = reflexd.vendor_error("opencode", OPENCODE_NO_ROUTE, "")
    assert line.startswith("⚠ opencode: Model unavailable: xai/grok-code-fast-1.")
    assert "opencode auth login" in line and "XAI_API_KEY" in line
    _reply, _sid, err = reflexd.reflexd_models.parse_opencode_stream(OPENCODE_NO_ROUTE)
    assert err == "Model unavailable: xai/grok-code-fast-1"  # exit-0 path too, not "error"


def test_codex_and_claude_bad_models_say_so() -> None:
    codex = reflexd.vendor_error("codex", CODEX_BAD_MODEL, "Reading additional input")
    assert "model is not supported when using Codex with a ChatGPT account" in codex
    assert "Check the model name" in codex and "{" not in codex  # unwrapped, not raw JSON
    claude = reflexd.vendor_error("claude", CLAUDE_BAD_MODEL, "")
    assert claude.startswith("⚠ claude: There's an issue with the selected model")
    reply, _sid = reflexd._parse_claude_json(CLAUDE_BAD_MODEL)
    assert reply.startswith("⚠ claude: There's an issue")  # exit 0 + is_error


def test_stderr_fallback_is_short_and_clean() -> None:
    stderr = "loading…\n\x1b[31mError: model gemini-9 not found\x1b[0m\n"
    line = reflexd.vendor_error("gemini", "", stderr)
    assert line == ("⚠ gemini: Error: model gemini-9 not found. Check the model name and "
                    "that this CLI can use it.")
    long = reflexd.vendor_error("gemini", "", "Error: model " + "y" * 30 + " not found" + "z" * 400)
    assert len(long) < 320


def test_errors_that_arent_about_the_model_stay_private() -> None:
    """Harness output can echo secrets or tool inputs: only bad-model errors are quoted."""
    assert reflexd.vendor_error("gemini", "", "Error: private-token abc123 rejected") is None
    leak = '{"type":"error","error":{"message":"private-token: unexplained error"}}'
    assert reflexd.vendor_error("opencode", leak, "") is None


# ── Ollama precheck (release check: 182s wait for "no known cause") ─────


def test_ollama_not_running_is_said_at_once(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:1")  # nothing listens there
    msg = asyncio.run(reflexd.ollama_problem("qwen3:4b", timeout=1))
    assert msg == "Ollama isn't running: start it with `OLLAMA_CONTEXT_LENGTH=32768 ollama serve`."


def test_ollama_without_the_model_says_pull() -> None:
    import httpx

    def tags(_req):
        return httpx.Response(200, json={"models": [{"name": "llama3:latest"}]})
    t = httpx.MockTransport(tags)
    assert asyncio.run(reflexd.ollama_problem("qwen3:4b", transport=t)) == \
        "Ollama doesn't have qwen3:4b: `ollama pull qwen3:4b`."
    assert asyncio.run(reflexd.ollama_problem("llama3", transport=t)) is None
    # A fresh Ollama with no models pulled still needs the pull hint (codex).
    empty = httpx.MockTransport(lambda _r: httpx.Response(200, json={"models": []}))
    assert asyncio.run(reflexd.ollama_problem("qwen3:4b", transport=empty)) == \
        "Ollama doesn't have qwen3:4b: `ollama pull qwen3:4b`."


def test_an_ollama_wake_stops_before_opencode_when_ollama_is_down(monkeypatch) -> None:
    adapter, tried = _rotating_adapter(set())
    adapter.model = "ollama/qwen3:4b"

    async def down(model, **_k):
        return "Ollama isn't running: start it with `OLLAMA_CONTEXT_LENGTH=32768 ollama serve`."
    monkeypatch.setattr(reflexd, "ollama_problem", down)
    out = asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert out.startswith("⚠ opencode: Ollama isn't running") and tried == []


# ── slow free models: first-output watchdog (fix 9, release check) ──────


def _fake_harness(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "fake_opencode.py"
    script.write_text("import sys, time\n" + body)
    return [sys.executable, str(script)]


def test_a_harness_silent_past_its_deadline_is_killed_fast(tmp_path: Path) -> None:
    import time as _t

    argv = _fake_harness(tmp_path, "time.sleep(30)\nprint('{\"type\":\"step_start\"}')\n")
    started = _t.monotonic()
    out = asyncio.run(reflexd.HeadlessAdapter(timeout_s=60)._run_subprocess(
        argv, parser=lambda o: o, first_output_s=0.5))
    assert isinstance(out, reflexd.SlowStart) and "sent nothing" in out
    assert _t.monotonic() - started < 10  # not the 30s sleep, not the 60s turn


def test_output_before_the_deadline_runs_to_the_end(tmp_path: Path) -> None:
    argv = _fake_harness(tmp_path, "print('first', flush=True)\ntime.sleep(1.5)\nprint('done')\n")
    out = asyncio.run(reflexd.HeadlessAdapter(timeout_s=30)._run_subprocess(
        argv, parser=lambda o: o.strip(), first_output_s=0.5))
    assert out == "first\ndone"


def test_a_quick_silent_exit_is_a_failure_not_slowness(tmp_path: Path) -> None:
    argv = _fake_harness(tmp_path, "sys.exit(3)\n")
    out = asyncio.run(reflexd.HeadlessAdapter(timeout_s=30)._run_subprocess(
        argv, parser=lambda o: o, first_output_s=5))
    assert not isinstance(out, reflexd.SlowStart) and getattr(out, "exit_code", None) == 3


def test_a_slow_free_model_hands_the_turn_to_the_next_and_goes_last(monkeypatch) -> None:
    from quorus.runtime import opencode_free

    free = reflexd.reflexd_models.OPENCODE_FREE_MODELS
    adapter = reflexd.HeadlessAdapter()
    tried: list[str | None] = []
    steps: list[str] = []
    adapter.on_step = steps.append

    async def once(*_a: Any, **_k: Any) -> str:
        tried.append(adapter.model)
        if adapter.model == free[0]:
            return reflexd.SlowStart("opencode", 60)
        return f"fixed it on {adapter.model}"

    adapter._opencode_once = once  # type: ignore[method-assign]
    adapter.model = free[0]
    out = asyncio.run(adapter._run_opencode("ctx", cwd=None, resume=None, on_session=None))
    assert out == f"fixed it on {free[1]}" and tried == [free[0], free[1]]
    assert len(steps) == 1 and "slow to start" in steps[0]
    assert opencode_free.is_slow(free[0])
    assert opencode_free.free_models(free)[-1] == free[0]  # back of the rotation


def test_paid_and_local_models_get_no_first_output_deadline(monkeypatch) -> None:
    seen: list[Any] = []
    adapter = reflexd.HeadlessAdapter()

    async def capture(*_a: Any, first_output_s: Any = None, **_k: Any) -> str:
        seen.append(first_output_s)
        return "ok"

    adapter._run_with_resume = capture  # type: ignore[method-assign]
    adapter.wake_spec = None
    monkeypatch.setattr(reflexd.reflexd_wake, "wake_env", lambda *a, **k: {})
    monkeypatch.setattr(reflexd.reflexd_models, "opencode_config", lambda *a, **k: {})
    for model in ("ollama/qwen3:4b", "openrouter/qwen/qwen3-coder", None,
                  "opencode/beam-free"):
        adapter.model = model
        asyncio.run(adapter._opencode_once("ctx", cwd=None, resume=None, on_session=None,
                                           timeout_s=None, mode="default"))
    assert seen == [None, None, reflexd.FREE_FIRST_EVENT_S, reflexd.FREE_FIRST_EVENT_S]


GEMINI_BAD_MODEL = (  # gemini-cli 0.64 stream-json, rc 1 (overseer's live check)
    '{"type":"result","status":"error","error":{"type":"unknown","message":"[API Error: '
    'models/gemini-9-ultra is not found for API version v1beta, or is not supported for '
    'generateContent. Call ModelService.ListModels to see the list of available models '
    'and their supported methods.]"}}')


def test_gemini_bad_model_says_so_without_the_tail() -> None:
    line = reflexd.vendor_error("gemini", GEMINI_BAD_MODEL, "")
    assert line.startswith("⚠ gemini: models/gemini-9-ultra is not found for API version")
    assert "ModelService" not in line and "[API Error" not in line
    assert "Check the model name" in line
    _r, _sid, err = reflexd.reflexd_models.parse_gemini_json(GEMINI_BAD_MODEL)
    assert err.startswith("models/gemini-9-ultra is not found") and "ModelService" not in err


def test_an_opencode_typo_is_pointed_at_the_model_list() -> None:
    typo = ('{"type":"error","error":{"type":"provider.no-route",'
            '"message":"Model unavailable: opencode/beem-free"}}')
    assert "`opencode models`" in reflexd.vendor_error("opencode", typo, "")


def test_gemini_reads_the_repos_agents_and_claude_files() -> None:
    """Overseer's replay: Gemini read only GEMINI.md, never learned the venv, ran
    `find /` and timed out three times. And find's steady 'Permission denied'
    output kept the 300s shell inactivity timeout from ever firing."""
    cfg = models.gemini_settings(None)
    assert cfg["context"]["fileName"] == ["GEMINI.md", "AGENTS.md", "CLAUDE.md"]
    assert cfg["tools"]["shell"]["inactivityTimeout"] == 120
    keyed = models.gemini_settings(None, api_key=True)  # the other settings stay
    assert keyed["security"]["auth"]["selectedType"] == "gemini-api-key"
    assert keyed["context"] == cfg["context"]


def test_a_timeout_says_what_the_agent_was_doing() -> None:
    line = reflexd._timed_out_line(last_step="searching the code")
    assert line == ("⚠ ran out of time while searching the code, before finishing its "
                    "reply. Mention me to continue.")
    assert reflexd._timed_out_line() == ("⚠ ran out of time before finishing its reply. "
                                         "Mention me to continue.")


def test_a_manual_or_readonly_gemini_cannot_leave_plan_mode() -> None:
    """Release sweep: in a manual room Gemini 0.64 called exit_plan_mode (it
    approves itself headless), wrote gm.txt, and Quorus published it to main."""
    for mode in ("manual", "readonly"):
        cfg = models.gemini_settings(None, mode=mode)
        assert cfg["tools"]["exclude"] == ["exit_plan_mode"]
        assert "--approval-mode" in models.gemini_flags(mode=mode)
        assert models.gemini_flags(mode=mode)[-1] == "plan"
    for mode in ("default", "autonomous"):
        assert "exclude" not in models.gemini_settings(None, mode=mode)["tools"]


def test_the_gemini_wake_writes_its_mode_into_the_settings(tmp_path: Path,
                                                           monkeypatch) -> None:
    import json as _json

    adapter = reflexd.HeadlessAdapter()
    adapter.agent_config_dir = tmp_path / "cfg"
    adapter.wake_spec = None

    async def capture(*_a: Any, **_k: Any) -> str:
        return "ok"
    adapter._run_with_resume = capture  # type: ignore[method-assign]
    monkeypatch.setattr(reflexd.reflexd_wake, "wake_env", lambda *a, **k: {})
    asyncio.run(adapter._run_gemini("ctx", cwd=None, resume=None, on_session=None,
                                    mode="manual"))
    written = _json.loads((tmp_path / "cfg" / "gemini-home" / ".gemini" /
                           "settings.json").read_text())
    assert written["tools"]["exclude"] == ["exit_plan_mode"]
