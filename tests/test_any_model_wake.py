"""Any-model agents (gemini, opencode) get the same wake as Claude and Codex.

Before 2026-10-16 gemini and opencode were woken bare: no Quorus room tools,
no room permission mode, no model choice, no session resume, and opencode
wrote files into the daemon's $PWD (`/` under launchd) instead of the repo.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
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
    settings = json.loads(Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]).read_text())
    quorus = settings["mcpServers"]["quorus"]
    assert quorus["trust"] is True and quorus["env"]["QUORUS_INSTANCE_NAME"] == "a-opencode"


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
